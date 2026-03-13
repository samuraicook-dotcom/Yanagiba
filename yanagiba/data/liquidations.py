"""Liquidation stream via Binance/Bybit WebSockets.

Monitors forced liquidations in real-time. Liquidation cascades
(many liquidations in one direction within seconds) often cause price
wicks and reversals — high-probability reversal setups.

Free, no authentication required. Public market data streams.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass

import websockets

logger = logging.getLogger("yanagiba")

# Binance futures liquidation stream (all symbols)
BINANCE_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"
# Bybit public linear stream
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"


@dataclass
class LiquidationEvent:
    """A single forced liquidation."""

    exchange: str
    symbol: str
    side: str  # "buy" (short liquidated) or "sell" (long liquidated)
    price: float
    quantity: float
    usd_value: float
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "side": self.side,
            "price": self.price,
            "quantity": self.quantity,
            "usd_value": round(self.usd_value, 2),
        }


@dataclass
class LiquidationSummary:
    """Aggregated liquidation data for a symbol over a time window."""

    symbol: str
    long_liqs_usd: float = 0.0  # total USD of long liquidations (sells)
    short_liqs_usd: float = 0.0  # total USD of short liquidations (buys)
    long_count: int = 0
    short_count: int = 0
    cascade_detected: bool = False
    cascade_side: str = ""  # "long" or "short" — which side got liquidated
    net_pressure: float = 0.0  # positive = more longs liquidated (bearish pressure)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "long_liqs_usd": round(self.long_liqs_usd, 2),
            "short_liqs_usd": round(self.short_liqs_usd, 2),
            "long_count": self.long_count,
            "short_count": self.short_count,
            "cascade_detected": self.cascade_detected,
            "cascade_side": self.cascade_side,
            "net_pressure": round(self.net_pressure, 2),
        }


class LiquidationTracker:
    """Collects liquidation events from exchange WebSockets.

    Runs WebSocket connections in background tasks. Aggregates events
    per symbol over a configurable window (default 5 minutes).
    Detects liquidation cascades (>$100K in same direction within 30s).
    """

    def __init__(self, window_seconds: int = 300, cascade_threshold_usd: float = 100_000):
        self._window = window_seconds
        self._cascade_threshold = cascade_threshold_usd
        # Recent events per symbol: deque of LiquidationEvent
        self._events: dict[str, deque[LiquidationEvent]] = {}
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        """Start WebSocket listeners in background."""
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._listen_binance()),
            asyncio.create_task(self._listen_bybit()),
        ]
        logger.info("Liquidation tracker started (Binance + Bybit)")

    async def stop(self):
        """Stop all WebSocket listeners."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    def get_summary(self, symbol: str) -> LiquidationSummary:
        """Get aggregated liquidation data for a symbol.

        Normalizes symbol format: BTC/USDT, BTCUSDT, BTC/USDT:USDT all map
        to BTCUSDT for matching.
        """
        normalized = self._normalize_symbol(symbol)
        summary = LiquidationSummary(symbol=symbol)

        events = self._events.get(normalized, deque())
        if not events:
            return summary

        now = time.time()
        cutoff = now - self._window
        # Track recent events for cascade detection (30s window)
        cascade_window = now - 30
        recent_long_usd = 0.0
        recent_short_usd = 0.0

        for ev in events:
            if ev.timestamp < cutoff:
                continue
            if ev.side == "sell":  # long got liquidated
                summary.long_liqs_usd += ev.usd_value
                summary.long_count += 1
                if ev.timestamp >= cascade_window:
                    recent_long_usd += ev.usd_value
            else:  # short got liquidated
                summary.short_liqs_usd += ev.usd_value
                summary.short_count += 1
                if ev.timestamp >= cascade_window:
                    recent_short_usd += ev.usd_value

        # Detect cascade
        if recent_long_usd > self._cascade_threshold:
            summary.cascade_detected = True
            summary.cascade_side = "long"
            logger.info(
                f"LIQUIDATION CASCADE: {symbol} longs — "
                f"${recent_long_usd:,.0f} in 30s"
            )
        elif recent_short_usd > self._cascade_threshold:
            summary.cascade_detected = True
            summary.cascade_side = "short"
            logger.info(
                f"LIQUIDATION CASCADE: {symbol} shorts — "
                f"${recent_short_usd:,.0f} in 30s"
            )

        # Net pressure: positive = longs getting rekt, negative = shorts getting rekt
        total = summary.long_liqs_usd + summary.short_liqs_usd
        if total > 0:
            summary.net_pressure = (summary.long_liqs_usd - summary.short_liqs_usd) / total

        return summary

    def get_liq_score(self, summary: LiquidationSummary) -> float:
        """Score liquidation data for market analysis (-3 to +3).

        Cascade of long liqs = potential bottom (contrarian long) → +2
        Cascade of short liqs = potential top (contrarian short) → -2
        Heavy one-sided liqs without cascade = mild signal → ±1
        """
        if summary.cascade_detected:
            if summary.cascade_side == "long":
                return 2.0  # longs flushed, contrarian buy
            else:
                return -2.0  # shorts squeezed, contrarian sell

        if abs(summary.net_pressure) > 0.7:
            if summary.net_pressure > 0:
                return 1.0  # longs being punished
            else:
                return -1.0  # shorts being punished

        return 0.0

    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize to BTCUSDT format for internal matching."""
        return symbol.replace("/", "").replace(":USDT", "").upper()

    def _prune_old(self, symbol: str):
        """Remove events older than the window."""
        if symbol not in self._events:
            return
        cutoff = time.time() - self._window * 2  # keep 2x window for safety
        while self._events[symbol] and self._events[symbol][0].timestamp < cutoff:
            self._events[symbol].popleft()

    def _add_event(self, event: LiquidationEvent):
        """Add an event and prune old ones."""
        normalized = self._normalize_symbol(event.symbol)
        if normalized not in self._events:
            self._events[normalized] = deque(maxlen=1000)
        self._events[normalized].append(event)
        self._prune_old(normalized)

    async def _listen_binance(self):
        """Listen to Binance all-symbol liquidation stream."""
        while self._running:
            try:
                async with websockets.connect(
                    BINANCE_WS, ping_interval=20, ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    logger.info("Connected to Binance liquidation stream")
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(msg)
                            order = data.get("o", data)
                            symbol = order.get("s", "")
                            side = order.get("S", "").lower()
                            price = float(order.get("p", 0))
                            qty = float(order.get("q", 0))
                            ts = order.get("T", time.time() * 1000) / 1000

                            if symbol and price > 0 and qty > 0:
                                self._add_event(LiquidationEvent(
                                    exchange="binance",
                                    symbol=symbol,
                                    side=side,
                                    price=price,
                                    quantity=qty,
                                    usd_value=price * qty,
                                    timestamp=ts,
                                ))
                        except (ValueError, KeyError, TypeError):
                            continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.debug(f"Binance liq stream error: {e}")
                    await asyncio.sleep(5)

    async def _listen_bybit(self):
        """Listen to Bybit all-liquidation stream for major symbols."""
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        while self._running:
            try:
                async with websockets.connect(
                    BYBIT_WS, ping_interval=20, ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    # Subscribe to liquidation topics
                    sub_msg = {
                        "op": "subscribe",
                        "args": [f"allLiquidation.{s}" for s in symbols],
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info("Connected to Bybit liquidation stream")

                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(msg)
                            if data.get("topic", "").startswith("allLiquidation"):
                                liq_data = data.get("data", {})
                                symbol = liq_data.get("symbol", "")
                                side = liq_data.get("side", "").lower()
                                # Bybit: side=Buy means short liquidated,
                                # side=Sell means long liquidated
                                side = "buy" if side == "buy" else "sell"
                                price = float(liq_data.get("price", 0))
                                size = float(liq_data.get("size", 0))
                                ts = liq_data.get("updatedTime", time.time() * 1000)
                                if isinstance(ts, str):
                                    ts = float(ts)
                                ts = ts / 1000

                                if symbol and price > 0 and size > 0:
                                    self._add_event(LiquidationEvent(
                                        exchange="bybit",
                                        symbol=symbol,
                                        side=side,
                                        price=price,
                                        quantity=size,
                                        usd_value=price * size,
                                        timestamp=ts,
                                    ))
                        except (ValueError, KeyError, TypeError):
                            continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.debug(f"Bybit liq stream error: {e}")
                    await asyncio.sleep(5)
