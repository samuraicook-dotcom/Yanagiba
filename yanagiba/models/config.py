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

    # Risk limits (micro account ~$50) — aggressive mode
    max_portfolio_risk: float = 1.00  # 100% max margin exposure
    max_risk_per_trade: float = 0.25  # 25% risk per trade
    max_leverage: float = 5.0  # max 5x leverage
    daily_loss_limit: float = 0.05  # 5% daily loss cap ($2.50 on $50)
    scalp_stop_loss: float = 0.004  # 0.4% for scalping
    scalp_take_profit_min: float = 0.010  # 1.0% TP1 (2.5:1 R:R with 0.4% SL)
    scalp_take_profit_max: float = 0.022  # 2.2% TP2
    scalp_position_size: float = 0.08  # 8% of portfolio per scalp

    # R:R and confidence thresholds — lowered for more entries
    min_risk_reward: float = 1.2  # minimum R:R after fees (need ~45% win rate)

    # Indicator params
    rsi_period: int = 14
    rsi_long_threshold: float = 50.0  # at neutral (more entries)
    rsi_short_threshold: float = 50.0  # at neutral (more entries)
    ema_fast: int = 9  # faster EMA for scalping responsiveness
    ema_mid: int = 21
    ema_slow: int = 55  # shorter slow EMA — 200 is too laggy for micro account
    bb_period: int = 20
    bb_std: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Fees (Binance Futures USDT-M — these eat profits on small accounts)
    # Actual Binance non-VIP rates (verified March 2026)
    taker_fee: float = 0.0005  # 0.05% taker (market orders)
    maker_fee: float = 0.0002  # 0.02% maker (limit orders)
    use_limit_entry: bool = True  # use limit orders to save 60% on fees
    # Round-trip cost at taker: 0.05% * 2 = 0.10% of notional
    # At 20x leverage: 0.10% * 20 = 2% of margin per round-trip!
    # With limit entry: maker(0.02%) + taker(0.05%) = 0.07% = 1.4% of margin

    # Trading session filter — disabled for 24/7 trading
    active_hours_utc: list[tuple[int, int]] = field(
        default_factory=lambda: [(0, 24)]  # trade all hours
    )
    use_session_filter: bool = False  # trade around the clock
    weekend_position_scale: float = 0.75  # 75% size on weekends (was 50%)
    use_weekend_filter: bool = False  # trade weekends too

    # Asset-specific overrides (BTC moves differently than altcoins)
    # BTC: lower volatility per candle, needs wider SL but is more predictable
    # Altcoins: higher volatility, need tighter SL but more noise
    asset_overrides: dict = field(
        default_factory=lambda: {
            "BTC/USDT": {
                "scalp_stop_loss": 0.003,  # 0.3% (BTC is tighter)
                "scalp_take_profit_min": 0.008,  # 0.8% TP1 (lower for more fills)
                "scalp_take_profit_max": 0.016,  # 1.6% TP2
                "max_leverage": 5.0,  # BTC: up to 5x
            },
            "ETH/USDT": {
                "scalp_stop_loss": 0.004,  # 0.4%
                "scalp_take_profit_min": 0.010,  # 1.0% TP1
                "scalp_take_profit_max": 0.020,  # 2.0% TP2
                "max_leverage": 5.0,  # ETH: up to 5x
            },
        }
    )

    # Correlation guard — relaxed for more simultaneous trades
    max_correlated_trades: int = 3  # max 3 longs on BTC+ETH+SOL
    correlation_groups: list[list[str]] = field(
        default_factory=lambda: [
            ["BTC/USDT", "ETH/USDT", "SOL/USDT", "LINK/USDT"],
            ["ARB/USDT", "OP/USDT"],  # L2s move together
            ["DOGE/USDT", "POL/USDT"],  # meme/low-cap
        ]
    )

    # Risk weights for high-beta assets (SOL crashed -40% when BTC fell 15%)
    # Used in correlation guard to count high-beta assets as >1 position
    asset_risk_weights: dict = field(
        default_factory=lambda: {
            "SOL/USDT": 1.5,  # SOL = 1.5x BTC beta
            "DOGE/USDT": 1.5,  # memecoins are high-beta
            "ARB/USDT": 1.3,
            "OP/USDT": 1.3,
        }
    )

    # Funding rate filter — avoid entering against funding
    max_funding_rate_long: float = 0.0003  # skip longs if funding > 0.03%
    max_funding_rate_short: float = -0.0003  # skip shorts if funding < -0.03%

    # Execution
    use_trailing_stop: bool = True
    trailing_stop_activation: float = 0.008  # activate after 0.8% profit
    trailing_stop_callback: float = 0.003  # trail by 0.3%
    min_confidence: float = 3.5  # lowered to allow more trades

    # RSS feeds & NLP sentiment
    enable_rss_feeds: bool = True  # scrape CoinDesk, CoinTelegraph, Decrypt, etc.
    rss_max_age_hours: int = 4  # ignore headlines older than this

    # On-chain / order flow data providers (all free, no API keys)
    enable_open_interest: bool = True  # OI tracking via ccxt
    enable_liquidation_stream: bool = True  # Binance+Bybit liquidation WebSocket
    enable_volume_flow: bool = True  # Real volume delta from trades
    enable_mempool_monitor: bool = True  # BTC mempool whale monitoring
    liq_cascade_threshold_usd: float = 100000  # USD threshold for cascade detection
    liq_window_seconds: int = 300  # Liquidation aggregation window (5 min)
    volume_flow_large_threshold: float = 10000  # USD threshold for "large" trade
    mempool_whale_threshold_btc: float = 10.0  # BTC threshold for whale tx
