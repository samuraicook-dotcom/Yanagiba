"""Agent 4: Execution Engine — Converts approved trades into orders."""

from __future__ import annotations

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
        position_size = position_value / signal.entry if signal.entry > 0 else 0

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

        plan = ExecutionPlan(
            order_type="LIMIT",
            side=side,
            symbol=signal.asset,
            entry=signal.entry,
            stop=signal.stop_loss,
            take_profit_levels=tp_levels,
            position_size=round(position_size, 6),
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
                    logger.warning(f"Order below min qty: {plan.position_size} < {min_qty} {plan.symbol}")
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

                # Set leverage on the exchange before placing order
                leverage = int(min(self.config.max_leverage, 20))
                try:
                    await exchange.set_leverage(leverage, futures_symbol)
                    logger.info(f"Leverage set to {leverage}x for {futures_symbol}")
                except Exception as e:
                    logger.warning(f"Could not set leverage (may already be set): {e}")

                # Place market entry order
                logger.info(
                    f"Placing {plan.side.upper()} {plan.position_size} {plan.symbol} "
                    f"(notional ${notional:.2f}, leverage {leverage}x)"
                )
                result = await exchange.create_order(
                    symbol=futures_symbol,
                    type="market",
                    side=plan.side,
                    amount=plan.position_size,
                )
                order.status = "placed"
                order.entry = float(result.get("average", result.get("price", plan.entry)) or plan.entry)
                order_id = result.get("id", "unknown")
                logger.info(f"ORDER PLACED: {order_id} | {plan.side.upper()} {plan.position_size} {plan.symbol} @ {order.entry}")

                # Place stop loss as stop-market
                try:
                    sl_side = "sell" if plan.side == "buy" else "buy"
                    await exchange.create_order(
                        symbol=futures_symbol,
                        type="stop_market",
                        side=sl_side,
                        amount=plan.position_size,
                        params={"stopPrice": plan.stop},
                    )
                    logger.info(f"Stop loss set at {plan.stop}")
                except Exception as e:
                    logger.error(f"Failed to set stop loss: {e}")

                # Place take profits
                for tp in plan.take_profit_levels:
                    try:
                        tp_size = round(plan.position_size / len(plan.take_profit_levels), 6)
                        tp_side = "sell" if plan.side == "buy" else "buy"
                        await exchange.create_order(
                            symbol=futures_symbol,
                            type="limit",
                            side=tp_side,
                            amount=tp_size,
                            price=tp,
                            params={"reduceOnly": True},
                        )
                        logger.info(f"Take profit set at {tp} (size {tp_size})")
                    except Exception as e:
                        logger.error(f"Failed to set TP at {tp}: {e}")

            except Exception as e:
                order.status = f"error: {e}"
                logger.error(f"EXECUTION FAILED: {plan.side.upper()} {plan.position_size} {plan.symbol} — {e}")
        else:
            order.status = "simulated"
            logger.info(f"[PAPER] {plan.side.upper()} {plan.position_size} {plan.symbol} @ {plan.entry}")

        self.executed_orders.append(order.to_dict())
        return order

    def get_execution_summary(self) -> dict:
        return {
            "pending_orders": len(self.pending_orders),
            "executed_orders": len(self.executed_orders),
            "recent_executions": self.executed_orders[-5:],
        }
