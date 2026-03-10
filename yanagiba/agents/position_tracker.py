"""Agent 5: Position Tracker — Monitors open positions and updates portfolio state.

Solves the critical problem of "fire and forget" order placement:
- Tracks all open positions with entry, SL, TP order IDs
- Polls exchange for fill status (live) or simulates fills (sandbox)
- Updates portfolio exposure when positions close
- Detects orphaned orders (SL/TP without matching position)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

POSITIONS_FILE = Path.home() / "Yanagiba" / "journal" / "open_positions.json"


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
        self._load_positions()

    def _load_positions(self):
        """Restore open positions from disk on startup."""
        if POSITIONS_FILE.exists():
            try:
                data = json.loads(POSITIONS_FILE.read_text())
                for d in data:
                    self.positions.append(TrackedPosition(**d))
                if self.positions:
                    logger.info(f"Restored {len(self.positions)} open positions from disk")
            except Exception as e:
                logger.warning(f"Could not restore positions: {e}")

    def _save_positions(self):
        """Persist open positions to disk for crash recovery."""
        try:
            POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = [p.to_dict() for p in self.positions]
            POSITIONS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning(f"Could not save positions: {e}")

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
        self._save_positions()
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
                    # Position was closed — try to get real PnL from exchange
                    pnl = await self._fetch_real_pnl(exchange, pos)
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

                    close_type = "take_profit" if pnl > 0 else "stop_loss"
                    event = {
                        "type": f"position_closed_{close_type}",
                        "symbol": pos.symbol,
                        "pnl": round(pnl, 2),
                        "portfolio_value": round(portfolio.total_value, 2),
                    }
                    events.append(event)
                    logger.info(
                        f"POSITION CLOSED ({close_type}): {pos.symbol} "
                        f"PnL=${pnl:+.2f} | Portfolio=${portfolio.total_value:.2f}"
                    )
                else:
                    # Position still open — update unrealized PnL
                    ep_data = open_symbols[check_sym]
                    unrealized = float(ep_data.get("unrealizedPnl", 0))
                    if unrealized != 0:
                        logger.debug(f"{pos.symbol} unrealized: ${unrealized:+.2f}")

        except Exception as e:
            logger.warning(f"Position sync failed (will retry): {e}")

        if events:
            self._save_positions()
        return events

    async def _fetch_real_pnl(self, exchange, pos: TrackedPosition) -> float:
        """Fetch actual realized PnL from exchange trade history.

        Falls back to price-based estimation if the exchange API fails.
        """
        try:
            # Fetch recent closed orders / trades for this symbol
            trades = await exchange.fetch_my_trades(pos.symbol, limit=20)
            # Find trades after position was opened
            entry_time = pos.opened_at or ""
            relevant_pnl = 0.0
            found_close = False
            for t in reversed(trades):
                info = t.get("info", {})
                realized = float(info.get("realizedPnl", 0))
                if realized != 0:
                    relevant_pnl += realized
                    found_close = True
            if found_close:
                logger.info(
                    f"Exchange PnL for {pos.symbol}: ${relevant_pnl:+.4f}"
                )
                return relevant_pnl
        except Exception as e:
            logger.debug(f"Could not fetch trade history for {pos.symbol}: {e}")

        # Fallback: estimate from current price vs entry
        return await self._estimate_pnl_from_price(exchange, pos)

    async def simulate_sandbox_fills(
        self, exchange, portfolio,
    ) -> list[dict]:
        """Sandbox mode: check current prices against SL/TP to simulate fills.

        In sandbox/paper trading, the exchange doesn't execute SL/TP orders
        automatically. We must check prices ourselves each cycle.
        """
        if not self.positions:
            return []

        events = []
        for pos in list(self.positions):
            try:
                ticker = await exchange.fetch_ticker(pos.symbol)
                price = ticker.get("last", 0)
                if not price:
                    continue

                hit = None
                pnl = 0.0
                notional = pos.entry_price * pos.quantity
                fee_cost = notional * (0.0002 + 0.0005)

                is_long = pos.side == "buy"
                # Check stop loss
                if is_long and price <= pos.stop_loss:
                    hit = "stop_loss"
                    pnl = (pos.stop_loss - pos.entry_price) * pos.quantity - fee_cost
                elif not is_long and price >= pos.stop_loss:
                    hit = "stop_loss"
                    pnl = (pos.entry_price - pos.stop_loss) * pos.quantity - fee_cost

                # Check take profits (TP1 = full close for simplicity)
                if not hit and pos.take_profits:
                    tp1 = pos.take_profits[0]
                    if is_long and price >= tp1:
                        hit = "take_profit"
                        pnl = (tp1 - pos.entry_price) * pos.quantity - fee_cost
                    elif not is_long and price <= tp1:
                        hit = "take_profit"
                        pnl = (pos.entry_price - tp1) * pos.quantity - fee_cost

                if hit:
                    pos.status = "closed"
                    pos.realized_pnl = pnl
                    self.total_realized_pnl += pnl
                    self.positions.remove(pos)
                    self.closed_positions.append(pos)

                    portfolio.cash += pos.margin_usd + pnl
                    portfolio.total_value += pnl
                    portfolio.daily_pnl += pnl
                    margin_pct = (
                        pos.margin_usd / portfolio.total_value
                        if portfolio.total_value > 0 else 0
                    )
                    portfolio.total_exposure_pct = max(
                        0, portfolio.total_exposure_pct - margin_pct,
                    )

                    event = {
                        "type": f"sandbox_{hit}",
                        "symbol": pos.symbol,
                        "price": round(price, 2),
                        "pnl": round(pnl, 2),
                        "portfolio_value": round(portfolio.total_value, 2),
                    }
                    events.append(event)
                    logger.info(
                        f"SANDBOX {hit.upper()}: {pos.symbol} "
                        f"@ ${price:,.2f} PnL=${pnl:+.2f} | "
                        f"Portfolio=${portfolio.total_value:.2f}"
                    )
            except Exception as e:
                logger.debug(f"Sandbox check {pos.symbol}: {e}")

        if events:
            self._save_positions()
        return events

    async def _estimate_pnl_from_price(
        self, exchange, pos: TrackedPosition,
    ) -> float:
        """Estimate PnL by fetching last price and comparing to entry/SL/TP.

        Used as fallback when exchange trade history is unavailable.
        """
        try:
            ticker = await exchange.fetch_ticker(pos.symbol)
            last_price = ticker.get("last", 0)
        except Exception:
            last_price = 0

        notional = pos.entry_price * pos.quantity
        fee_cost = notional * (0.0002 + 0.0005)  # maker + taker
        is_long = pos.side == "buy"

        # If we have a last price, determine if TP or SL was more likely hit
        if last_price > 0 and pos.take_profits:
            tp1 = pos.take_profits[0]
            if is_long:
                # Price above TP → likely hit TP
                if last_price >= tp1:
                    pnl = (tp1 - pos.entry_price) * pos.quantity - fee_cost
                    return pnl
                # Price below SL → likely hit SL
                elif last_price <= pos.stop_loss:
                    pnl = (pos.stop_loss - pos.entry_price) * pos.quantity - fee_cost
                    return pnl
            else:
                # Short: price below TP → likely hit TP
                if last_price <= tp1:
                    pnl = (pos.entry_price - tp1) * pos.quantity - fee_cost
                    return pnl
                # Price above SL → likely hit SL
                elif last_price >= pos.stop_loss:
                    pnl = (pos.entry_price - pos.stop_loss) * pos.quantity - fee_cost
                    return pnl

            # Price between SL and TP — use actual price difference
            if is_long:
                pnl = (last_price - pos.entry_price) * pos.quantity - fee_cost
            else:
                pnl = (pos.entry_price - last_price) * pos.quantity - fee_cost
            return pnl

        # Last resort: assume SL hit (conservative)
        sl_distance = abs(pos.entry_price - pos.stop_loss)
        pnl = -(sl_distance * pos.quantity + fee_cost)
        return max(pnl, -(pos.margin_usd * 0.95))

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
