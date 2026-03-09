"""Agent 1: Market Analyst — Detects market regime and sentiment."""

from __future__ import annotations

import numpy as np
import pandas as pd

from yanagiba.indicators import technical
from yanagiba.models.config import TradingConfig
from yanagiba.models.types import MarketAnalysis, MarketRegime


class MarketAnalyst:
    """Analyzes market conditions across multiple timeframes and assets."""

    def __init__(self, config: TradingConfig | None = None):
        self.config = config or TradingConfig()

    def analyze(
        self,
        ohlcv_by_timeframe: dict[str, pd.DataFrame],
        order_book: dict | None = None,
        funding_rate: float | None = None,
    ) -> MarketAnalysis:
        scores: list[float] = []
        details: dict = {}

        # Analyze each timeframe
        for tf, df in ohlcv_by_timeframe.items():
            if len(df) < 50:
                continue
            df = technical.compute_all(df, self.config)
            tf_score = self._score_timeframe(df, tf)
            scores.append(tf_score)
            details[f"score_{tf}"] = tf_score

        # Order book analysis
        if order_book:
            imbalance = order_book.get("imbalance", 0)
            ob_score = imbalance * 3  # scale to ~(-3, +3)
            scores.append(ob_score)
            details["order_book_imbalance"] = imbalance

        # Funding rate
        if funding_rate is not None:
            fr_score = -funding_rate * 100  # high funding = bearish (crowded long)
            scores.append(fr_score)
            details["funding_rate"] = funding_rate

        sentiment = float(np.clip(np.mean(scores) if scores else 0, -10, 10))
        regime = self._classify_regime(ohlcv_by_timeframe, sentiment)
        volatility = self._measure_volatility(ohlcv_by_timeframe)

        return MarketAnalysis(
            market_regime=regime,
            sentiment_score=round(sentiment, 2),
            volatility_level=round(volatility, 3),
            details=details,
        )

    def _score_timeframe(self, df: pd.DataFrame, timeframe: str) -> float:
        """Score a single timeframe from -10 to +10."""
        score = 0.0
        latest = df.iloc[-1]

        # Trend: price vs EMAs
        close = latest["close"]
        if not np.isnan(latest["ema_fast"]):
            if close > latest["ema_fast"] > latest["ema_mid"]:
                score += 2
            elif close < latest["ema_fast"] < latest["ema_mid"]:
                score -= 2

        # RSI
        rsi_val = latest["rsi"]
        if not np.isnan(rsi_val):
            if rsi_val > 70:
                score -= 1  # overbought
            elif rsi_val > 55:
                score += 1.5
            elif rsi_val < 30:
                score += 1  # oversold bounce potential
            elif rsi_val < 45:
                score -= 1.5

        # MACD
        if not np.isnan(latest["macd_hist"]):
            if latest["macd_hist"] > 0 and latest["macd"] > latest["macd_signal"]:
                score += 1.5
            elif latest["macd_hist"] < 0 and latest["macd"] < latest["macd_signal"]:
                score -= 1.5

        # VWAP
        if not np.isnan(latest["vwap"]):
            if close > latest["vwap"]:
                score += 1
            else:
                score -= 1

        # Volume delta
        recent_vd = df["volume_delta"].tail(5).sum()
        if recent_vd > 0:
            score += 1
        elif recent_vd < 0:
            score -= 1

        # Weight shorter timeframes MORE for scalping (this is an intraday bot)
        weight = {"1m": 1.4, "3m": 1.3, "5m": 1.2, "15m": 1.0, "1h": 0.8, "4h": 0.6, "1d": 0.4}
        return score * weight.get(timeframe, 1.0)

    def _classify_regime(
        self, ohlcv_by_timeframe: dict[str, pd.DataFrame], sentiment: float
    ) -> MarketRegime:
        # Use the highest available timeframe for regime detection
        for tf in ["1d", "4h", "1h", "15m", "5m", "1m"]:
            if tf in ohlcv_by_timeframe and len(ohlcv_by_timeframe[tf]) >= 50:
                df = ohlcv_by_timeframe[tf]
                break
        else:
            return MarketRegime.RANGING

        df = technical.compute_all(df, self.config)
        latest = df.iloc[-1]

        # Volatility check
        if not np.isnan(latest["atr"]) and latest["close"] > 0:
            atr_pct = latest["atr"] / latest["close"]
            if atr_pct > 0.03:
                return MarketRegime.VOLATILE

        # Trend check via EMA alignment
        if not any(np.isnan(latest[k]) for k in ["ema_fast", "ema_mid", "ema_slow"]):
            if latest["ema_fast"] > latest["ema_mid"] > latest["ema_slow"]:
                return MarketRegime.TRENDING_UP
            if latest["ema_fast"] < latest["ema_mid"] < latest["ema_slow"]:
                return MarketRegime.TRENDING_DOWN

        return MarketRegime.RANGING

    def _measure_volatility(self, ohlcv_by_timeframe: dict[str, pd.DataFrame]) -> float:
        for tf in ["1h", "4h", "15m", "5m", "1d"]:
            if tf in ohlcv_by_timeframe and len(ohlcv_by_timeframe[tf]) >= 20:
                df = ohlcv_by_timeframe[tf]
                returns = df["close"].pct_change().dropna()
                vol = float(returns.std()) if len(returns) > 1 else 0.0
                return min(vol * 10, 1.0)  # normalized 0-1
        return 0.5
