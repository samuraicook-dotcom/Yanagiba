"""
core/signals.py — Signal Generation and Decision Matrix

Generates technical BUY/SELL signals from indicator data and combines
them with sentiment bias to produce final trade decisions.

SHORT signals are generated but NOT executed (NSE ETF restriction).
"""

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger("kaizen.signals")

# Signal constants
SIGNAL_BUY = "BUY"
SIGNAL_SELL = "SELL"
SIGNAL_NONE = "NONE"

# Action constants
ACTION_LONG = "LONG"
ACTION_SHORT = "SHORT"
ACTION_HOLD = "HOLD"

# Bias constants
BIAS_BULLISH = "BULLISH"
BIAS_BEARISH = "BEARISH"
BIAS_NEUTRAL = "NEUTRAL"


class SignalEngine:
    """
    Generates trading signals by combining technical indicators with sentiment bias.

    Technical signal rules require ALL conditions to be true simultaneously.
    Decision matrix maps (tech_signal, sentiment_bias) → action + size_factor.
    """

    def __init__(self, config: dict) -> None:
        """
        Args:
            config: technicals section from settings.yaml
        """
        self.rsi_overbought: float = config["rsi_overbought"]
        self.rsi_oversold: float = config["rsi_oversold"]

    # ------------------------------------------------------------------
    # EMA crossover detection
    # ------------------------------------------------------------------

    def _ema_crossed_above(self, df: pd.DataFrame, lookback: int = 2) -> bool:
        """
        True if EMA(fast) crossed above EMA(slow) within last `lookback` candles.
        A cross is defined as: previous candle had fast <= slow, current has fast > slow.
        """
        if len(df) < lookback + 1:
            return False

        recent = df.tail(lookback + 1)
        for i in range(len(recent) - 1):
            prev_fast = recent["ema_fast"].iloc[i]
            prev_slow = recent["ema_slow"].iloc[i]
            curr_fast = recent["ema_fast"].iloc[i + 1]
            curr_slow = recent["ema_slow"].iloc[i + 1]
            if prev_fast <= prev_slow and curr_fast > curr_slow:
                return True
        return False

    def _ema_crossed_below(self, df: pd.DataFrame, lookback: int = 2) -> bool:
        """
        True if EMA(fast) crossed below EMA(slow) within last `lookback` candles.
        """
        if len(df) < lookback + 1:
            return False

        recent = df.tail(lookback + 1)
        for i in range(len(recent) - 1):
            prev_fast = recent["ema_fast"].iloc[i]
            prev_slow = recent["ema_slow"].iloc[i]
            curr_fast = recent["ema_fast"].iloc[i + 1]
            curr_slow = recent["ema_slow"].iloc[i + 1]
            if prev_fast >= prev_slow and curr_fast < curr_slow:
                return True
        return False

    # ------------------------------------------------------------------
    # Technical signal generation
    # ------------------------------------------------------------------

    def generate_technical_signal(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Generate a BUY, SELL, or NONE technical signal from the latest candles.

        BUY conditions (ALL must be true):
          - EMA(fast) > EMA(slow) on latest candle
          - EMA(fast) crossed above EMA(slow) within last 2 candles
          - RSI between rsi_oversold and rsi_overbought
          - Close > VWAP
          - Volume spike = True

        SELL conditions (ALL must be true):
          - EMA(fast) < EMA(slow) on latest candle
          - EMA(fast) crossed below EMA(slow) within last 2 candles
          - RSI between rsi_oversold and rsi_overbought
          - Close < VWAP
          - Volume spike = True

        Args:
            df: OHLCV DataFrame with all indicator columns computed

        Returns:
            dict: {signal, reason, conditions}
        """
        if len(df) < 3:
            return {"signal": SIGNAL_NONE, "reason": "insufficient_data", "conditions": {}}

        latest = df.iloc[-1]

        ema_fast = latest["ema_fast"]
        ema_slow = latest["ema_slow"]
        rsi = latest["rsi"]
        close = latest["close"]
        vwap = latest["vwap"]
        vol_spike = bool(latest["volume_spike"])
        rsi_ok = self.rsi_oversold < rsi < self.rsi_overbought

        # BUY conditions
        buy_conditions = {
            "ema_fast_above_slow": ema_fast > ema_slow,
            "ema_crossed_above": self._ema_crossed_above(df),
            "rsi_in_range": rsi_ok,
            "close_above_vwap": close > vwap,
            "volume_spike": vol_spike,
        }
        if all(buy_conditions.values()):
            logger.info(
                "BUY signal — EMA cross above, RSI=%.1f, close=%.2f > VWAP=%.2f",
                rsi, close, vwap,
            )
            return {"signal": SIGNAL_BUY, "reason": "ema_cross_buy", "conditions": buy_conditions}

        # SELL conditions
        sell_conditions = {
            "ema_fast_below_slow": ema_fast < ema_slow,
            "ema_crossed_below": self._ema_crossed_below(df),
            "rsi_in_range": rsi_ok,
            "close_below_vwap": close < vwap,
            "volume_spike": vol_spike,
        }
        if all(sell_conditions.values()):
            logger.info(
                "SELL signal — EMA cross below, RSI=%.1f, close=%.2f < VWAP=%.2f",
                rsi, close, vwap,
            )
            return {"signal": SIGNAL_SELL, "reason": "ema_cross_sell", "conditions": sell_conditions}

        # Determine which conditions failed for diagnostics
        failed_buy = [k for k, v in buy_conditions.items() if not v]
        return {
            "signal": SIGNAL_NONE,
            "reason": f"no_signal (failed_buy={failed_buy})",
            "conditions": buy_conditions,
        }

    # ------------------------------------------------------------------
    # Decision matrix
    # ------------------------------------------------------------------

    def combine_with_sentiment(
        self, tech_signal: dict, sentiment_bias: str
    ) -> dict[str, Any]:
        """
        Apply the decision matrix to combine tech signal and sentiment bias.

        Matrix:
          BUY  + BULLISH  → LONG  (size_factor=1.0)
          BUY  + NEUTRAL  → LONG  (size_factor=0.5)
          BUY  + BEARISH  → REJECT
          SELL + BEARISH  → SHORT (size_factor=1.0) [logged, not executed]
          SELL + NEUTRAL  → SHORT (size_factor=0.5) [logged, not executed]
          SELL + BULLISH  → REJECT
          NONE + any      → HOLD

        Args:
            tech_signal: Output of generate_technical_signal()
            sentiment_bias: 'BULLISH', 'BEARISH', or 'NEUTRAL'

        Returns:
            dict: {action, size_factor, signal, bias, executable, reason}
        """
        signal = tech_signal.get("signal", SIGNAL_NONE)
        bias = sentiment_bias.upper() if sentiment_bias else BIAS_NEUTRAL

        if signal == SIGNAL_NONE:
            return {
                "action": ACTION_HOLD,
                "size_factor": 0.0,
                "signal": signal,
                "bias": bias,
                "executable": False,
                "reason": tech_signal.get("reason", "no_signal"),
            }

        if signal == SIGNAL_BUY:
            if bias == BIAS_BULLISH:
                return {
                    "action": ACTION_LONG,
                    "size_factor": 1.0,
                    "signal": signal,
                    "bias": bias,
                    "executable": True,
                    "reason": "buy_bullish_confirmed",
                }
            elif bias == BIAS_NEUTRAL:
                return {
                    "action": ACTION_LONG,
                    "size_factor": 0.5,
                    "signal": signal,
                    "bias": bias,
                    "executable": True,
                    "reason": "buy_neutral_half_size",
                }
            else:  # BEARISH
                logger.info("BUY signal REJECTED — sentiment is BEARISH")
                return {
                    "action": ACTION_HOLD,
                    "size_factor": 0.0,
                    "signal": signal,
                    "bias": bias,
                    "executable": False,
                    "reason": "buy_rejected_bearish_sentiment",
                }

        if signal == SIGNAL_SELL:
            if bias == BIAS_BEARISH:
                return {
                    "action": ACTION_SHORT,
                    "size_factor": 1.0,
                    "signal": signal,
                    "bias": bias,
                    "executable": False,  # NSE ETF restriction
                    "reason": "short_bearish_confirmed_not_executable",
                }
            elif bias == BIAS_NEUTRAL:
                return {
                    "action": ACTION_SHORT,
                    "size_factor": 0.5,
                    "signal": signal,
                    "bias": bias,
                    "executable": False,  # NSE ETF restriction
                    "reason": "short_neutral_half_size_not_executable",
                }
            else:  # BULLISH
                logger.info("SELL signal REJECTED — sentiment is BULLISH")
                return {
                    "action": ACTION_HOLD,
                    "size_factor": 0.0,
                    "signal": signal,
                    "bias": bias,
                    "executable": False,
                    "reason": "sell_rejected_bullish_sentiment",
                }

        return {
            "action": ACTION_HOLD,
            "size_factor": 0.0,
            "signal": signal,
            "bias": bias,
            "executable": False,
            "reason": "unknown_signal",
        }
