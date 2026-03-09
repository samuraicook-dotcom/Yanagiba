"""Agent 4: Execution Engine — Converts approved trades into orders."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
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

        # Position size in base currency value
        position_value = portfolio.total_value * risk.adjusted_position_size
        position_size = position_value / signal.entry if signal.entry > 0 else 0

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
                # Check minimum notional value ($5 on Binance Futures)
                notional = plan.position_size * plan.entry
                if notional < 5.0:
                    order.status = "skipped: below minimum notional ($5)"
                    logger.warning(f"Order too small: {notional:.2f} USDT (min $5)")
                    return order

                result = await exchange.create_order(
                    symbol=plan.symbol,
                    type="market",
                    side=plan.side,
                    amount=plan.position_size,
                )
                order.status = "placed"
                order.entry = float(result.get("average", plan.entry))
                logger.info(f"Order placed: {result.get('id', 'unknown')} @ {order.entry}")

                # Place stop loss as stop-market
                await exchange.create_order(
                    symbol=plan.symbol,
                    type="stop_market",
                    side="sell" if plan.side == "buy" else "buy",
                    amount=plan.position_size,
                    params={"stopPrice": plan.stop},
                )

                # Place take profits
                for tp in plan.take_profit_levels:
                    tp_size = plan.position_size / len(plan.take_profit_levels)
                    await exchange.create_order(
                        symbol=plan.symbol,
                        type="limit",
                        side="sell" if plan.side == "buy" else "buy",
                        amount=tp_size,
                        price=tp,
                    )
            except Exception as e:
                order.status = f"error: {e}"
                logger.error(f"Execution error: {e}")
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
