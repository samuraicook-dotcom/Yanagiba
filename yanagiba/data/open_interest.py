"""Open Interest tracker via ccxt.

Tracks OI changes across symbols to detect leverage buildup/unwind.
Rising OI + rising price = new longs. Rising OI + falling price = new shorts.
Falling OI = position closing (trend exhaustion).

Uses exchange public endpoints via ccxt — free, no API key needed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import ccxt.async_support as ccxt

logger = logging.getLogger("yanagiba")


@dataclass
class OpenInterestSnapshot:
    """OI data for a single symbol."""

    symbol: str
    oi_value: float  # OI in contracts/coins
    oi_usd: float  # OI in USD (if available)
    timestamp: float  # unix timestamp
    change_pct: float = 0.0  # % change from previous snapshot
    signal: str = ""  # "buildup", "unwind", or ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "oi_value": self.oi_value,
            "oi_usd": self.oi_usd,
            "change_pct": round(self.change_pct, 2),
            "signal": self.signal,
        }


class OpenInterestTracker:
    """Tracks open interest changes across trading cycles.

    Stores previous OI values to compute cycle-over-cycle % change.
    A >5% OI spike with price moving same direction = trend confirmation.
    A >5% OI spike against price = crowded trade, potential reversal.
    """

    def __init__(self):
        # {symbol: previous OI value}
        self._prev_oi: dict[str, float] = {}
        self._last_fetch: dict[str, float] = {}
        self._min_interval = 30  # seconds between fetches per symbol

    async def fetch_oi(
        self, exchange: ccxt.Exchange, symbol: str,
    ) -> OpenInterestSnapshot | None:
        """Fetch current OI for a symbol and compute change from last cycle."""
        # Rate limit: don't hammer the endpoint
        now = time.time()
        if symbol in self._last_fetch and now - self._last_fetch[symbol] < self._min_interval:
            return None

        try:
            # Resolve futures symbol
            resolved = symbol
            if symbol in exchange.markets:
                pass
            elif f"{symbol}:USDT" in exchange.markets:
                resolved = f"{symbol}:USDT"

            oi_data = await exchange.fetch_open_interest(resolved)
            if not oi_data:
                return None

            oi_value = oi_data.get("openInterestAmount", 0) or 0
            oi_usd = oi_data.get("openInterestValue", 0) or 0
            ts = oi_data.get("timestamp", now * 1000) / 1000

            self._last_fetch[symbol] = now

            # Compute change from previous cycle
            change_pct = 0.0
            signal = ""
            if symbol in self._prev_oi and self._prev_oi[symbol] > 0:
                change_pct = ((oi_value - self._prev_oi[symbol]) / self._prev_oi[symbol]) * 100

                if change_pct > 5:
                    signal = "buildup"  # leverage increasing
                elif change_pct < -5:
                    signal = "unwind"  # positions closing

            self._prev_oi[symbol] = oi_value

            snap = OpenInterestSnapshot(
                symbol=symbol,
                oi_value=oi_value,
                oi_usd=oi_usd,
                timestamp=ts,
                change_pct=change_pct,
                signal=signal,
            )

            if signal:
                logger.info(
                    f"OI {signal.upper()} {symbol}: {change_pct:+.1f}% "
                    f"(${oi_usd:,.0f})"
                )
            return snap

        except Exception as e:
            logger.debug(f"OI fetch failed for {symbol}: {e}")
            return None

    async def fetch_funding_history(
        self, exchange: ccxt.Exchange, symbol: str,
    ) -> list[dict] | None:
        """Fetch recent funding rate history for extreme detection."""
        try:
            resolved = symbol
            if f"{symbol}:USDT" in exchange.markets:
                resolved = f"{symbol}:USDT"

            history = await exchange.fetch_funding_rate_history(resolved, limit=8)
            return [
                {
                    "rate": h.get("fundingRate", 0),
                    "timestamp": h.get("timestamp", 0),
                }
                for h in history
            ] if history else None
        except Exception as e:
            logger.debug(f"Funding history failed for {symbol}: {e}")
            return None

    def get_oi_score(self, snapshot: OpenInterestSnapshot | None, price_change_pct: float) -> float:
        """Score OI signal for market analysis (-3 to +3).

        OI buildup + price up = trend confirmed (+2)
        OI buildup + price down = shorts piling in, squeeze potential (+1 for longs)
        OI unwind + price down = longs capitulating (-1)
        OI unwind + price up = short squeeze in progress (+1.5)
        """
        if not snapshot or abs(snapshot.change_pct) < 2:
            return 0.0

        if snapshot.signal == "buildup":
            if price_change_pct > 0:
                return 2.0  # new longs confirmed
            else:
                return 1.0  # shorts piling in, squeeze potential
        elif snapshot.signal == "unwind":
            if price_change_pct > 0:
                return 1.5  # short squeeze
            else:
                return -1.0  # capitulation
        return 0.0
