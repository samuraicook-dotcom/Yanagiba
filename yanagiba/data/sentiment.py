"""Geopolitical and news sentiment tracker.

Monitors global events (wars, sanctions, regulatory actions, macro shocks) and
scores their likely impact on crypto markets. Uses free public APIs.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Keywords that signal risk-off / bearish pressure
BEARISH_KEYWORDS = [
    "war", "invasion", "missile", "sanctions", "embargo", "nuclear",
    "conflict", "military", "escalation", "attack", "ceasefire collapse",
    "ban crypto", "crypto ban", "regulation crackdown", "sec lawsuit",
    "default", "recession", "rate hike", "inflation spike", "bank collapse",
    "hack", "exploit", "rug pull", "insolvency",
]

# Keywords that signal risk-on / bullish catalysts
BULLISH_KEYWORDS = [
    "ceasefire", "peace deal", "de-escalation", "treaty", "truce",
    "rate cut", "stimulus", "quantitative easing", "etf approved", "etf approval",
    "adoption", "institutional buy", "reserve asset", "legal tender",
    "bull run", "all time high", "ath", "rally",
]

# Gaming sector / GTA 6 narrative keywords
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


@dataclass
class SentimentReport:
    overall_score: float  # -10 to +10
    geo_score: float  # geopolitical risk score
    news_score: float  # general news sentiment
    gaming_score: float = 0.0  # gaming sector sentiment (GTA 6, P2E, metaverse)
    fear_greed_index: int | None = None  # 0-100
    key_events: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    opportunities: list[str] = field(default_factory=list)
    gaming_catalysts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overall_score": self.overall_score,
            "geo_score": self.geo_score,
            "news_score": self.news_score,
            "gaming_score": self.gaming_score,
            "fear_greed_index": self.fear_greed_index,
            "key_events": self.key_events,
            "risk_flags": self.risk_flags,
            "opportunities": self.opportunities,
            "gaming_catalysts": self.gaming_catalysts,
        }


class SentimentTracker:
    """Tracks geopolitical events, news, and market sentiment from public sources."""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

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
        fear_greed, geo_events, news_sentiment, gaming = await asyncio.gather(
            self._fetch_fear_greed(),
            self._scan_geopolitical(),
            self._analyze_news(),
            self._scan_gaming_sector(),
            return_exceptions=True,
        )

        fg_index = fear_greed if isinstance(fear_greed, int) else None
        geo_data = geo_events if isinstance(geo_events, dict) else {"score": 0, "events": [], "flags": []}
        news_data = news_sentiment if isinstance(news_sentiment, dict) else {"score": 0, "events": [], "opportunities": []}
        gaming_data = gaming if isinstance(gaming, dict) else {"score": 0, "catalysts": []}

        # Fear & Greed contribution: 0-25 = extreme fear (-3), 25-45 = fear (-1),
        # 55-75 = greed (+1), 75-100 = extreme greed (+2, but caution)
        fg_score = 0.0
        if fg_index is not None:
            if fg_index < 25:
                fg_score = -3.0  # extreme fear = bearish but potential bounce
            elif fg_index < 45:
                fg_score = -1.0
            elif fg_index > 75:
                fg_score = 2.0  # greed, but toppy
            elif fg_index > 55:
                fg_score = 1.0

        geo_score = geo_data["score"]
        news_score = news_data["score"]
        gaming_score = gaming_data["score"]
        overall = max(-10, min(10, (fg_score + geo_score + news_score) / 3 * 5))

        return SentimentReport(
            overall_score=round(overall, 2),
            geo_score=round(geo_score, 2),
            news_score=round(news_score, 2),
            gaming_score=round(gaming_score, 2),
            fear_greed_index=fg_index,
            key_events=geo_data["events"] + news_data["events"],
            risk_flags=geo_data["flags"],
            opportunities=news_data["opportunities"],
            gaming_catalysts=gaming_data["catalysts"],
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
        """Score geopolitical risk based on available signals.

        In production, this would connect to a news API (e.g., NewsAPI, GDELT).
        Here we provide the scoring framework that processes headlines.
        """
        events: list[str] = []
        flags: list[str] = []
        score = 0.0

        try:
            session = await self._get_session()
            # CoinGecko trending as a proxy for market attention
            url = "https://api.coingecko.com/api/v3/search/trending"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    coins = data.get("coins", [])
                    # If memecoins are trending, risk-on sentiment
                    names = [c["item"]["name"].lower() for c in coins[:5]]
                    events.append(f"Trending: {', '.join(names)}")
        except Exception as e:
            logger.warning(f"Geo scan error: {e}")

        return {"score": score, "events": events, "flags": flags}

    async def _analyze_news(self) -> dict[str, Any]:
        """Analyze news headlines for crypto-relevant sentiment.

        In production, connect to NewsAPI, CryptoPanic, or similar.
        Framework scores headlines against keyword lists.
        """
        events: list[str] = []
        opportunities: list[str] = []
        score = 0.0

        try:
            session = await self._get_session()
            # CryptoPanic free tier (no API key needed for basic)
            url = "https://cryptopanic.com/api/free/v1/posts/?auth_token=public&public=true"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for post in data.get("results", [])[:20]:
                        title = post.get("title", "").lower()
                        # Score against keyword lists
                        for kw in BEARISH_KEYWORDS:
                            if kw in title:
                                score -= 0.5
                                events.append(f"BEARISH: {post['title'][:80]}")
                                break
                        for kw in BULLISH_KEYWORDS:
                            if kw in title:
                                score += 0.5
                                opportunities.append(f"BULLISH: {post['title'][:80]}")
                                break
        except Exception as e:
            logger.warning(f"News analysis error: {e}")

        return {"score": max(-5, min(5, score)), "events": events, "opportunities": opportunities}


    async def _scan_gaming_sector(self) -> dict[str, Any]:
        """Scan for gaming/GTA 6 related catalysts that could move gaming tokens.

        Tracks: GTA 6 launch news, P2E developments, metaverse announcements,
        gaming partnership deals, and AAA game blockchain integrations.
        """
        catalysts: list[str] = []
        score = 0.0

        try:
            session = await self._get_session()

            # Check CoinGecko for gaming category performance
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

            # Check trending for gaming tokens
            url = "https://api.coingecko.com/api/v3/search/trending"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gaming_ids = {"gala", "immutable-x", "ronin", "the-sandbox", "axie-infinity",
                                  "decentraland", "enjincoin", "illuvium", "beam", "yield-guild-games",
                                  "pixels", "superverse"}
                    for coin in data.get("coins", []):
                        item = coin.get("item", {})
                        coin_id = item.get("id", "").lower()
                        if coin_id in gaming_ids:
                            score += 1.5
                            catalysts.append(f"TRENDING: {item.get('name', coin_id)}")

        except Exception as e:
            logger.warning(f"Gaming sector scan error: {e}")

        return {"score": max(-5, min(5, score)), "catalysts": catalysts}


def score_headline(headline: str) -> float:
    """Score a single headline. Useful for real-time news feed processing."""
    headline_lower = headline.lower()
    score = 0.0
    for kw in BEARISH_KEYWORDS:
        if kw in headline_lower:
            score -= 1.0
    for kw in BULLISH_KEYWORDS:
        if kw in headline_lower:
            score += 1.0
    return max(-5, min(5, score))
