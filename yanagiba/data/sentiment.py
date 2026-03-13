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

# Geopolitical hotspots to scan in trending data
GEO_WATCHLIST = [
    "russia", "ukraine", "israel", "gaza", "china", "taiwan",
    "iran", "north korea", "sanctions", "war",
]


@dataclass
class SentimentReport:
    overall_score: float  # -10 to +10
    geo_score: float  # geopolitical risk score
    news_score: float  # general news sentiment
    gaming_score: float = 0.0
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
    """Tracks geopolitical events, news, and market sentiment."""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        async with self._lock:
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

        # Fear & Greed contribution (symmetric range)
        fg_score = 0.0
        if fg_index is not None:
            if fg_index < 25:
                fg_score = -3.0
            elif fg_index < 45:
                fg_score = -1.0
            elif fg_index > 75:
                fg_score = 3.0  # symmetric with extreme fear
            elif fg_index > 55:
                fg_score = 1.0

        geo_score = geo_data["score"]
        news_score = news_data["score"]
        gaming_score = gaming_data["score"]
        # Include gaming_score in overall and use symmetric formula
        components = [fg_score, geo_score, news_score, gaming_score]
        non_zero = [c for c in components if c != 0]
        if non_zero:
            avg = sum(non_zero) / len(non_zero)
        else:
            avg = 0
        overall = max(-10, min(10, avg * 3))

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
        """Score geopolitical risk from CoinGecko trending data."""
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
                    names = [
                        c["item"]["name"].lower() for c in coins[:7]
                    ]
                    events.append(f"Trending: {', '.join(names)}")

                    # Memecoins trending = risk-on sentiment
                    meme_kw = ["doge", "pepe", "shib", "floki", "bonk", "wif"]
                    meme_count = sum(
                        1 for n in names if any(m in n for m in meme_kw)
                    )
                    if meme_count >= 2:
                        score += 2.0
                        events.append(
                            f"Risk-on: {meme_count} memecoins trending"
                        )
                    elif meme_count == 1:
                        score += 0.5

                    # Check trending coin names for geo watchlist keywords
                    for name in names:
                        for kw in GEO_WATCHLIST:
                            if kw in name:
                                score -= 2.0
                                flags.append(f"Geo risk: '{kw}' trending")
                                break

        except Exception as e:
            logger.warning(f"Geo scan error: {e}")

        return {
            "score": max(-5, min(5, score)),
            "events": events,
            "flags": flags,
        }

    async def _analyze_news(self) -> dict[str, Any]:
        """Analyze news headlines for crypto-relevant sentiment.

        Uses CoinGecko status updates as a free news proxy since
        CryptoPanic requires an API key.
        """
        events: list[str] = []
        opportunities: list[str] = []
        score = 0.0

        try:
            session = await self._get_session()
            # Use CoinGecko global data as a market sentiment proxy
            url = "https://api.coingecko.com/api/v3/global"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gd = data.get("data", {})
                    # Market cap change as sentiment signal
                    mc_change = gd.get(
                        "market_cap_change_percentage_24h_usd", 0,
                    ) or 0
                    if mc_change > 3:
                        score += 2.0
                        opportunities.append(
                            f"Market cap up {mc_change:.1f}% (24h)"
                        )
                    elif mc_change > 1:
                        score += 0.5
                    elif mc_change < -3:
                        score -= 2.0
                        events.append(
                            f"Market cap down {mc_change:.1f}% (24h)"
                        )
                    elif mc_change < -1:
                        score -= 0.5

                    # BTC dominance shift
                    btc_dom = gd.get("market_cap_percentage", {}).get(
                        "btc", 0,
                    )
                    if btc_dom > 60:
                        events.append(
                            f"BTC dominance high ({btc_dom:.1f}%) "
                            f"— risk-off"
                        )
                        score -= 0.5
                    elif btc_dom < 45:
                        opportunities.append(
                            f"BTC dominance low ({btc_dom:.1f}%) "
                            f"— alt season"
                        )
                        score += 0.5

        except Exception as e:
            logger.warning(f"News analysis error: {e}")

        return {
            "score": max(-5, min(5, score)),
            "events": events,
            "opportunities": opportunities,
        }

    async def _scan_gaming_sector(self) -> dict[str, Any]:
        """Scan for gaming sector catalysts."""
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
                        if any(
                            kw in name
                            for kw in ["gaming", "play-to-earn", "metaverse"]
                        ):
                            change_24h = (
                                cat.get("market_cap_change_24h", 0) or 0
                            )
                            if change_24h > 5:
                                score += 2
                                catalysts.append(
                                    f"Gaming sector up "
                                    f"{change_24h:.1f}% (24h)"
                                )
                            elif change_24h > 0:
                                score += 0.5
                            elif change_24h < -5:
                                score -= 1.5
                                catalysts.append(
                                    f"Gaming sector down "
                                    f"{change_24h:.1f}% (24h)"
                                )
                            break

            # Check trending for gaming tokens
            url = "https://api.coingecko.com/api/v3/search/trending"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    gaming_ids = {
                        "gala", "immutable-x", "ronin", "the-sandbox",
                        "axie-infinity", "decentraland", "enjincoin",
                        "illuvium", "beam", "yield-guild-games",
                        "pixels", "superverse",
                    }
                    for coin in data.get("coins", []):
                        item = coin.get("item", {})
                        coin_id = item.get("id", "").lower()
                        if coin_id in gaming_ids:
                            score += 1.5
                            catalysts.append(
                                f"TRENDING: {item.get('name', coin_id)}"
                            )

        except Exception as e:
            logger.warning(f"Gaming sector scan error: {e}")

        return {"score": max(-5, min(5, score)), "catalysts": catalysts}
