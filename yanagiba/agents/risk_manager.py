"""Agent 3: Risk Manager — Evaluates and gates every trade.

Key protections:
- Daily loss limit (survive bad days)
- Portfolio exposure cap (don't over-leverage)
- Fee-adjusted R:R (account for Binance costs)
- Correlation guard (don't stack same-direction bets)
- Funding rate filter (don't fight the funding)
- Volatility-scaled position sizing
- Session hour filter (trade when volume is high)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from yanagiba.models.config import TradingConfig
from yanagiba.models.types import (
    Direction,
    PortfolioState,
    RiskAssessment,
    RiskLevel,
    TradeSignal,
)

logger = logging.getLogger(__name__)


class RiskManager:
    """Protects the portfolio by evaluating every trade before execution."""

    def __init__(self, config: TradingConfig | None = None):
        self.config = config or TradingConfig()
        # Track active trade directions for correlation guard
        self.active_trades: list[dict] = []

    def evaluate(
        self, signal: TradeSignal, portfolio: PortfolioState,
        volatility_level: float = 0.5,
        funding_rate: float | None = None,
    ) -> RiskAssessment:
        reasons: list[str] = []

        # Check daily loss limit
        if portfolio.daily_loss_pct >= self.config.daily_loss_limit:
            return self._reject(
                RiskLevel.HIGH, "Daily loss limit reached"
            )

        # Check portfolio exposure
        if portfolio.total_exposure_pct >= self.config.max_portfolio_risk:
            return self._reject(
                RiskLevel.HIGH,
                f"Portfolio exposure {portfolio.total_exposure_pct:.1%} "
                f"exceeds limit",
            )

        # Session hour filter — don't trade during dead hours
        if self.config.use_session_filter:
            if not self._is_active_session():
                return self._reject(
                    RiskLevel.LOW,
                    "Outside active trading hours (low volume)",
                )

        # Weekend filter — thin liquidity, wider spreads, gap risk
        is_weekend = self._is_weekend()
        if is_weekend and self.config.use_weekend_filter:
            # Don't fully block, just flag for position scaling later
            pass

        # Funding rate filter — don't fight the funding
        if funding_rate is not None:
            if (
                signal.direction == Direction.LONG
                and funding_rate > self.config.max_funding_rate_long
            ):
                return self._reject(
                    RiskLevel.MEDIUM,
                    f"Funding {funding_rate:.4%} too high for longs "
                    f"(crowded longs = reversal risk)",
                )
            if (
                signal.direction == Direction.SHORT
                and funding_rate < self.config.max_funding_rate_short
            ):
                return self._reject(
                    RiskLevel.MEDIUM,
                    f"Funding {funding_rate:.4%} too negative for shorts",
                )

        # Correlation guard — don't stack same-direction bets
        if not self._check_correlation(signal):
            return self._reject(
                RiskLevel.MEDIUM,
                f"Too many {signal.direction.value} trades on "
                f"correlated assets",
            )

        # Fee-adjusted R:R check
        # Round-trip fees at leverage eat into profits
        entry = signal.entry
        sl = signal.stop_loss
        tp1 = signal.take_profit_1
        # Entry fee depends on order type: limit (maker) saves 60%
        entry_fee = (
            self.config.maker_fee if self.config.use_limit_entry
            else self.config.taker_fee
        )
        exit_fee = self.config.taker_fee  # SL/TP exits are taker
        fee_cost = entry_fee + exit_fee
        sl_distance = abs(entry - sl)
        tp_distance = abs(tp1 - entry)

        # Actual R:R after fees (round-trip fees reduce profit AND increase loss)
        # You pay entry+exit fees whether you win or lose
        total_fee = entry * fee_cost
        effective_tp = tp_distance - total_fee
        effective_sl = sl_distance + total_fee
        fee_adjusted_rr = (
            effective_tp / effective_sl if effective_sl > 0 else 0
        )

        min_rr = self.config.min_risk_reward
        if fee_adjusted_rr < min_rr:
            return self._reject(
                RiskLevel.MEDIUM,
                f"Fee-adjusted R:R {fee_adjusted_rr:.2f} below "
                f"{min_rr} (raw R:R {signal.risk_reward})",
            )

        # Check confidence
        if signal.confidence_score < self.config.min_confidence:
            reasons.append(
                f"Confidence {signal.confidence_score} below "
                f"{self.config.min_confidence}"
            )
            return self._reject(RiskLevel.MEDIUM, "; ".join(reasons))

        # Position sizing — volatility-scaled
        vol_scale = max(0.3, 1.0 - volatility_level)
        risk_per_trade = self.config.max_risk_per_trade * vol_scale
        max_loss = portfolio.total_value * risk_per_trade

        sl_distance_pct = abs(entry - sl) / entry if entry > 0 else 1
        position_size_pct = (
            risk_per_trade / sl_distance_pct
            if sl_distance_pct > 0 else 0
        )

        # Asset-specific leverage cap (BTC=10x, ETH=15x, alts=20x)
        asset_overrides = self.config.asset_overrides.get(
            signal.asset, {}
        )
        max_lev = asset_overrides.get(
            "max_leverage", self.config.max_leverage
        )
        position_size_pct = min(position_size_pct, max_lev)

        # Cap margin usage
        margin_pct = position_size_pct / max_lev
        remaining_margin = (
            self.config.max_portfolio_risk - portfolio.total_exposure_pct
        )
        if margin_pct > remaining_margin:
            position_size_pct = remaining_margin * max_lev

        if position_size_pct <= 0:
            return self._reject(
                RiskLevel.HIGH, "No room for new positions"
            )

        # Enforce minimum notional ($5 on Binance Futures)
        # If vol scaling drops position below min, bump up to min
        min_notional = 5.0
        position_value = portfolio.total_value * position_size_pct
        if position_value < min_notional and portfolio.total_value > 0:
            min_size_pct = min_notional / portfolio.total_value
            min_margin = min_size_pct / max_lev
            if min_margin + portfolio.total_exposure_pct <= self.config.max_portfolio_risk:
                position_size_pct = min_size_pct
                logger.info(
                    f"Bumped position to min notional "
                    f"(${min_notional}): {position_size_pct:.4f}"
                )
            else:
                return self._reject(
                    RiskLevel.MEDIUM,
                    f"Position ${position_value:.2f} below "
                    f"${min_notional} min notional, no margin to bump",
                )

        # Weekend scaling — reduce position size on Sat/Sun
        # Re-check min notional AFTER scaling to avoid sub-$5 orders
        if is_weekend and self.config.use_weekend_filter:
            scaled = position_size_pct * self.config.weekend_position_scale
            scaled_value = portfolio.total_value * scaled
            if scaled_value >= min_notional:
                position_size_pct = scaled
                logger.info(
                    f"Weekend: scaled position to "
                    f"{self.config.weekend_position_scale:.0%}"
                )
            else:
                logger.info(
                    f"Weekend: skipping scale (would drop below "
                    f"${min_notional} min notional)"
                )

        # Recalculate max_loss to match actual position after all adjustments
        actual_value = portfolio.total_value * position_size_pct
        max_loss = actual_value * sl_distance_pct

        # Determine risk score
        risk_score = RiskLevel.LOW
        if fee_adjusted_rr < 2.5:
            risk_score = RiskLevel.MEDIUM
        if signal.confidence_score < 7:
            risk_score = RiskLevel.MEDIUM
        if sl_distance_pct > 0.03:
            risk_score = RiskLevel.HIGH

        # Track this trade for correlation guard
        self.active_trades.append({
            "asset": signal.asset,
            "direction": signal.direction,
        })

        return RiskAssessment(
            approved=True,
            adjusted_position_size=round(position_size_pct, 4),
            risk_score=risk_score,
            max_loss_amount=round(max_loss, 2),
            reason=(
                f"Trade approved (fee-adj R:R={fee_adjusted_rr:.2f}, "
                f"vol_scale={vol_scale:.1f})"
            ),
        )

    def clear_cycle_trades(self, open_positions=None):
        """Reset and repopulate from open positions for cross-cycle tracking."""
        self.active_trades.clear()
        if open_positions:
            for pos in open_positions:
                direction = (
                    Direction.LONG if pos.side == "buy"
                    else Direction.SHORT
                )
                self.active_trades.append({
                    "asset": pos.symbol,
                    "direction": direction,
                })

    def remove_closed_trade(self, asset: str):
        """Remove a trade from active tracking when it closes."""
        self.active_trades = [
            t for t in self.active_trades if t["asset"] != asset
        ]

    def _is_active_session(self) -> bool:
        """Check if current UTC hour is within active trading hours."""
        hour = datetime.now(timezone.utc).hour
        for start, end in self.config.active_hours_utc:
            if start <= hour < end:
                return True
        return False

    @staticmethod
    def _is_weekend() -> bool:
        """Check if it's Saturday or Sunday UTC."""
        return datetime.now(timezone.utc).weekday() >= 5

    def _check_correlation(self, signal: TradeSignal) -> bool:
        """Ensure we don't stack too many same-direction correlated bets.

        Uses risk weights: SOL (1.5x beta) counts as 1.5 positions,
        so BTC long + SOL long = 2.5 weighted positions (vs limit of 2).
        """
        weights = self.config.asset_risk_weights
        for group in self.config.correlation_groups:
            if signal.asset not in group:
                continue
            weighted_count = sum(
                weights.get(t["asset"], 1.0)
                for t in self.active_trades
                if t["asset"] in group
                and t["direction"] == signal.direction
            )
            # Add this signal's weight to check if it would exceed
            new_weight = weights.get(signal.asset, 1.0)
            if weighted_count + new_weight > self.config.max_correlated_trades:
                return False
        return True

    @staticmethod
    def _reject(level: RiskLevel, reason: str) -> RiskAssessment:
        return RiskAssessment(
            approved=False,
            adjusted_position_size=0,
            risk_score=level,
            max_loss_amount=0,
            reason=reason,
        )
