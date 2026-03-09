"""Agent 3: Risk Manager — Evaluates and gates every trade."""

from __future__ import annotations

from yanagiba.models.config import TradingConfig
from yanagiba.models.types import (
    PortfolioState,
    RiskAssessment,
    RiskLevel,
    TradeSignal,
)


class RiskManager:
    """Protects the portfolio by evaluating every trade before execution."""

    def __init__(self, config: TradingConfig | None = None):
        self.config = config or TradingConfig()

    def evaluate(
        self, signal: TradeSignal, portfolio: PortfolioState
    ) -> RiskAssessment:
        reasons: list[str] = []

        # Check daily loss limit
        if portfolio.daily_loss_pct >= self.config.daily_loss_limit:
            return RiskAssessment(
                approved=False,
                adjusted_position_size=0,
                risk_score=RiskLevel.HIGH,
                max_loss_amount=0,
                reason="Daily loss limit reached",
            )

        # Check portfolio exposure
        if portfolio.total_exposure_pct >= self.config.max_portfolio_risk:
            return RiskAssessment(
                approved=False,
                adjusted_position_size=0,
                risk_score=RiskLevel.HIGH,
                max_loss_amount=0,
                reason=f"Portfolio exposure {portfolio.total_exposure_pct:.1%} exceeds limit",
            )

        # Check risk/reward (lowered for aggressive mode)
        if signal.risk_reward < 1.3:
            reasons.append(f"R:R {signal.risk_reward} below 1.3 threshold")
            return RiskAssessment(
                approved=False,
                adjusted_position_size=0,
                risk_score=RiskLevel.MEDIUM,
                max_loss_amount=0,
                reason="; ".join(reasons),
            )

        # Check confidence
        if signal.confidence_score < self.config.min_confidence:
            reasons.append(f"Confidence {signal.confidence_score} below {self.config.min_confidence}")
            return RiskAssessment(
                approved=False,
                adjusted_position_size=0,
                risk_score=RiskLevel.MEDIUM,
                max_loss_amount=0,
                reason="; ".join(reasons),
            )

        # Calculate position size (as multiple of portfolio — >1.0 means leveraged)
        risk_per_trade = self.config.max_risk_per_trade
        max_loss = portfolio.total_value * risk_per_trade

        entry = signal.entry
        sl = signal.stop_loss
        sl_distance_pct = abs(entry - sl) / entry if entry > 0 else 1
        position_size_pct = risk_per_trade / sl_distance_pct if sl_distance_pct > 0 else 0

        # Cap at max leverage
        position_size_pct = min(position_size_pct, self.config.max_leverage)

        # Cap margin usage: each position uses (position_size_pct / leverage) of portfolio as margin
        # Exposure tracks margin used, not notional — so leveraged positions still fit
        leverage = min(position_size_pct, self.config.max_leverage) if position_size_pct > 1 else 1.0
        margin_pct = position_size_pct / leverage if leverage > 0 else position_size_pct
        remaining_margin = self.config.max_portfolio_risk - portfolio.total_exposure_pct
        if margin_pct > remaining_margin:
            # Scale down position to fit remaining margin
            position_size_pct = remaining_margin * leverage

        if position_size_pct <= 0:
            return RiskAssessment(
                approved=False,
                adjusted_position_size=0,
                risk_score=RiskLevel.HIGH,
                max_loss_amount=0,
                reason="No room for new positions",
            )

        # Determine risk score
        risk_score = RiskLevel.LOW
        if signal.risk_reward < 2.5:
            risk_score = RiskLevel.MEDIUM
        if signal.confidence_score < 7:
            risk_score = RiskLevel.MEDIUM
        if sl_distance_pct > 0.03:
            risk_score = RiskLevel.HIGH

        return RiskAssessment(
            approved=True,
            adjusted_position_size=round(position_size_pct, 4),
            risk_score=risk_score,
            max_loss_amount=round(max_loss, 2),
            reason="Trade approved",
        )
