"""Technical indicators for trading analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    # Guard: when both avg_gain and avg_loss are 0 (flat price), RSI = 50
    rs = np.where(avg_loss == 0, np.where(avg_gain == 0, 1.0, np.inf), avg_gain / avg_loss)
    return pd.Series(100 - (100 / (1 + rs)), index=series.index)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(
    series: pd.Series, period: int = 20, std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = series.rolling(window=period).mean()
    # Use population std (ddof=0) — standard for Bollinger Bands
    std = series.rolling(window=period).std(ddof=0)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def vwap(df: pd.DataFrame) -> pd.Series:
    """Calculate VWAP from OHLCV dataframe (expects 'high', 'low', 'close', 'volume')."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cumulative_tp_vol = (typical_price * df["volume"]).cumsum()
    cumulative_vol = df["volume"].cumsum()
    # Guard: avoid division by zero on zero-volume data
    return cumulative_tp_vol / cumulative_vol.replace(0, np.nan)


def volume_delta(df: pd.DataFrame) -> pd.Series:
    """Estimate volume delta: positive volume on up candles, negative on down.

    Doji candles (open == close) use high/low wick ratio to estimate direction.
    """
    diff = df["close"] - df["open"]
    # For doji candles: use (close - low) / (high - low) to estimate bias
    hl_range = df["high"] - df["low"]
    wick_ratio = np.where(
        hl_range > 0,
        (df["close"] - df["low"]) / hl_range * 2 - 1,  # -1 to +1
        0,
    )
    direction = np.where(diff != 0, np.sign(diff), wick_ratio)
    return df["volume"] * pd.Series(direction, index=df.index)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR using Wilder's smoothing (RMA) — more responsive than SMA."""
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    # Wilder's RMA = EWM with alpha=1/period (equivalent to span=2*period-1)
    return true_range.ewm(span=2 * period - 1, adjust=False).mean()


def compute_all(df: pd.DataFrame, config=None) -> pd.DataFrame:
    """Compute all indicators and add them as columns."""
    from yanagiba.models.config import TradingConfig

    cfg = config or TradingConfig()

    df = df.copy()
    df["rsi"] = rsi(df["close"], cfg.rsi_period)
    df["ema_fast"] = ema(df["close"], cfg.ema_fast)
    df["ema_mid"] = ema(df["close"], cfg.ema_mid)
    df["ema_slow"] = ema(df["close"], cfg.ema_slow)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(
        df["close"], cfg.macd_fast, cfg.macd_slow, cfg.macd_signal
    )
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = bollinger_bands(
        df["close"], cfg.bb_period, cfg.bb_std
    )
    df["vwap"] = vwap(df)
    df["volume_delta"] = volume_delta(df)
    df["atr"] = atr(df)
    return df
