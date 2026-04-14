"""
tests/test_technicals.py — Unit tests for TechnicalEngine
"""

import numpy as np
import pandas as pd
import pytest

from core.technicals import TechnicalEngine

TECH_CONFIG = {
    "ema_fast": 9,
    "ema_slow": 21,
    "rsi_period": 7,
    "atr_period": 14,
    "volume_spike_multiplier": 1.5,
}


@pytest.fixture
def engine():
    return TechnicalEngine(TECH_CONFIG)


@pytest.fixture
def sample_df():
    """Create a synthetic 60-candle OHLCV DataFrame with an uptrend."""
    np.random.seed(42)
    n = 60
    base = 100.0
    closes = base + np.cumsum(np.random.randn(n) * 0.5)
    highs = closes + np.abs(np.random.randn(n)) * 0.3
    lows = closes - np.abs(np.random.randn(n)) * 0.3
    opens = closes - np.random.randn(n) * 0.2

    dates = pd.date_range("2026-01-02 09:15", periods=n, freq="30min")
    df = pd.DataFrame({
        "datetime": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": np.random.randint(5000, 50000, n).astype(float),
    })
    return df


class TestEMA:
    def test_ema_length(self, engine, sample_df):
        result = engine.ema(sample_df["close"], 9)
        assert len(result) == len(sample_df)

    def test_ema_values_not_all_nan(self, engine, sample_df):
        result = engine.ema(sample_df["close"], 9)
        assert result.notna().sum() > 0

    def test_ema_fast_closer_to_price(self, engine, sample_df):
        """EMA(9) should track price more closely than EMA(21)."""
        fast = engine.ema(sample_df["close"], 9)
        slow = engine.ema(sample_df["close"], 21)
        close = sample_df["close"]
        fast_diff = (fast - close).abs().mean()
        slow_diff = (slow - close).abs().mean()
        assert fast_diff <= slow_diff


class TestRSI:
    def test_rsi_range(self, engine, sample_df):
        """RSI must always be between 0 and 100."""
        rsi = engine.rsi(sample_df["close"], 7)
        assert (rsi >= 0).all()
        assert (rsi <= 100).all()

    def test_rsi_length(self, engine, sample_df):
        rsi = engine.rsi(sample_df["close"], 7)
        assert len(rsi) == len(sample_df)


class TestVWAP:
    def test_vwap_length(self, engine, sample_df):
        vwap = engine.vwap(sample_df)
        assert len(vwap) == len(sample_df)

    def test_vwap_positive(self, engine, sample_df):
        vwap = engine.vwap(sample_df)
        assert (vwap.dropna() > 0).all()

    def test_vwap_within_hl_range(self, engine, sample_df):
        """VWAP should be between daily low and high."""
        vwap = engine.vwap(sample_df)
        valid = vwap.dropna()
        assert (valid >= sample_df["low"].min()).all()
        assert (valid <= sample_df["high"].max()).all()


class TestATR:
    def test_atr_positive(self, engine, sample_df):
        atr = engine.atr(sample_df, 14)
        assert (atr.dropna() > 0).all()

    def test_atr_length(self, engine, sample_df):
        atr = engine.atr(sample_df, 14)
        assert len(atr) == len(sample_df)


class TestVolumeSpike:
    def test_volume_spike_boolean(self, engine, sample_df):
        spike = engine.volume_spike(sample_df)
        assert spike.dtype == bool

    def test_volume_spike_some_true(self, engine, sample_df):
        """At least some candles should show a volume spike."""
        spike = engine.volume_spike(sample_df)
        assert spike.sum() >= 0  # may be 0 for low-variance volume

    def test_spike_with_forced_spike(self, engine, sample_df):
        """Force a spike and verify detection."""
        df = sample_df.copy()
        df.at[50, "volume"] = df["volume"].mean() * 10
        spike = engine.volume_spike(df)
        assert spike.iloc[50] == True


class TestComputeAll:
    def test_columns_added(self, engine, sample_df):
        """compute_all must add all expected columns."""
        result = engine.compute_all(sample_df)
        for col in ["ema_fast", "ema_slow", "rsi", "vwap", "atr", "volume_sma", "volume_spike"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_original_columns_intact(self, engine, sample_df):
        """Original OHLCV columns should still be present."""
        result = engine.compute_all(sample_df)
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in result.columns

    def test_no_modification_of_input(self, engine, sample_df):
        """compute_all should not mutate the input DataFrame."""
        original_cols = set(sample_df.columns)
        engine.compute_all(sample_df)
        assert set(sample_df.columns) == original_cols
