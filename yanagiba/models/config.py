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

    # Risk limits (micro account ~$50)
    max_portfolio_risk: float = 0.30  # 30% max margin exposure (conservative)
    max_risk_per_trade: float = 0.01  # 1% risk per trade ($0.50 on $50 — survive 30+ losses)
    max_leverage: float = 3.0  # max 3x leverage (safe for micro accounts)
    daily_loss_limit: float = 0.03  # 3% daily loss cap ($1.50 on $50)
    scalp_stop_loss: float = 0.004  # 0.4% for scalping (tighter = more trades survive)
    scalp_take_profit_min: float = 0.012  # 1.2% TP1 (3:1 R:R with 0.4% SL)
    scalp_take_profit_max: float = 0.025  # 2.5% TP2
    scalp_position_size: float = 0.05  # 5% of portfolio per scalp

    # R:R and confidence thresholds
    min_risk_reward: float = 1.8  # minimum R:R to take a trade (need ~36% win rate)

    # Indicator params
    rsi_period: int = 14
    rsi_long_threshold: float = 53.0  # slightly above neutral
    rsi_short_threshold: float = 47.0  # slightly below neutral
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

    # Trading session filter (UTC hours — trade when volume is highest)
    # Best BTC hours: US+EU overlap (13:00-17:00 UTC), Asia open (00:00-03:00)
    active_hours_utc: list[tuple[int, int]] = field(
        default_factory=lambda: [(0, 4), (8, 21)]
    )
    use_session_filter: bool = True  # skip low-volume hours
    weekend_position_scale: float = 0.5  # halve position size on weekends
    use_weekend_filter: bool = True  # reduce risk on Sat/Sun (thin liquidity)

    # Asset-specific overrides (BTC moves differently than altcoins)
    # BTC: lower volatility per candle, needs wider SL but is more predictable
    # Altcoins: higher volatility, need tighter SL but more noise
    asset_overrides: dict = field(
        default_factory=lambda: {
            "BTC/USDT": {
                "scalp_stop_loss": 0.003,  # 0.3% (BTC is tighter)
                "scalp_take_profit_min": 0.009,  # 0.9% TP1
                "scalp_take_profit_max": 0.018,  # 1.8% TP2
                "max_leverage": 3.0,  # BTC: conservative leverage
            },
            "ETH/USDT": {
                "scalp_stop_loss": 0.004,  # 0.4%
                "scalp_take_profit_min": 0.012,
                "scalp_take_profit_max": 0.024,
                "max_leverage": 3.0,  # ETH: conservative leverage
            },
        }
    )

    # Correlation guard — max same-direction trades on correlated assets
    max_correlated_trades: int = 2  # max 2 longs on BTC+ETH+SOL
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
    min_confidence: float = 5.0  # require decent confidence to trade

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
