"""Real volume delta from actual trade data via ccxt.

Instead of guessing buy/sell from candle color, this fetches actual
trades and classifies them as buys (taker buy at ask) or sells
(taker sell at bid) for true volume delta.

Uses exchange public endpoints via ccxt — free, no API key needed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import ccxt.async_support as ccxt

logger = logging.getLogger("yanagiba")


@dataclass
class VolumeFlowSnapshot:
    """Real buy/sell volume breakdown for a symbol."""

    symbol: str
    buy_volume_usd: float  # taker buys (aggressive buying)
    sell_volume_usd: float  # taker sells (aggressive selling)
    total_volume_usd: float
    delta: float  # buy - sell (positive = buying pressure)
    delta_pct: float  # delta as % of total (normalized -1 to +1)
    trade_count: int
    large_buy_count: int  # trades > $10K USD
    large_sell_count: int  # trades > $10K USD
    avg_trade_size_usd: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "buy_volume_usd": round(self.buy_volume_usd, 2),
            "sell_volume_usd": round(self.sell_volume_usd, 2),
            "delta": round(self.delta, 2),
            "delta_pct": round(self.delta_pct, 4),
            "trade_count": self.trade_count,
            "large_buy_count": self.large_buy_count,
            "large_sell_count": self.large_sell_count,
            "avg_trade_size_usd": round(self.avg_trade_size_usd, 2),
        }


class VolumeFlowTracker:
    """Fetches recent trades and computes real buy/sell volume.

    Classifies each trade as buy or sell based on the taker side.
    ccxt's fetch_trades returns trade data with a 'side' field when available.
    """

    def __init__(self, large_trade_threshold_usd: float = 10_000):
        self._large_threshold = large_trade_threshold_usd
        self._last_fetch: dict[str, float] = {}
        self._min_interval = 15  # seconds between fetches per symbol

    async def fetch_volume_flow(
        self, exchange: ccxt.Exchange, symbol: str,
    ) -> VolumeFlowSnapshot | None:
        """Fetch recent trades and compute real volume delta.

        Uses ccxt fetch_trades which returns the last N trades with
        taker side classification.
        """
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

            # Fetch last 200 trades
            trades = await exchange.fetch_trades(resolved, limit=200)
            if not trades:
                return None

            self._last_fetch[symbol] = now

            buy_vol = 0.0
            sell_vol = 0.0
            large_buys = 0
            large_sells = 0

            for trade in trades:
                price = trade.get("price", 0)
                amount = trade.get("amount", 0)
                side = trade.get("side", "")
                usd_value = price * amount

                if side == "buy":
                    buy_vol += usd_value
                    if usd_value >= self._large_threshold:
                        large_buys += 1
                elif side == "sell":
                    sell_vol += usd_value
                    if usd_value >= self._large_threshold:
                        large_sells += 1

            total = buy_vol + sell_vol
            delta = buy_vol - sell_vol
            delta_pct = delta / total if total > 0 else 0
            count = len(trades)
            avg_size = total / count if count > 0 else 0

            snap = VolumeFlowSnapshot(
                symbol=symbol,
                buy_volume_usd=buy_vol,
                sell_volume_usd=sell_vol,
                total_volume_usd=total,
                delta=delta,
                delta_pct=delta_pct,
                trade_count=count,
                large_buy_count=large_buys,
                large_sell_count=large_sells,
                avg_trade_size_usd=avg_size,
            )

            # Log notable imbalances
            if abs(delta_pct) > 0.3:
                side_label = "BUY" if delta_pct > 0 else "SELL"
                logger.info(
                    f"Volume flow {symbol}: {side_label} pressure "
                    f"({delta_pct:+.1%}, ${abs(delta):,.0f})"
                )

            return snap

        except Exception as e:
            logger.debug(f"Volume flow fetch failed for {symbol}: {e}")
            return None

    def get_flow_score(self, snapshot: VolumeFlowSnapshot | None) -> float:
        """Score volume flow for market analysis (-3 to +3).

        Strong buy pressure (>30% delta) = +2
        Moderate buy pressure (>15%) = +1
        Strong sell pressure (<-30%) = -2
        Moderate sell pressure (<-15%) = -1
        Large trades further amplify the signal.
        """
        if not snapshot:
            return 0.0

        score = 0.0

        # Delta-based scoring
        if snapshot.delta_pct > 0.3:
            score = 2.0
        elif snapshot.delta_pct > 0.15:
            score = 1.0
        elif snapshot.delta_pct < -0.3:
            score = -2.0
        elif snapshot.delta_pct < -0.15:
            score = -1.0

        # Large trade imbalance bonus
        large_diff = snapshot.large_buy_count - snapshot.large_sell_count
        if large_diff >= 3:
            score += 1.0  # big players buying
        elif large_diff <= -3:
            score -= 1.0  # big players selling

        return max(-3.0, min(3.0, score))
