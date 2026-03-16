"""Geopolitical and news sentiment tracker.

Monitors global events (wars, sanctions, regulatory actions, macro shocks) and
scores their likely impact on crypto markets. Uses free public APIs + RSS feeds.

Sentiment sources:
- Fear & Greed Index (alternative.me)
- CoinGecko trending coins + gaming sector
- CryptoPanic headlines
- RSS feeds (CoinDesk, CoinTelegraph, Decrypt, macro/central bank)
- Context-aware NLP scoring (negation, urgency, recency weighting)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

import aiohttp

logger = logging.getLogger(__name__)

# --- NLP keyword system ---
# Each entry: (phrase, base_score, weight)
# Negative score = bearish, positive = bullish
# Weight reflects how market-moving the event typically is

SCORED_PHRASES: list[tuple[str, float, float]] = [
    # Bearish — geopolitical
    ("war", -1.0, 1.5),
    ("invasion", -1.0, 1.5),
    ("missile strike", -1.0, 1.3),
    ("missile", -0.7, 1.0),
    ("sanctions", -0.8, 1.2),
    ("embargo", -0.8, 1.2),
    ("nuclear", -1.0, 1.5),
    ("conflict", -0.6, 1.0),
    ("military", -0.5, 0.8),
    ("escalation", -0.8, 1.2),
    ("attack", -0.6, 1.0),
    ("ceasefire collapse", -0.9, 1.3),
    # Bearish — regulatory
    ("ban crypto", -1.0, 1.5),
    ("crypto ban", -1.0, 1.5),
    ("regulation crackdown", -0.8, 1.3),
    ("sec charges", -0.8, 1.3),
    ("sec sues", -0.9, 1.4),
    ("sec lawsuit", -0.8, 1.3),
    ("enforcement action", -0.7, 1.1),
    ("subpoena", -0.6, 1.0),
    # Bearish — macro
    ("default", -0.8, 1.2),
    ("recession", -0.9, 1.3),
    ("rate hike", -0.7, 1.2),
    ("inflation spike", -0.8, 1.2),
    ("bank collapse", -1.0, 1.5),
    ("bank run", -1.0, 1.5),
    ("yield curve inversion", -0.5, 1.0),
    ("debt ceiling", -0.5, 0.8),
    # Bearish — crypto-specific
    ("hack", -0.8, 1.3),
    ("exploit", -0.7, 1.2),
    ("rug pull", -0.9, 1.3),
    ("insolvency", -0.9, 1.4),
    ("depeg", -0.8, 1.3),
    ("flash crash", -0.7, 1.2),
    ("liquidation", -0.5, 0.8),
    ("whale dump", -0.6, 1.0),

    # Bullish — geopolitical
    ("ceasefire", 0.8, 1.2),
    ("peace deal", 0.9, 1.3),
    ("de-escalation", 0.7, 1.1),
    ("treaty", 0.7, 1.1),
    ("truce", 0.7, 1.1),
    # Bullish — macro
    ("rate cut", 0.9, 1.4),
    ("interest rate cut", 0.9, 1.4),
    ("stimulus", 0.8, 1.3),
    ("quantitative easing", 0.9, 1.4),
    ("dovish", 0.6, 1.0),
    ("soft landing", 0.5, 0.9),
    ("jobs growth", 0.4, 0.8),
    # Bullish — crypto adoption
    ("etf approved", 1.0, 1.5),
    ("etf approval", 1.0, 1.5),
    ("etf launch", 0.9, 1.4),
    ("spot etf", 0.8, 1.3),
    ("adoption", 0.5, 0.9),
    ("institutional buy", 0.7, 1.2),
    ("reserve asset", 0.8, 1.3),
    ("legal tender", 0.8, 1.3),
    ("strategic reserve", 0.9, 1.4),
    ("bull run", 0.6, 1.0),
    ("all time high", 0.7, 1.1),
    ("ath", 0.5, 0.8),
    ("rally", 0.4, 0.8),
    ("whale accumulation", 0.6, 1.0),
    ("inflow", 0.5, 0.9),
    ("partnership", 0.4, 0.8),
]

# Negation words that flip sentiment
NEGATION_WORDS = {
    "not", "no", "never", "neither", "nor", "don't", "doesn't",
    "didn't", "won't", "wouldn't", "couldn't", "shouldn't",
    "isn't", "aren't", "wasn't", "weren't", "cannot", "can't",
    "unlikely", "fails", "failed", "rejected", "denies", "denied",
    "drops", "scraps", "abandons", "delays", "postpones", "halts",
    "reverses", "opposes", "blocks", "against",
}

# Amplifier words that increase magnitude
AMPLIFIER_WORDS = {
    "breaking": 1.5, "urgent": 1.4, "massive": 1.3, "major": 1.2,
    "critical": 1.3, "unprecedented": 1.3, "historic": 1.2,
    "emergency": 1.4, "imminent": 1.3, "confirmed": 1.2,
    "officially": 1.1, "exclusive": 1.1, "just in": 1.3,
}

# Gaming sector keywords
GAMING_BULLISH_KEYWORDS = [
    "gta 6", "gta vi", "rockstar games", "game launch", "gaming token",
    "play to earn", "p2e", "metaverse", "virtual world", "nft gaming",
    "gaming partnership", "aaa game", "blockchain gaming",
    "immutable", "gala games", "ronin network",
]

GAMING_BEARISH_KEYWORDS = [
    "game delay", "launch postponed", "gaming crash", "p2e dead",
    "gaming token dump", "metaverse dead",
]

# Geopolitical hotspots to track
GEO_WATCHLIST = [
    "russia ukraine", "israel gaza", "china taiwan",
    "iran", "north korea", "red sea shipping",
    "us china trade", "brics",
]

# --- RSS feed configuration ---
# Free feeds, no API keys needed

CRYPTO_RSS_FEEDS = [
    # Crypto-native news (fastest for crypto events)
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://thedefiant.io/feed",
    "https://www.theblock.co/rss.xml",
]

MACRO_RSS_FEEDS = [
    # Central banks & macro (rate decisions, policy shifts)
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://www.ecb.europa.eu/rss/press.html",
    "https://www.bis.org/doclist/bis_fsi_publs.rss",
]

# Max age for RSS entries (4 hours — older news is priced in)
RSS_MAX_AGE_SECONDS = 4 * 3600

# Strip HTML tags from RSS content
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text).strip()


@dataclass
class ScoredHeadline:
    """A headline with its computed sentiment score and metadata."""
    title: str
    score: float  # final weighted score
    source: str
    published: datetime | None = None
    is_breaking: bool = False


@dataclass
class SentimentReport:
    overall_score: float  # -10 to +10
    geo_score: float  # geopolitical risk score
    news_score: float  # general news sentiment
    rss_score: float = 0.0  # RSS feed sentiment
    gaming_score: float = 0.0  # gaming sector sentiment
    fear_greed_index: int | None = None  # 0-100
    key_events: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    gaming_catalysts: list[str] = field(default_factory=list)
    rss_headlines: list[str] = field(default_factory=list)  # top scored headlines

    def to_dict(self) -> dict:
        return {
            "overall_score": self.overall_score,
            "geo_score": self.geo_score,
            "news_score": self.news_score,
            "rss_score": self.rss_score,
            "gaming_score": self.gaming_score,
            "fear_greed_index": self.fear_greed_index,
            "key_events": self.key_events,
            "risk_flags": self.risk_flags,
            "opportunities": self.opportunities,
            "gaming_catalysts": self.gaming_catalysts,
            "rss_headlines": self.rss_headlines,
        }


# --- Context-aware NLP scoring ---


def score_headline_nlp(headline: str) -> tuple[float, bool]:
    """Score a headline using context-aware NLP.

    Handles negation (e.g. "SEC drops lawsuit" = bullish, not bearish),
    amplifiers ("BREAKING: rate cut" scores higher), and phrase-level matching.

    Returns (score, is_breaking).
    """
    text = headline.lower().strip()
    words = text.split()
    word_set = set(words)

    # Check for amplifiers — boost final score magnitude
    amplifier = 1.0
    for amp_word, amp_mult in AMPLIFIER_WORDS.items():
        if amp_word in text:
            amplifier = max(amplifier, amp_mult)

    is_breaking = amplifier >= 1.3

    # Check for negation in the headline
    has_negation = bool(word_set & NEGATION_WORDS)

    # Score using phrase matching (longer phrases first for specificity)
    total_score = 0.0
    matched_phrases: set[str] = set()

    # Sort by phrase length descending — match "missile strike" before "missile"
    sorted_phrases = sorted(SCORED_PHRASES, key=lambda x: len(x[0]), reverse=True)

    for phrase, base_score, weight in sorted_phrases:
        if phrase in text:
            # Skip if a longer phrase already matched this region
            if any(phrase in mp and phrase != mp for mp in matched_phrases):
                continue
            matched_phrases.add(phrase)

            score = base_score * weight

            # Negation flips sentiment (e.g. "SEC drops lawsuit" → bullish)
            if has_negation:
                score *= -0.7  # partial flip (negation doesn't fully reverse)

            total_score += score

    # Apply amplifier to magnitude
    total_score *= amplifier

    return (max(-5.0, min(5.0, total_score)), is_breaking)


def _deduplicate_headlines(headlines: list[ScoredHeadline]) -> list[ScoredHeadline]:
    """Remove duplicate stories across sources using title similarity."""
    seen: list[str] = []
    unique: list[ScoredHeadline] = []

    for h in headlines:
        # Normalize: lowercase, strip punctuation, take first 8 words
        normalized = re.sub(r"[^\w\s]", "", h.title.lower())
        key_words = " ".join(normalized.split()[:8])

        # Check if any seen headline shares 60%+ words
        is_dup = False
        key_set = set(key_words.split())
        for seen_key in seen:
            seen_set = set(seen_key.split())
            overlap = len(key_set & seen_set)
            total = max(len(key_set), len(seen_set), 1)
            if overlap / total > 0.6:
                is_dup = True
                break

        if not is_dup:
            seen.append(key_words)
            unique.append(h)

    return unique


def _recency_weight(published: datetime | None) -> float:
    """Weight headlines by recency. Breaking news = higher impact."""
    if published is None:
        return 0.7  # unknown age, moderate weight

    age_seconds = (datetime.now(timezone.utc) - published).total_seconds()
    if age_seconds < 0:
        age_seconds = 0

    if age_seconds < 900:  # < 15 min — breaking
        return 1.5
    if age_seconds < 1800:  # < 30 min
        return 1.3
    if age_seconds < 3600:  # < 1 hour
        return 1.1
    if age_seconds < 7200:  # < 2 hours
        return 0.9
    if age_seconds < RSS_MAX_AGE_SECONDS:  # < 4 hours
        return 0.7
    return 0.4  # old news, mostly priced in


class SentimentTracker:
    """Tracks geopolitical events, news, and market sentiment from public sources."""

    def __init__(self, enable_rss: bool = True):
        self._session: aiohttp.ClientSession | None = None
        self.enable_rss = enable_rss
        # Cache: avoid re-parsing same feed within 2 minutes
        self._rss_cache: dict[str, tuple[float, list[ScoredHeadline]]] = {}
        self._rss_cache_ttl = 120  # seconds

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_report(self) -> SentimentReport:
        """Gather sentiment from all available sources."""
        tasks = [
            self._fetch_fear_greed(),
            self._scan_geopolitical(),
            self._analyze_news(),
            self._scan_gaming_sector(),
        ]
        if self.enable_rss:
            tasks.append(self._scan_rss_feeds())

        results = await asyncio.gather(*tasks, return_exceptions=True)

        fear_greed = results[0]
        geo_events = results[1]
        news_sentiment = results[2]
        gaming = results[3]
        rss_data = results[4] if self.enable_rss and len(results) > 4 else None

        fg_index = fear_greed if isinstance(fear_greed, int) else None
        geo_data = (
            geo_events if isinstance(geo_events, dict)
            else {"score": 0, "events": [], "flags": []}
        )
        news_data = (
            news_sentiment if isinstance(news_sentiment, dict)
            else {"score": 0, "events": [], "opportunities": []}
        )
        gaming_data = (
            gaming if isinstance(gaming, dict)
            else {"score": 0, "catalysts": []}
        )
        rss_result = (
            rss_data if isinstance(rss_data, dict)
            else {"score": 0, "headlines": []}
        )

        # Fear & Greed contribution
        fg_score = 0.0
        if fg_index is not None:
            if fg_index < 25:
                fg_score = -3.0
            elif fg_index < 45:
                fg_score = -1.0
            elif fg_index > 75:
                fg_score = 2.0
            elif fg_index > 55:
                fg_score = 1.0

        geo_score = geo_data["score"]
        news_score = news_data["score"]
        rss_score = rss_result["score"]
        gaming_score = gaming_data["score"]

        # Weight by active sources (RSS gets higher weight — it's faster/richer)
        active_scores: list[tuple[float, float]] = []  # (score, weight)
        if fg_score != 0.0:
            active_scores.append((fg_score, 1.0))
        if geo_score != 0.0:
            active_scores.append((geo_score, 1.0))
        if news_score != 0.0:
            active_scores.append((news_score, 1.0))
        if rss_score != 0.0:
            active_scores.append((rss_score, 1.5))  # RSS weighted 1.5x

        if active_scores:
            total_weight = sum(w for _, w in active_scores)
            raw_avg = sum(s * w for s, w in active_scores) / total_weight
        else:
            raw_avg = 0.0
        overall = max(-10, min(10, raw_avg * 3))

        return SentimentReport(
            overall_score=round(overall, 2),
            geo_score=round(geo_score, 2),
            news_score=round(news_score, 2),
            rss_score=round(rss_score, 2),
            gaming_score=round(gaming_score, 2),
            fear_greed_index=fg_index,
            key_events=geo_data["events"] + news_data["events"],
            risk_flags=geo_data["flags"],
            opportunities=news_data["opportunities"],
            gaming_catalysts=gaming_data["catalysts"],
            rss_headlines=rss_result["headlines"],
        )

    async def _fetch_fear_greed(self) -> int | None:
        """Fetch Crypto Fear & Greed Index."""
        try:
            session = await self._get_session()
            url = "https://api.alternative.me/fng/?limit=1"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return int(data["data"][0]["value"])
        except Exception as e:
            logger.warning(f"Fear & Greed fetch failed: {e}")
        return None

    async def _scan_geopolitical(self) -> dict[str, Any]:
        """Score geopolitical risk from CoinGecko trending + CryptoPanic."""
        events: list[str] = []
        flags: list[str] = []
        score = 0.0

        try:
            session = await self._get_session()
            url = "https://api.coingecko.com/api/v3/search/trending"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    coins = data.get("coins", [])
                    names = [c["item"]["name"].lower() for c in coins[:5]]
                    events.append(f"Trending: {', '.join(names)}")

                    meme_keywords = ["pepe", "doge", "shib", "floki", "bonk", "wif", "meme"]
                    defi_keywords = ["aave", "uni", "link", "maker", "lido"]
                    meme_count = sum(1 for n in names if any(kw in n for kw in meme_keywords))
                    defi_count = sum(1 for n in names if any(kw in n for kw in defi_keywords))

                    if meme_count >= 2:
                        score += 1.5
                        events.append("Memecoins trending — risk-on sentiment")
                    if defi_count >= 2:
                        score += 1.0
                        events.append("DeFi tokens trending — institutional interest")

            url = "https://cryptopanic.com/api/free/v1/posts/?auth_token=public&public=true"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for post in data.get("results", [])[:15]:
                        title = post.get("title", "").lower()
                        for hotspot in GEO_WATCHLIST:
                            if hotspot in title:
                                score -= 1.0
                                flags.append(f"GEO: {post['title'][:80]}")
                                break
        except Exception as e:
            logger.warning(f"Geo scan error: {e}")

        return {"score": max(-5, min(5, score)), "events": events, "flags": flags}

    async def _analyze_news(self) -> dict[str, Any]:
        """Analyze CryptoPanic headlines using NLP scoring."""
        events: list[str] = []
        opportunities: list[str] = []
        score = 0.0

        try:
            session = await self._get_session()
            url = "https://cryptopanic.com/api/free/v1/posts/?auth_token=public&public=true"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for post in data.get("results", [])[:20]:
                        title = post.get("title", "")
                        headline_score, is_breaking = score_headline_nlp(title)
                        if headline_score < -0.3:
                            score += headline_score
                            label = "BREAKING BEARISH" if is_breaking else "BEARISH"
                            events.append(f"{label}: {title[:80]}")
                        elif headline_score > 0.3:
                            score += headline_score
                            label = "BREAKING BULLISH" if is_breaking else "BULLISH"
                            opportunities.append(f"{label}: {title[:80]}")
        except Exception as e:
            logger.warning(f"News analysis error: {e}")

        return {"score": max(-5, min(5, score)), "events": events, "opportunities": opportunities}

    async def _scan_gaming_sector(self) -> dict[str, Any]:
        """Scan for gaming/GTA 6 related catalysts."""
        catalysts: list[str] = []
        score = 0.0

        try:
            session = await self._get_session()

            url = "https://api.coingecko.com/api/v3/coins/categories"
            async with session.get(url) as resp:
                if resp.status == 200:
                    categories = await resp.json()
                    for cat in categories:
                        name = cat.get("name", "").lower()
                        if any(kw in name for kw in ["gaming", "play-to-earn", "metaverse"]):
                            change_24h = cat.get("market_cap_change_24h", 0) or 0
                            if change_24h > 5:
                                score += 2
                                catalysts.append(f"Gaming sector up {change_24h:.1f}% (24h)")
                            elif change_24h > 0:
                                score += 0.5
                            elif change_24h < -5:
                                score -= 1.5
                                catalysts.append(f"Gaming sector down {change_24h:.1f}% (24h)")
                            break

            url = "https://api.coingecko.com/api/v3/search/trending"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gaming_ids = {
                        "gala", "immutable-x", "ronin", "the-sandbox", "axie-infinity",
                        "decentraland", "enjincoin", "illuvium", "beam", "yield-guild-games",
                        "pixels", "superverse",
                    }
                    for coin in data.get("coins", []):
                        item = coin.get("item", {})
                        coin_id = item.get("id", "").lower()
                        if coin_id in gaming_ids:
                            score += 1.5
                            catalysts.append(f"TRENDING: {item.get('name', coin_id)}")

        except Exception as e:
            logger.warning(f"Gaming sector scan error: {e}")

        return {"score": max(-5, min(5, score)), "catalysts": catalysts}

    # --- RSS feed scraping ---

    async def _fetch_single_rss(self, feed_url: str) -> list[ScoredHeadline]:
        """Fetch and parse a single RSS feed, return scored headlines."""
        # Check cache
        now = time.monotonic()
        if feed_url in self._rss_cache:
            cached_time, cached_items = self._rss_cache[feed_url]
            if now - cached_time < self._rss_cache_ttl:
                return cached_items

        headlines: list[ScoredHeadline] = []
        try:
            session = await self._get_session()
            async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    logger.debug(f"RSS feed {feed_url} returned {resp.status}")
                    return []
                raw = await resp.text()

            root = ElementTree.fromstring(raw)

            # Handle both RSS 2.0 (<item>) and Atom (<entry>) formats
            items = root.findall(".//item") or root.findall(
                ".//{http://www.w3.org/2005/Atom}entry"
            )

            source = feed_url.split("//")[1].split("/")[0] if "//" in feed_url else feed_url

            for item in items[:15]:  # max 15 per feed
                # RSS 2.0
                title_el = item.find("title")
                pub_el = item.find("pubDate")
                desc_el = item.find("description")

                # Atom fallback
                if title_el is None:
                    title_el = item.find("{http://www.w3.org/2005/Atom}title")
                if pub_el is None:
                    pub_el = (
                        item.find("{http://www.w3.org/2005/Atom}published")
                        or item.find("{http://www.w3.org/2005/Atom}updated")
                    )

                if title_el is None or not title_el.text:
                    continue

                title = _strip_html(title_el.text)

                # Parse publish date
                published = None
                if pub_el is not None and pub_el.text:
                    try:
                        published = parsedate_to_datetime(pub_el.text.strip())
                        if published.tzinfo is None:
                            published = published.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pass

                # Skip old headlines
                if published:
                    age = (datetime.now(timezone.utc) - published).total_seconds()
                    if age > RSS_MAX_AGE_SECONDS:
                        continue

                # Score with NLP
                # Combine title + description snippet for better context
                full_text = title
                if desc_el is not None and desc_el.text:
                    snippet = _strip_html(desc_el.text)[:200]
                    full_text = f"{title}. {snippet}"

                headline_score, is_breaking = score_headline_nlp(full_text)

                # Apply recency weighting
                recency = _recency_weight(published)
                weighted_score = headline_score * recency

                if abs(weighted_score) > 0.2:  # filter noise
                    headlines.append(ScoredHeadline(
                        title=title[:120],
                        score=round(weighted_score, 3),
                        source=source,
                        published=published,
                        is_breaking=is_breaking,
                    ))

        except ElementTree.ParseError as e:
            logger.debug(f"RSS parse error for {feed_url}: {e}")
        except asyncio.TimeoutError:
            logger.debug(f"RSS feed timeout: {feed_url}")
        except Exception as e:
            logger.warning(f"RSS fetch error for {feed_url}: {e}")

        # Update cache
        self._rss_cache[feed_url] = (now, headlines)
        return headlines

    async def _scan_rss_feeds(self) -> dict[str, Any]:
        """Scrape all RSS feeds concurrently, deduplicate, and score."""
        all_feeds = CRYPTO_RSS_FEEDS + MACRO_RSS_FEEDS

        feed_results = await asyncio.gather(
            *[self._fetch_single_rss(url) for url in all_feeds],
            return_exceptions=True,
        )

        # Collect all headlines
        all_headlines: list[ScoredHeadline] = []
        for result in feed_results:
            if isinstance(result, list):
                all_headlines.extend(result)

        if not all_headlines:
            return {"score": 0, "headlines": []}

        # Deduplicate across sources
        unique = _deduplicate_headlines(all_headlines)

        # Sort by absolute score (most impactful first)
        unique.sort(key=lambda h: abs(h.score), reverse=True)

        # Aggregate score (cap contribution per headline to avoid one story dominating)
        total_score = 0.0
        headline_strings: list[str] = []
        for h in unique[:20]:  # top 20 headlines
            capped = max(-1.5, min(1.5, h.score))
            total_score += capped
            direction = "+" if h.score > 0 else ""
            prefix = "BREAKING " if h.is_breaking else ""
            headline_strings.append(
                f"{prefix}[{direction}{h.score:.2f}] {h.title} ({h.source})"
            )

        # Log top headlines
        if headline_strings:
            logger.info(
                f"RSS sentiment: {total_score:+.2f} from "
                f"{len(unique)} unique headlines ({len(all_headlines)} total)"
            )
            for hl in headline_strings[:5]:
                logger.info(f"  RSS: {hl}")

        return {
            "score": max(-5, min(5, total_score)),
            "headlines": headline_strings[:10],  # top 10 for reporting
        }


def score_headline(headline: str) -> float:
    """Score a single headline. Backwards-compatible wrapper around NLP scorer."""
    score, _ = score_headline_nlp(headline)
    return score
