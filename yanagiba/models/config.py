"""Trading system configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TradingConfig:
    # Exchange
    exchange: str = "binance"
    sandbox: bool = True  # paper trading by default
    api_key: str = ""
    api_secret: str = ""

    # Market type
    market_type: str = "future"  # "spot" or "future"

    # Trade universe (supports any pairs available on the exchange)
    assets: list[str] = field(
        default_factory=lambda: [
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "ARB/USDT", "OP/USDT",
            "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "POL/USDT", "APT/USDT",
        ]
    )

    # Gaming / narrative tokens (GTA 6 launch hype, gaming sector)
    gaming_tokens: list[str] = field(
        default_factory=lambda: [
            "GALA/USDT", "IMX/USDT", "AXS/USDT", "SAND/USDT",
            "MANA/USDT", "ENJ/USDT", "SUPER/USDT", "YGG/USDT",
        ]
    )
    enable_gaming_sector: bool = True

    # Timeframes
    timeframes: list[str] = field(
        default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "1d"]
    )
    scalp_timeframes: list[str] = field(
        default_factory=lambda: ["1m", "3m", "5m"]
    )

    # Risk limits (aggressive mode for small account growth)
    max_portfolio_risk: float = 0.30  # 30% max exposure
    max_risk_per_trade: float = 0.03  # 3% risk per trade
    max_leverage: float = 10.0  # up to 10x leverage
    daily_loss_limit: float = 0.08  # 8% daily loss cap
    scalp_stop_loss: float = 0.008  # 0.8% for scalping
    scalp_take_profit_min: float = 0.02  # 2% TP1
    scalp_take_profit_max: float = 0.04  # 4% TP2
    scalp_position_size: float = 0.02  # 2% of portfolio per scalp

    # Indicator params
    rsi_period: int = 14
    rsi_long_threshold: float = 52.0
    rsi_short_threshold: float = 48.0
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    bb_period: int = 20
    bb_std: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Execution
    use_trailing_stop: bool = True
    min_confidence: float = 5.0  # lower bar = more trades taken
