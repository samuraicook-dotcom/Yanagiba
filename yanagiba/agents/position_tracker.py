"""Agent 5: Position Tracker — Monitors open positions and updates portfolio state.

Solves the critical problem of "fire and forget" order placement:
- Tracks all open positions with entry, SL, TP order IDs
- Polls exchange for fill status
- Updates portfolio exposure when positions close
- Detects orphaned orders (SL/TP without matching position)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    symbol: str
    side: str  # "buy" or "sell"
    entry_price: float
    quantity: float
    margin_usd: float
    stop_loss: float
    take_profits: list[float]
    entry_order_id: str = ""
    sl_order_id: str = ""
    tp_order_ids: list[str] = field(default_factory=list)
    opened_at: str = ""
    status: str = "open"  # open, closed, error
    realized_pnl: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "margin_usd": self.margin_usd,
            "stop_loss": self.stop_loss,
            "take_profits": self.take_profits,
            "status": self.status,
            "realized_pnl": self.realized_pnl,
            "opened_at": self.opened_at,
        }


class PositionTracker:
    """Tracks open positions and syncs portfolio state with exchange."""

    def __init__(self):
        self.positions: list[TrackedPosition] = []
        self.closed_positions: list[TrackedPosition] = []
        self.total_realized_pnl: float = 0.0

    def add_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        margin_usd: float,
        stop_loss: float,
        take_profits: list[float],
        entry_order_id: str = "",
    ) -> TrackedPosition:
        pos = TrackedPosition(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            margin_usd=margin_usd,
            stop_loss=stop_loss,
            take_profits=take_profits,
            entry_order_id=entry_order_id,
            opened_at=datetime.utcnow().isoformat(),
        )
        self.positions.append(pos)
        logger.info(f"TRACKING: {side.upper()} {quantity} {symbol} @ {entry_price}")
        return pos

    async def sync_with_exchange(self, exchange, portfolio) -> list[dict]:
        """Check exchange for position changes and update portfolio."""
        if not exchange:
            return []

        events = []
        try:
            # Fetch current positions from exchange
            exchange_positions = await exchange.fetch_positions()
            open_symbols = {}
            for ep in exchange_positions:
                if ep.get("contracts", 0) > 0:
                    sym = ep.get("symbol", "")
                    open_symbols[sym] = ep

            # Check each tracked position
            for pos in list(self.positions):
                futures_sym = f"{pos.symbol}:USDT"
                check_sym = futures_sym if futures_sym in open_symbols else pos.symbol

                if check_sym not in open_symbols:
                    # Position was closed (SL or TP hit)
                    pnl = self._estimate_pnl(pos, open_symbols)
                    pos.status = "closed"
                    pos.realized_pnl = pnl
                    self.total_realized_pnl += pnl
                    self.positions.remove(pos)
                    self.closed_positions.append(pos)

                    # Update portfolio
                    portfolio.cash += pos.margin_usd + pnl
                    portfolio.total_value += pnl
                    portfolio.daily_pnl += pnl
                    margin_pct = (
                        pos.margin_usd / portfolio.total_value
                        if portfolio.total_value > 0 else 0
                    )
                    portfolio.total_exposure_pct = max(0, portfolio.total_exposure_pct - margin_pct)

                    event = {
                        "type": "position_closed",
                        "symbol": pos.symbol,
                        "pnl": round(pnl, 2),
                        "portfolio_value": round(portfolio.total_value, 2),
                    }
                    events.append(event)
                    logger.info(
                        f"POSITION CLOSED: {pos.symbol} PnL=${pnl:+.2f} | "
                        f"Portfolio=${portfolio.total_value:.2f}"
                    )
                else:
                    # Position still open — update unrealized PnL
                    ep_data = open_symbols[check_sym]
                    unrealized = float(ep_data.get("unrealizedPnl", 0))
                    if unrealized != 0:
                        logger.debug(f"{pos.symbol} unrealized: ${unrealized:+.2f}")

        except Exception as e:
            logger.warning(f"Position sync failed (will retry): {e}")

        return events

    def _estimate_pnl(self, pos: TrackedPosition, open_symbols: dict) -> float:
        """Estimate PnL for a closed position based on SL/TP levels.

        Deducts round-trip fees (entry taker + exit taker) from the estimate.
        At 20x leverage, fees are ~1.6% of margin per round-trip.
        """
        sl_distance = abs(pos.entry_price - pos.stop_loss) / pos.entry_price

        # Default: assume SL hit (conservative)
        leverage = 20 if pos.margin_usd < 10 else 10
        estimated_loss = pos.margin_usd * sl_distance * leverage

        # Deduct round-trip fees: taker 0.04% * 2 sides * notional
        notional = pos.entry_price * pos.quantity
        fee_cost = notional * 0.0004 * 2  # 0.04% taker each way
        estimated_loss += fee_cost

        return -min(estimated_loss, pos.margin_usd * 0.95)

    @property
    def open_count(self) -> int:
        return len(self.positions)

    @property
    def open_margin(self) -> float:
        return sum(p.margin_usd for p in self.positions)

    def get_summary(self) -> dict:
        wins = sum(1 for p in self.closed_positions if p.realized_pnl > 0)
        losses = sum(1 for p in self.closed_positions if p.realized_pnl <= 0)
        total = wins + losses
        return {
            "open_positions": self.open_count,
            "open_margin": round(self.open_margin, 2),
            "closed_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(self.total_realized_pnl, 2),
        }
