"""
tests/test_signals.py — Unit tests for SignalEngine
"""

import numpy as np
import pandas as pd
import pytest

from core.signals import (
    ACTION_HOLD,
    ACTION_LONG,
    ACTION_SHORT,
    BIAS_BEARISH,
    BIAS_BULLISH,
    BIAS_NEUTRAL,
    SIGNAL_BUY,
    SIGNAL_NONE,
    SIGNAL_SELL,
    SignalEngine,
)
from core.technicals import TechnicalEngine

TECH_CONFIG = {
    "ema_fast": 9,
    "ema_slow": 21,
    "rsi_period": 7,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "atr_period": 14,
    "volume_spike_multiplier": 1.5,
}


@pytest.fixture
def signal_engine():
    return SignalEngine(TECH_CONFIG)


@pytest.fixture
def tech_engine():
    return TechnicalEngine(TECH_CONFIG)


def _make_df_with_ema_cross(cross_up: bool = True, n: int = 60) -> pd.DataFrame:
    """
    Build a DataFrame that will produce an EMA crossover.
    cross_up=True → fast crosses above slow (BUY condition).
    cross_up=False → fast crosses below slow (SELL condition).
    """
    np.random.seed(0)
    dates = pd.date_range("2026-01-02 09:15", periods=n, freq="30min")

    if cross_up:
        # Downtrend then sharp upturn to trigger bullish cross
        closes = np.concatenate([
            np.linspace(110, 100, n - 5),   # downtrend: fast stays below slow
            np.linspace(100, 115, 5),         # sharp up: fast crosses above slow
        ])
    else:
        closes = np.concatenate([
            np.linspace(100, 110, n - 5),
            np.linspace(110, 95, 5),
        ])

    highs = closes + 0.5
    lows = closes - 0.5
    # Force volume spike on last candle
    volumes = np.ones(n) * 10000
    volumes[-1] = 30000  # spike

    df = pd.DataFrame({
        "datetime": dates,
        "open": closes - 0.1,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    return df


class TestTechnicalSignal:
    def test_no_signal_insufficient_data(self, signal_engine):
        """Signal is NONE when fewer than 3 rows."""
        df = pd.DataFrame({"ema_fast": [1], "ema_slow": [1], "rsi": [50],
                           "vwap": [1], "volume_spike": [True], "close": [1]})
        result = signal_engine.generate_technical_signal(df)
        assert result["signal"] == SIGNAL_NONE

    def test_hold_when_no_cross(self, signal_engine, tech_engine):
        """No signal emitted when EMA cross didn't happen recently."""
        np.random.seed(1)
        n = 60
        dates = pd.date_range("2026-01-02 09:15", periods=n, freq="30min")
        # Flat price — no cross
        closes = np.ones(n) * 100.0
        df = pd.DataFrame({
            "datetime": dates,
            "open": closes, "high": closes + 0.1, "low": closes - 0.1,
            "close": closes, "volume": np.ones(n) * 10000,
        })
        df = tech_engine.compute_all(df)
        result = signal_engine.generate_technical_signal(df)
        assert result["signal"] == SIGNAL_NONE

    def test_result_has_required_keys(self, signal_engine, tech_engine):
        df = _make_df_with_ema_cross(cross_up=True)
        df = tech_engine.compute_all(df)
        result = signal_engine.generate_technical_signal(df)
        assert "signal" in result
        assert "reason" in result
        assert "conditions" in result


class TestCombineWithSentiment:
    def test_buy_bullish_is_long_full_size(self, signal_engine):
        tech = {"signal": SIGNAL_BUY, "reason": "test"}
        decision = signal_engine.combine_with_sentiment(tech, BIAS_BULLISH)
        assert decision["action"] == ACTION_LONG
        assert decision["size_factor"] == 1.0
        assert decision["executable"] is True

    def test_buy_neutral_is_long_half_size(self, signal_engine):
        tech = {"signal": SIGNAL_BUY, "reason": "test"}
        decision = signal_engine.combine_with_sentiment(tech, BIAS_NEUTRAL)
        assert decision["action"] == ACTION_LONG
        assert decision["size_factor"] == 0.5
        assert decision["executable"] is True

    def test_buy_bearish_is_rejected(self, signal_engine):
        tech = {"signal": SIGNAL_BUY, "reason": "test"}
        decision = signal_engine.combine_with_sentiment(tech, BIAS_BEARISH)
        assert decision["action"] == ACTION_HOLD
        assert decision["executable"] is False

    def test_sell_bearish_is_short_not_executable(self, signal_engine):
        tech = {"signal": SIGNAL_SELL, "reason": "test"}
        decision = signal_engine.combine_with_sentiment(tech, BIAS_BEARISH)
        assert decision["action"] == ACTION_SHORT
        assert decision["executable"] is False  # NSE ETF restriction

    def test_sell_neutral_is_short_not_executable(self, signal_engine):
        tech = {"signal": SIGNAL_SELL, "reason": "test"}
        decision = signal_engine.combine_with_sentiment(tech, BIAS_NEUTRAL)
        assert decision["action"] == ACTION_SHORT
        assert decision["size_factor"] == 0.5
        assert decision["executable"] is False

    def test_sell_bullish_is_rejected(self, signal_engine):
        tech = {"signal": SIGNAL_SELL, "reason": "test"}
        decision = signal_engine.combine_with_sentiment(tech, BIAS_BULLISH)
        assert decision["action"] == ACTION_HOLD
        assert decision["executable"] is False

    def test_none_signal_is_hold(self, signal_engine):
        tech = {"signal": SIGNAL_NONE, "reason": "test"}
        decision = signal_engine.combine_with_sentiment(tech, BIAS_BULLISH)
        assert decision["action"] == ACTION_HOLD
        assert decision["size_factor"] == 0.0

    def test_decision_has_bias_field(self, signal_engine):
        tech = {"signal": SIGNAL_BUY, "reason": "test"}
        decision = signal_engine.combine_with_sentiment(tech, BIAS_NEUTRAL)
        assert decision["bias"] == BIAS_NEUTRAL
