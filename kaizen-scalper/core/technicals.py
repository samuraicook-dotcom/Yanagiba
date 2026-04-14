"""
core/technicals.py — Technical Indicators Engine

Pure pandas/numpy implementation. No TA-Lib dependency.
Computes EMA, RSI, VWAP, ATR, Volume SMA and spike flag on OHLCV DataFrames.
"""

import numpy as np
import pandas as pd


class TechnicalEngine:
    """
    Computes all required technical indicators on an OHLCV DataFrame.

    Expected input columns: datetime, open, high, low, close, volume
    """

    def __init__(self, config: dict) -> None:
        """
        Args:
            config: technicals section from settings.yaml
                    Keys: ema_fast, ema_slow, rsi_period, atr_period,
                          volume_spike_multiplier
        """
        self.ema_fast_period: int = config["ema_fast"]
        self.ema_slow_period: int = config["ema_slow"]
        self.rsi_period: int = config["rsi_period"]
        self.atr_period: int = config["atr_period"]
        self.volume_spike_multiplier: float = config["volume_spike_multiplier"]
        self.volume_sma_period: int = 20

    # ------------------------------------------------------------------
    # Individual indicator methods
    # ------------------------------------------------------------------

    def ema(self, series: pd.Series, period: int) -> pd.Series:
        """
        Exponential Moving Average.

        Args:
            series: Price series
            period: EMA lookback period

        Returns:
            EMA series
        """
        return series.ewm(span=period, adjust=False).mean()

    def rsi(self, series: pd.Series, period: int) -> pd.Series:
        """
        Relative Strength Index (Wilder's smoothing method).

        Args:
            series: Close price series
            period: RSI lookback period

        Returns:
            RSI series (0–100)
        """
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))
        return rsi_series.fillna(50)

    def vwap(self, df: pd.DataFrame) -> pd.Series:
        """
        Volume Weighted Average Price — resets at each new trading day.

        Args:
            df: OHLCV DataFrame with a 'datetime' column (or DatetimeIndex)

        Returns:
            VWAP series
        """
        df = df.copy()

        # Ensure datetime column is available as a column (not just index)
        if "datetime" in df.columns:
            dt_col = pd.to_datetime(df["datetime"])
        elif isinstance(df.index, pd.DatetimeIndex):
            dt_col = df.index
        else:
            raise ValueError("DataFrame must have a 'datetime' column or DatetimeIndex")

        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        tp_vol = typical_price * df["volume"]

        date_groups = dt_col.dt.date if hasattr(dt_col, "dt") else pd.Series(
            [d.date() for d in dt_col], index=df.index
        )

        vwap_values = pd.Series(index=df.index, dtype=float)
        for date, group_idx in df.groupby(date_groups).groups.items():
            cum_tp_vol = tp_vol.loc[group_idx].cumsum()
            cum_vol = df["volume"].loc[group_idx].cumsum()
            vwap_values.loc[group_idx] = cum_tp_vol / cum_vol.replace(0, np.nan)

        return vwap_values

    def atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        """
        Average True Range.

        Args:
            df: OHLCV DataFrame
            period: ATR lookback period

        Returns:
            ATR series
        """
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)

        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        return tr.ewm(alpha=1 / period, adjust=False).mean()

    def volume_sma(self, df: pd.DataFrame, period: int = 20) -> pd.Series:
        """
        Simple Moving Average of volume.

        Args:
            df: OHLCV DataFrame
            period: SMA lookback period

        Returns:
            Volume SMA series
        """
        return df["volume"].rolling(window=period, min_periods=1).mean()

    def volume_spike(self, df: pd.DataFrame) -> pd.Series:
        """
        Boolean series — True where volume > multiplier * volume SMA(20).

        Args:
            df: OHLCV DataFrame

        Returns:
            Boolean spike flag series
        """
        vol_sma = self.volume_sma(df, self.volume_sma_period)
        return df["volume"] > (self.volume_spike_multiplier * vol_sma)

    # ------------------------------------------------------------------
    # Master compute method
    # ------------------------------------------------------------------

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute all indicators and append as new columns to the DataFrame.

        Added columns:
            ema_fast, ema_slow, rsi, vwap, atr, volume_sma, volume_spike

        Args:
            df: OHLCV DataFrame (must have open, high, low, close, volume columns)

        Returns:
            DataFrame with indicator columns appended
        """
        df = df.copy()

        df["ema_fast"] = self.ema(df["close"], self.ema_fast_period)
        df["ema_slow"] = self.ema(df["close"], self.ema_slow_period)
        df["rsi"] = self.rsi(df["close"], self.rsi_period)
        df["vwap"] = self.vwap(df)
        df["atr"] = self.atr(df, self.atr_period)
        df["volume_sma"] = self.volume_sma(df, self.volume_sma_period)
        df["volume_spike"] = self.volume_spike(df)

        return df
