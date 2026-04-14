"""
core/order_guard.py — Duplicate Order Prevention & Order State Tracking

Prevents the bot from ever placing two orders on the same symbol simultaneously.
Uses a threading lock so concurrent scheduler ticks can't race each other.

State machine per symbol:
  IDLE → PENDING_ENTRY → OPEN → PENDING_EXIT → IDLE

Also tracks all broker order IDs so we can cancel the right orders on exit.
"""

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("kaizen.order_guard")


class PositionState(Enum):
    IDLE = "IDLE"
    PENDING_ENTRY = "PENDING_ENTRY"   # Entry order placed, waiting for fill
    OPEN = "OPEN"                      # Position live, SL+TP orders active
    PENDING_EXIT = "PENDING_EXIT"      # Exit order placed, waiting for fill


@dataclass
class OrderRecord:
    """All order IDs associated with one live trade."""
    symbol: str
    state: PositionState = PositionState.IDLE
    entry_order_id: Optional[str] = None
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    direction: Optional[str] = None    # 'BUY' or 'SELL'
    quantity: int = 0
    entry_price: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    exit_order_id: Optional[str] = None


class OrderGuard:
    """
    Thread-safe guard that prevents duplicate orders per symbol.

    Usage:
        guard = OrderGuard()

        # Before placing entry:
        if not guard.can_enter(symbol):
            return  # already have a position or pending order

        guard.mark_pending(symbol)
        try:
            order_id = broker.place_order(...)
            guard.mark_open(symbol, order_id, ...)
        except:
            guard.mark_idle(symbol)  # rollback on failure
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, OrderRecord] = {}

    def _get_or_create(self, symbol: str) -> OrderRecord:
        if symbol not in self._records:
            self._records[symbol] = OrderRecord(symbol=symbol)
        return self._records[symbol]

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def can_enter(self, symbol: str) -> bool:
        """
        Returns True only if symbol is in IDLE state.
        Any other state means a position or order is already active.
        """
        with self._lock:
            rec = self._get_or_create(symbol)
            can = rec.state == PositionState.IDLE
            if not can:
                logger.warning(
                    "OrderGuard BLOCKED entry for %s — state=%s", symbol, rec.state.value
                )
            return can

    def is_open(self, symbol: str) -> bool:
        """Returns True if symbol has an open position."""
        with self._lock:
            return self._records.get(symbol, OrderRecord(symbol)).state == PositionState.OPEN

    def get_state(self, symbol: str) -> PositionState:
        with self._lock:
            return self._get_or_create(symbol).state

    def get_record(self, symbol: str) -> Optional[OrderRecord]:
        with self._lock:
            return self._records.get(symbol)

    def all_open_symbols(self) -> list[str]:
        """Return all symbols currently in OPEN state."""
        with self._lock:
            return [s for s, r in self._records.items() if r.state == PositionState.OPEN]

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def mark_pending(self, symbol: str) -> None:
        """Call immediately before placing an entry order."""
        with self._lock:
            rec = self._get_or_create(symbol)
            if rec.state != PositionState.IDLE:
                raise RuntimeError(
                    f"Cannot mark {symbol} PENDING_ENTRY from state {rec.state.value}"
                )
            rec.state = PositionState.PENDING_ENTRY
            logger.debug("OrderGuard: %s → PENDING_ENTRY", symbol)

    def mark_open(
        self,
        symbol: str,
        entry_order_id: str,
        sl_order_id: Optional[str],
        tp_order_id: Optional[str],
        direction: str,
        quantity: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,
    ) -> None:
        """Call after entry is confirmed filled and SL/TP orders are live."""
        with self._lock:
            rec = self._get_or_create(symbol)
            rec.state = PositionState.OPEN
            rec.entry_order_id = entry_order_id
            rec.sl_order_id = sl_order_id
            rec.tp_order_id = tp_order_id
            rec.direction = direction
            rec.quantity = quantity
            rec.entry_price = entry_price
            rec.sl_price = sl_price
            rec.tp_price = tp_price
            logger.info(
                "OrderGuard: %s → OPEN | dir=%s qty=%d entry=%.2f SL=%.2f TP=%.2f",
                symbol, direction, quantity, entry_price, sl_price, tp_price,
            )

    def mark_pending_exit(self, symbol: str, exit_order_id: str) -> None:
        """Call when placing a market exit order (square-off)."""
        with self._lock:
            rec = self._get_or_create(symbol)
            rec.state = PositionState.PENDING_EXIT
            rec.exit_order_id = exit_order_id
            logger.debug("OrderGuard: %s → PENDING_EXIT", symbol)

    def mark_idle(self, symbol: str) -> None:
        """Call when position is fully closed or entry failed. Resets to IDLE."""
        with self._lock:
            rec = self._get_or_create(symbol)
            old_state = rec.state
            # Reset the record
            self._records[symbol] = OrderRecord(symbol=symbol)
            logger.info("OrderGuard: %s → IDLE (from %s)", symbol, old_state.value)

    def update_sl_order(self, symbol: str, new_sl_order_id: Optional[str]) -> None:
        """Update SL order ID (e.g. after cancelling and re-placing SL)."""
        with self._lock:
            rec = self._get_or_create(symbol)
            rec.sl_order_id = new_sl_order_id

    def update_tp_order(self, symbol: str, new_tp_order_id: Optional[str]) -> None:
        """Update TP order ID."""
        with self._lock:
            rec = self._get_or_create(symbol)
            rec.tp_order_id = new_tp_order_id
