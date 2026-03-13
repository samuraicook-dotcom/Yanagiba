"""Agent 4: Execution Engine — Converts approved trades into orders."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from yanagiba.models.config import TradingConfig
from yanagiba.models.types import (
    Direction,
    ExecutionOrder,
    PortfolioState,
    RiskAssessment,
    TradeSignal,
)

logger = logging.getLogger(__name__)

# Minimum order quantities for common Binance Futures pairs
MIN_QTY = {
    "BTC/USDT": 0.001,
    "ETH/USDT": 0.001,
    "SOL/USDT": 0.01,
    "ARB/USDT": 0.1,
    "OP/USDT": 0.1,
    "DOGE/USDT": 1.0,
    "AVAX/USDT": 0.01,
    "LINK/USDT": 0.01,
    "POL/USDT": 0.1,
    "APT/USDT": 0.01,
    "GALA/USDT": 1.0,
    "IMX/USDT": 0.1,
    "AXS/USDT": 0.01,
    "SAND/USDT": 1.0,
    "MANA/USDT": 1.0,
    "ENJ/USDT": 0.1,
    "SUPER/USDT": 0.1,
    "YGG/USDT": 0.1,
}

# Minimum notional value in USDT
MIN_NOTIONAL = 5.0


@dataclass
class ExecutionPlan:
    order_type: str  # LIMIT, MARKET
    side: str  # buy, sell
    symbol: str
    entry: float
    stop: float
    take_profit_levels: list[float]
    position_size: float
    execution_priority: str  # HIGH, MEDIUM, LOW

    def to_dict(self) -> dict:
        return {
            "order_type": self.order_type,
            "side": self.side,
            "symbol": self.symbol,
            "entry": self.entry,
            "stop": self.stop,
            "take_profit_levels": self.take_profit_levels,
            "position_size": self.position_size,
            "execution_priority": self.execution_priority,
        }


class ExecutionEngine:
    """Converts approved signals into executable orders and manages execution."""

    def __init__(self, config: TradingConfig | None = None):
        self.config = config or TradingConfig()
        self.pending_orders: list[ExecutionPlan] = []
        self.executed_orders: list[dict[str, Any]] = []

    def create_execution_plan(
        self, signal: TradeSignal, risk: RiskAssessment, portfolio: PortfolioState
    ) -> ExecutionPlan:
        side = "buy" if signal.direction == Direction.LONG else "sell"

        # Position size: use leverage to get meaningful position from small account
        # adjusted_position_size can be > 1.0 (e.g. 3.0 = 3x leverage)
        position_value = portfolio.total_value * risk.adjusted_position_size
        if signal.entry <= 0:
            logger.warning(f"Invalid entry price {signal.entry} for {signal.asset}")
            return ExecutionPlan(
                order_type="LIMIT", side=side, symbol=signal.asset,
                entry=0, stop=0, take_profit_levels=[], position_size=0,
                execution_priority="LOW",
            )
        position_size = position_value / signal.entry

        # Enforce minimum quantity for the pair
        min_qty = MIN_QTY.get(signal.asset, 0.001)
        if 0 < position_size < min_qty:
            # Scale up to minimum if margin allows (check leverage limit)
            min_notional = min_qty * signal.entry
            required_margin = min_notional / self.config.max_leverage
            if required_margin <= portfolio.cash:
                position_size = min_qty
                logger.info(
                    f"Scaled position to minimum {min_qty} {signal.asset} "
                    f"(notional ${min_notional:.2f}, margin ~${required_margin:.2f})"
                )
            else:
                logger.warning(
                    f"Cannot meet minimum qty {min_qty} for {signal.asset}: "
                    f"need ${required_margin:.2f} margin, have ${portfolio.cash:.2f}"
                )

        # Take profit levels: split into partial TPs
        tp_levels = [signal.take_profit_1, signal.take_profit_2]

        # Priority based on confidence
        if signal.confidence_score >= 8:
            priority = "HIGH"
        elif signal.confidence_score >= 6:
            priority = "MEDIUM"
        else:
            priority = "LOW"

        # Round to exchange step size (use min_qty as step)
        step = MIN_QTY.get(signal.asset, 0.001)
        if step > 0 and position_size > 0:
            import math
            position_size = math.floor(position_size / step) * step
            # Ensure rounding didn't drop below minimum (with margin check)
            if position_size < step:
                min_notional = step * signal.entry
                required_margin = min_notional / self.config.max_leverage
                if required_margin <= portfolio.cash:
                    position_size = step
                else:
                    position_size = 0

        plan = ExecutionPlan(
            order_type="LIMIT",
            side=side,
            symbol=signal.asset,
            entry=signal.entry,
            stop=signal.stop_loss,
            take_profit_levels=tp_levels,
            position_size=round(position_size, 8),
            execution_priority=priority,
        )
        self.pending_orders.append(plan)
        return plan

    async def execute(self, plan: ExecutionPlan, exchange=None) -> ExecutionOrder:
        """Execute the plan on an exchange (or simulate in sandbox mode)."""
        order = ExecutionOrder(
            pair=plan.symbol,
            signal=plan.side.upper(),
            entry=plan.entry,
            stop_loss=plan.stop,
            take_profit=plan.take_profit_levels[0] if plan.take_profit_levels else plan.entry,
            confidence=0,
            position_size=plan.position_size,
        )

        if exchange and not self.config.sandbox:
            try:
                # Check minimum notional value
                notional = plan.position_size * plan.entry
                if notional < MIN_NOTIONAL:
                    order.status = f"skipped: notional ${notional:.2f} below ${MIN_NOTIONAL}"
                    logger.warning(f"Order too small: {notional:.2f} USDT (min ${MIN_NOTIONAL})")
                    return order

                # Check minimum quantity
                min_qty = MIN_QTY.get(plan.symbol, 0.001)
                if plan.position_size < min_qty:
                    order.status = f"skipped: qty {plan.position_size} below min {min_qty}"
                    logger.warning(
                        f"Order below min qty: {plan.position_size} < {min_qty} {plan.symbol}"
                    )
                    return order

                # Load markets if not loaded (needed for leverage + proper symbol resolution)
                if not exchange.markets:
                    await exchange.load_markets()

                # Resolve the futures symbol (e.g. BTC/USDT -> BTC/USDT:USDT)
                futures_symbol = plan.symbol
                swap_symbol = f"{plan.symbol}:USDT"
                if swap_symbol in exchange.markets:
                    futures_symbol = swap_symbol
                elif plan.symbol not in exchange.markets:
                    logger.warning(f"Symbol {plan.symbol} not found in exchange markets")

                # Set leverage — asset-specific caps (BTC=10x, ETH=15x)
                overrides = self.config.asset_overrides.get(plan.symbol, {})
                max_lev = overrides.get("max_leverage", self.config.max_leverage)
                leverage = int(min(max_lev, 20))
                try:
                    await exchange.set_leverage(leverage, futures_symbol)
                    logger.info(f"Leverage set to {leverage}x for {futures_symbol}")
                except Exception as e:
                    logger.warning(f"Could not set leverage (may already be set): {e}")

                # Place entry order — limit saves 60% on fees
                order_type = "limit" if self.config.use_limit_entry else "market"
                logger.info(
                    f"Placing {order_type.upper()} {plan.side.upper()} "
                    f"{plan.position_size} {plan.symbol} "
                    f"(notional ${notional:.2f}, leverage {leverage}x)"
                )
                order_params: dict = {}
                if order_type == "limit":
                    # Limit order at current price for maker fee
                    order_params["price"] = plan.entry
                result = await exchange.create_order(
                    symbol=futures_symbol,
                    type=order_type,
                    side=plan.side,
                    amount=plan.position_size,
                    **order_params,
                )
                order.status = "placed"
                fill = result.get("average", result.get("price", plan.entry))
                order.entry = float(fill or plan.entry)
                order_id = result.get("id", "unknown")
                logger.info(
                    f"ORDER PLACED: {order_id} | {plan.side.upper()} "
                    f"{plan.position_size} {plan.symbol} @ {order.entry}"
                )

                # CRITICAL: Place stop loss — if this fails, close position immediately
                sl_side = "sell" if plan.side == "buy" else "buy"
                sl_placed = False
                for attempt in range(3):
                    try:
                        await exchange.create_order(
                            symbol=futures_symbol,
                            type="stop_market",
                            side=sl_side,
                            amount=plan.position_size,
                            params={
                                "stopPrice": plan.stop,
                                "reduceOnly": True,
                            },
                        )
                        logger.info(f"Stop loss set at {plan.stop}")
                        sl_placed = True
                        break
                    except Exception as e:
                        logger.error(f"SL attempt {attempt + 1}/3 failed: {e}")
                        if attempt < 2:
                            await asyncio.sleep(1)

                if not sl_placed:
                    # EMERGENCY: Close position — no stop loss = unlimited risk
                    logger.error("EMERGENCY: Closing position — stop loss could not be placed!")
                    try:
                        await exchange.create_order(
                            symbol=futures_symbol, type="market",
                            side=sl_side, amount=plan.position_size,
                            params={"reduceOnly": True},
                        )
                        order.status = "closed: stop loss failed"
                    except Exception as e2:
                        logger.error(f"CRITICAL: Could not close position either: {e2}")
                        order.status = "error: no stop loss, close failed"
                    self.executed_orders.append(order.to_dict())
                    return order

                # Place take profits — weighted split (60% TP1, 40% TP2)
                tp_weights = [0.6, 0.4]
                for i, tp in enumerate(plan.take_profit_levels):
                    try:
                        n_tps = len(plan.take_profit_levels)
                        weight = tp_weights[i] if i < len(tp_weights) else 1.0 / n_tps
                        tp_size = round(plan.position_size * weight, 6)
                        tp_side = "sell" if plan.side == "buy" else "buy"
                        await exchange.create_order(
                            symbol=futures_symbol,
                            type="limit",
                            side=tp_side,
                            amount=tp_size,
                            price=tp,
                            params={"reduceOnly": True},
                        )
                        logger.info(f"Take profit set at {tp} (size {tp_size}, {weight:.0%})")
                    except Exception as e:
                        logger.error(f"Failed to set TP at {tp}: {e}")

                # Trailing stop: if enabled, cover only the TP2 portion (40%)
                # TP1 (60%) is handled by the limit TP order above
                if self.config.use_trailing_stop:
                    try:
                        activation_price = self._trailing_activation_price(
                            plan.side, order.entry,
                            self.config.trailing_stop_activation,
                        )
                        callback_rate = self.config.trailing_stop_callback * 100
                        # Only trail the TP2 portion (40%) to avoid conflict
                        trail_size = round(plan.position_size * 0.4, 6)
                        await exchange.create_order(
                            symbol=futures_symbol,
                            type="TRAILING_STOP_MARKET",
                            side=sl_side,
                            amount=trail_size,
                            params={
                                "activationPrice": activation_price,
                                "callbackRate": round(callback_rate, 1),
                                "reduceOnly": True,
                            },
                        )
                        logger.info(
                            f"Trailing stop set: {trail_size} units, "
                            f"activates at {activation_price:.2f}, "
                            f"callback {callback_rate:.1f}%"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Trailing stop not supported or failed: {e}"
                        )

            except Exception as e:
                order.status = f"error: {e}"
                logger.error(
                    f"EXECUTION FAILED: {plan.side.upper()} "
                    f"{plan.position_size} {plan.symbol} — {e}"
                )
        else:
            order.status = "simulated"
            logger.info(
                f"[PAPER] {plan.side.upper()} {plan.position_size} "
                f"{plan.symbol} @ {plan.entry}"
            )

        self.executed_orders.append(order.to_dict())
        return order

    @staticmethod
    def _trailing_activation_price(side: str, entry: float, activation_pct: float) -> float:
        """Calculate price at which trailing stop activates."""
        if side == "buy":
            return round(entry * (1 + activation_pct), 6)
        return round(entry * (1 - activation_pct), 6)

    async def cancel_stale_orders(self, exchange, max_age_seconds: int = 300) -> int:
        """Cancel unfilled limit orders older than max_age_seconds.

        Returns the number of orders cancelled.
        """
        if not exchange or self.config.sandbox:
            # In sandbox mode, just clear pending_orders list
            cleared = len(self.pending_orders)
            self.pending_orders.clear()
            return cleared

        cancelled = 0
        try:
            open_orders = await exchange.fetch_open_orders()
            now = exchange.milliseconds()
            for order in open_orders:
                created = order.get("timestamp", now)
                age_s = (now - created) / 1000
                if age_s > max_age_seconds and order.get("status") == "open":
                    try:
                        symbol = order.get("symbol", "")
                        order_id = order.get("id", "")
                        await exchange.cancel_order(order_id, symbol)
                        cancelled += 1
                        logger.info(
                            f"Cancelled stale order {order_id} ({symbol}, "
                            f"age={age_s:.0f}s)"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to cancel order {order.get('id')}: {e}")
        except Exception as e:
            logger.warning(f"Could not fetch open orders: {e}")

        self.pending_orders.clear()
        return cancelled

    async def cancel_orders_for_symbol(
        self, exchange, symbol: str,
    ) -> int:
        """Cancel ALL open orders for a specific symbol.

        Called when a position closes to clean up orphaned SL/TP orders.
        Returns the number of orders cancelled.
        """
        if not exchange or self.config.sandbox:
            return 0

        cancelled = 0
        try:
            futures_symbol = symbol
            swap_symbol = f"{symbol}:USDT"
            if exchange.markets and swap_symbol in exchange.markets:
                futures_symbol = swap_symbol

            open_orders = await exchange.fetch_open_orders(futures_symbol)
            for order in open_orders:
                try:
                    order_id = order.get("id", "")
                    order_type = order.get("type", "unknown")
                    await exchange.cancel_order(order_id, futures_symbol)
                    cancelled += 1
                    logger.info(
                        f"Cancelled orphaned {order_type} order "
                        f"{order_id} for {symbol}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to cancel order {order.get('id')} "
                        f"for {symbol}: {e}"
                    )
            if cancelled:
                logger.info(
                    f"Cleaned up {cancelled} orphaned orders for {symbol}"
                )
        except Exception as e:
            logger.warning(f"Could not fetch orders for {symbol}: {e}")
        return cancelled

    async def cancel_all_orphaned_orders(self, exchange, open_symbols: set[str]) -> int:
        """Cancel all open orders for symbols that have no open position.

        Called on startup to clean up orders left from previous runs.
        """
        if not exchange or self.config.sandbox:
            return 0

        cancelled = 0
        try:
            all_orders = await exchange.fetch_open_orders()
            symbols_to_cancel: set[str] = set()
            for order in all_orders:
                sym = order.get("symbol", "")
                # Normalize: BTC/USDT:USDT -> BTC/USDT
                base_sym = sym.replace(":USDT", "")
                if base_sym not in open_symbols and sym not in open_symbols:
                    symbols_to_cancel.add(sym)

            for sym in symbols_to_cancel:
                try:
                    orders = await exchange.fetch_open_orders(sym)
                    for order in orders:
                        try:
                            await exchange.cancel_order(order["id"], sym)
                            cancelled += 1
                        except Exception as e:
                            logger.warning(f"Cancel failed: {e}")
                except Exception as e:
                    logger.warning(f"Fetch orders for {sym} failed: {e}")

            if cancelled:
                logger.info(
                    f"Startup cleanup: cancelled {cancelled} orphaned "
                    f"orders across {len(symbols_to_cancel)} symbols"
                )
        except Exception as e:
            logger.warning(f"Orphaned order cleanup failed: {e}")
        return cancelled

    def clear_cycle_data(self):
        """Reset per-cycle order lists to prevent unbounded growth."""
        self.pending_orders.clear()
        # Keep only the last 50 executed orders for summary
        if len(self.executed_orders) > 50:
            self.executed_orders = self.executed_orders[-50:]

    def get_execution_summary(self) -> dict:
        return {
            "pending_orders": len(self.pending_orders),
            "executed_orders": len(self.executed_orders),
            "recent_executions": self.executed_orders[-5:],
        }
