"""Core data types for the trading system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class MarketAnalysis:
    market_regime: MarketRegime
    sentiment_score: float  # -10 to +10
    volatility_level: float  # 0 to 1
    top_assets_to_watch: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "market_regime": self.market_regime.value,
            "sentiment_score": self.sentiment_score,
            "volatility_level": self.volatility_level,
            "top_assets_to_watch": self.top_assets_to_watch,
            "details": self.details,
        }


@dataclass
class TradeSignal:
    asset: str
    direction: Direction
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    confidence_score: float  # 1-10
    strategy: str = ""
    timeframe: str = ""

    def to_dict(self) -> dict:
        return {
            "asset": self.asset,
            "direction": self.direction.value,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "risk_reward": self.risk_reward,
            "confidence_score": self.confidence_score,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
        }


@dataclass
class RiskAssessment:
    approved: bool
    adjusted_position_size: float  # fraction of portfolio
    risk_score: RiskLevel
    max_loss_amount: float
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "adjusted_position_size": self.adjusted_position_size,
            "risk_score": self.risk_score.value,
            "max_loss_amount": self.max_loss_amount,
            "reason": self.reason,
        }


@dataclass
class ExecutionOrder:
    pair: str
    signal: str
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float
    position_size: float
    status: str = "pending"

    def to_dict(self) -> dict:
        return {
            "pair": self.pair,
            "signal": self.signal,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "confidence": self.confidence,
            "position_size": self.position_size,
            "status": self.status,
        }


@dataclass
class PortfolioState:
    total_value: float
    cash: float
    positions: dict[str, Any] = field(default_factory=dict)
    daily_pnl: float = 0.0
    daily_loss_pct: float = 0.0
    total_exposure_pct: float = 0.0
