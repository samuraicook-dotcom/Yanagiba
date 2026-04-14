"""
core/sentiment.py — Lightweight Sentiment Engine

Supports two modes:
  - 'rss'    : RSS keyword scoring (zero cost, default)
  - 'gemini' : Google Gemini Flash API (optional)

Runs independently on a 30-min cron. Trading loop reads cached JSON only.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser

logger = logging.getLogger("kaizen.sentiment")

CACHE_PATH = Path("data/sentiment_cache.json")
BIAS_BULLISH = "BULLISH"
BIAS_BEARISH = "BEARISH"
BIAS_NEUTRAL = "NEUTRAL"


class SentimentEngine:
    """
    Scores news headlines for gold/silver market bias.

    Mode A (rss): Fetches RSS feeds, scores against keyword dictionaries.
    Mode B (gemini): Sends batched headlines to Gemini Flash for classification.
    """

    def __init__(self, config: dict, keywords: dict) -> None:
        """
        Args:
            config: sentiment section from settings.yaml
            keywords: full parsed keywords.yaml dict
        """
        self.config = config
        self.mode: str = config.get("mode", "rss")
        self.rss_feeds: list[str] = config.get("rss_feeds", [])
        self.lookback_hours: float = config.get("headline_lookback_hours", 2)
        self.bias_threshold: float = config.get("bias_threshold", 0.3)
        self.gemini_model: str = config.get("gemini", {}).get(
            "model", "gemini-2.0-flash"
        )
        self.gemini_batch_size: int = config.get("gemini", {}).get("batch_size", 15)

        # Parse keyword dictionaries
        self.risk_off_keywords: list[str] = [
            k.lower() for k in keywords.get("risk_off_bullish", {}).get("keywords", [])
        ]
        self.risk_on_keywords: list[str] = [
            k.lower() for k in keywords.get("risk_on_bearish", {}).get("keywords", [])
        ]
        self.amplifier_keywords: list[str] = [
            k.lower() for k in keywords.get("amplifiers", {}).get("keywords", [])
        ]
        self.risk_off_weight: float = keywords.get("risk_off_bullish", {}).get(
            "weight", 1
        )
        self.risk_on_weight: float = keywords.get("risk_on_bearish", {}).get(
            "weight", -1
        )
        self.amplifier_weight: float = keywords.get("amplifiers", {}).get("weight", 2)

    # ------------------------------------------------------------------
    # RSS Fetch
    # ------------------------------------------------------------------

    def _fetch_rss_headlines(self) -> list[dict]:
        """
        Fetch and filter headlines from all configured RSS feeds.
        Returns only headlines published within lookback_hours.
        """
        cutoff_ts = time.time() - (self.lookback_hours * 3600)
        headlines = []

        for feed_url in self.rss_feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries:
                    pub_ts = None
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        pub_ts = time.mktime(entry.published_parsed)
                    elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                        pub_ts = time.mktime(entry.updated_parsed)

                    # Accept if within window or if we can't parse time
                    if pub_ts is None or pub_ts >= cutoff_ts:
                        title = getattr(entry, "title", "")
                        summary = getattr(entry, "summary", "")
                        headlines.append(
                            {
                                "title": title,
                                "summary": summary,
                                "published_ts": pub_ts,
                                "source": feed_url,
                            }
                        )
            except Exception as exc:
                logger.warning("Failed to fetch RSS feed %s: %s", feed_url, exc)

        logger.info("Fetched %d headlines from RSS feeds", len(headlines))
        return headlines

    # ------------------------------------------------------------------
    # Keyword Scoring
    # ------------------------------------------------------------------

    def _score_headline(self, text: str) -> float:
        """
        Score a single headline text against keyword lists.
        Amplifier keywords multiply the score by amplifier_weight.

        Args:
            text: Lowercased headline or summary text

        Returns:
            Float score (positive = bullish, negative = bearish, 0 = neutral)
        """
        score = 0.0

        for kw in self.risk_off_keywords:
            if kw in text:
                score += self.risk_off_weight

        for kw in self.risk_on_keywords:
            if kw in text:
                score += self.risk_on_weight

        # Apply amplifier multiplier if any amplifier word found
        if score != 0:
            for amp in self.amplifier_keywords:
                if amp in text:
                    score *= self.amplifier_weight
                    break

        return score

    def _rss_score(self, headlines: list[dict]) -> tuple[float, list[str]]:
        """
        Score all headlines and return average score + top headlines.

        Returns:
            (avg_score, top_5_headline_titles)
        """
        if not headlines:
            return 0.0, []

        scores = []
        scored = []
        for h in headlines:
            text = (h["title"] + " " + h["summary"]).lower()
            s = self._score_headline(text)
            scores.append(s)
            scored.append((s, h["title"]))

        avg_score = sum(scores) / len(scores)

        # Top headlines by absolute score
        scored.sort(key=lambda x: abs(x[0]), reverse=True)
        top_headlines = [t for _, t in scored[:5]]

        return avg_score, top_headlines

    def _bias_from_score(self, score: float) -> str:
        """Convert numeric score to bias string."""
        if score > self.bias_threshold:
            return BIAS_BULLISH
        elif score < -self.bias_threshold:
            return BIAS_BEARISH
        return BIAS_NEUTRAL

    # ------------------------------------------------------------------
    # Gemini Mode
    # ------------------------------------------------------------------

    def _gemini_classify(self, headlines: list[dict]) -> str:
        """
        Send headlines to Gemini Flash for classification.

        Args:
            headlines: List of headline dicts

        Returns:
            'BULLISH', 'BEARISH', or 'NEUTRAL'
        """
        try:
            import google.generativeai as genai  # type: ignore

            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                logger.warning("GEMINI_API_KEY not set, falling back to RSS mode")
                return BIAS_NEUTRAL

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(self.gemini_model)

            batch = headlines[: self.gemini_batch_size]
            headlines_text = "\n".join(
                [f"- {h['title']}" for h in batch if h.get("title")]
            )

            prompt = (
                "You are a gold/silver market sentiment classifier. "
                "Given these headlines, respond with exactly one word: "
                "BULLISH, BEARISH, or NEUTRAL. "
                "Consider: geopolitical risk = bullish for gold, "
                "risk-on/strong dollar = bearish.\n\n"
                f"Headlines:\n{headlines_text}"
            )

            response = model.generate_content(prompt)
            result = response.text.strip().upper()

            if result in (BIAS_BULLISH, BIAS_BEARISH, BIAS_NEUTRAL):
                return result
            # Partial match fallback
            for bias in (BIAS_BULLISH, BIAS_BEARISH, BIAS_NEUTRAL):
                if bias in result:
                    return bias
            return BIAS_NEUTRAL

        except Exception as exc:
            logger.error("Gemini classification failed: %s", exc)
            return BIAS_NEUTRAL

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(self) -> dict:
        """
        Fetch latest headlines, score them, and write to sentiment cache.

        Returns:
            Cache dict with keys: timestamp, bias, score, headline_count, top_headlines
        """
        headlines = self._fetch_rss_headlines()

        if self.mode == "gemini":
            bias = self._gemini_classify(headlines)
            avg_score = (
                1.0
                if bias == BIAS_BULLISH
                else (-1.0 if bias == BIAS_BEARISH else 0.0)
            )
            top_headlines = [h["title"] for h in headlines[:5]]
        else:
            # Default: RSS keyword mode
            avg_score, top_headlines = self._rss_score(headlines)
            bias = self._bias_from_score(avg_score)

        cache: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bias": bias,
            "score": round(avg_score, 4),
            "headline_count": len(headlines),
            "top_headlines": top_headlines,
        }

        self._write_cache(cache)
        logger.info(
            "Sentiment updated: %s (score=%.4f, headlines=%d)",
            bias,
            avg_score,
            len(headlines),
        )
        return cache

    def read_cache(self) -> dict:
        """
        Read the latest sentiment from the cache file.
        Returns NEUTRAL if cache is missing or stale.

        Returns:
            Cache dict: {timestamp, bias, score, headline_count, top_headlines}
        """
        if not CACHE_PATH.exists():
            logger.warning("Sentiment cache not found, defaulting to NEUTRAL")
            return self._neutral_cache()

        try:
            with open(CACHE_PATH, "r") as f:
                data = json.load(f)
            return data
        except Exception as exc:
            logger.error("Failed to read sentiment cache: %s", exc)
            return self._neutral_cache()

    def _write_cache(self, data: dict) -> None:
        """Write sentiment data to cache file."""
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def _neutral_cache(self) -> dict:
        """Return a default neutral cache entry."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bias": BIAS_NEUTRAL,
            "score": 0.0,
            "headline_count": 0,
            "top_headlines": [],
        }
