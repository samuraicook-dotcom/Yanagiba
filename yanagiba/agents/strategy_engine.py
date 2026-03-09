"""Agent 2: Strategy Engine — Generates trade signals based on market regime."""

from __future__ import annotations

import numpy as np
import pandas as pd

from yanagiba.indicators import technical
from yanagiba.models.config import TradingConfig
from yanagiba.models.types import Direction, MarketAnalysis, MarketRegime, TradeSignal


class StrategyEngine:
    """Generates trade opportunities matching current market regime."""

    def __init__(self, config: TradingConfig | None = None):
        self.config = config or TradingConfig()

    def generate_signals(
        self,
        symbol: str,
        analysis: MarketAnalysis,
        ohlcv_by_timeframe: dict[str, pd.DataFrame],
        order_book: dict | None = None,
    ) -> list[TradeSignal]:
        signals: list[TradeSignal] = []

        for tf, df in ohlcv_by_timeframe.items():
            if len(df) < 50:
                continue
            df = technical.compute_all(df, self.config)
            latest = df.iloc[-1]

            regime = analysis.market_regime
            if regime == MarketRegime.TRENDING_UP:
                sig = self._trending_long(symbol, df, latest, tf)
                if sig:
                    signals.append(sig)
            elif regime == MarketRegime.TRENDING_DOWN:
                sig = self._trending_short(symbol, df, latest, tf)
                if sig:
                    signals.append(sig)
            elif regime == MarketRegime.RANGING:
                sigs = self._ranging_strategies(symbol, df, latest, tf)
                signals.extend(sigs)
            elif regime == MarketRegime.VOLATILE:
                sigs = self._volatile_strategies(symbol, df, latest, tf, order_book)
                signals.extend(sigs)

            # Always check scalp setups on short timeframes
            if tf in self.config.scalp_timeframes:
                scalp = self._scalp_setup(symbol, df, latest, tf, order_book)
                if scalp:
                    signals.append(scalp)

        # Deduplicate: keep highest confidence per direction
        return self._deduplicate(signals)

    def _trending_long(
        self, symbol: str, df: pd.DataFrame, latest: pd.Series, tf: str
    ) -> TradeSignal | None:
        close = latest["close"]
        atr_val = latest["atr"]
        if np.isnan(atr_val) or atr_val <= 0:
            return None

        # EMA pullback: price near fast EMA in uptrend (widened entry zone)
        if not np.isnan(latest["ema_fast"]) and not np.isnan(latest["ema_mid"]):
            pullback = abs(close - latest["ema_fast"]) / close < 0.01  # wider zone
            above_mid = close > latest["ema_mid"]
            rsi_ok = not np.isnan(latest["rsi"]) and 40 < latest["rsi"] < 75

            if pullback and above_mid and rsi_ok:
                sl = close - 1.5 * atr_val  # tighter SL
                tp1 = close + 3 * atr_val   # bigger TP
                tp2 = close + 5 * atr_val
                rr = (tp1 - close) / (close - sl) if close > sl else 0
                return TradeSignal(
                    asset=symbol, direction=Direction.LONG, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2), confidence_score=7.0,
                    strategy="ema_pullback_long", timeframe=tf,
                )
        return None

    def _trending_short(
        self, symbol: str, df: pd.DataFrame, latest: pd.Series, tf: str
    ) -> TradeSignal | None:
        close = latest["close"]
        atr_val = latest["atr"]
        if np.isnan(atr_val) or atr_val <= 0:
            return None

        if not np.isnan(latest["ema_fast"]) and not np.isnan(latest["ema_mid"]):
            pullback = abs(close - latest["ema_fast"]) / close < 0.01
            below_mid = close < latest["ema_mid"]
            rsi_ok = not np.isnan(latest["rsi"]) and 25 < latest["rsi"] < 60

            if pullback and below_mid and rsi_ok:
                sl = close + 1.5 * atr_val
                tp1 = close - 3 * atr_val
                tp2 = close - 5 * atr_val
                rr = (close - tp1) / (sl - close) if sl > close else 0
                return TradeSignal(
                    asset=symbol, direction=Direction.SHORT, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2), confidence_score=7.0,
                    strategy="ema_pullback_short", timeframe=tf,
                )
        return None

    def _ranging_strategies(
        self, symbol: str, df: pd.DataFrame, latest: pd.Series, tf: str
    ) -> list[TradeSignal]:
        signals = []
        close = latest["close"]
        atr_val = latest["atr"]
        if np.isnan(atr_val) or atr_val <= 0:
            return signals

        # Mean reversion: Bollinger band proximity (widened trigger zone)
        if not np.isnan(latest["bb_lower"]) and not np.isnan(latest["bb_upper"]):
            # Long near lower band
            if close <= latest["bb_lower"] * 1.005:
                sl = close - 1.0 * atr_val  # tighter SL
                tp1 = latest["bb_mid"]
                tp2 = latest["bb_upper"]
                rr = (tp1 - close) / (close - sl) if close > sl else 0
                signals.append(TradeSignal(
                    asset=symbol, direction=Direction.LONG, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2), confidence_score=7.0,
                    strategy="bb_mean_reversion_long", timeframe=tf,
                ))
            # Short near upper band
            elif close >= latest["bb_upper"] * 0.995:
                sl = close + 1.0 * atr_val
                tp1 = latest["bb_mid"]
                tp2 = latest["bb_lower"]
                rr = (close - tp1) / (sl - close) if sl > close else 0
                signals.append(TradeSignal(
                    asset=symbol, direction=Direction.SHORT, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2), confidence_score=7.0,
                    strategy="bb_mean_reversion_short", timeframe=tf,
                ))

        # VWAP bounce (widened zone, bigger targets)
        if not np.isnan(latest["vwap"]):
            vwap_dist = abs(close - latest["vwap"]) / close
            if vwap_dist < 0.005:  # wider VWAP zone
                direction = Direction.LONG if latest.get("volume_delta", 0) > 0 else Direction.SHORT
                if direction == Direction.LONG:
                    sl = close - 1.0 * atr_val
                    tp1 = close + 3 * atr_val
                    tp2 = close + 5 * atr_val
                else:
                    sl = close + 1.0 * atr_val
                    tp1 = close - 3 * atr_val
                    tp2 = close - 5 * atr_val
                rr_val = abs(tp1 - close) / abs(close - sl) if abs(close - sl) > 0 else 0
                signals.append(TradeSignal(
                    asset=symbol, direction=direction, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr_val, 2), confidence_score=6.0,
                    strategy="vwap_bounce", timeframe=tf,
                ))

        return signals

    def _volatile_strategies(
        self, symbol: str, df: pd.DataFrame, latest: pd.Series, tf: str,
        order_book: dict | None = None,
    ) -> list[TradeSignal]:
        signals = []
        close = latest["close"]
        atr_val = latest["atr"]
        if np.isnan(atr_val) or atr_val <= 0:
            return signals

        # Liquidity sweep: price wicks beyond recent high/low then reverses
        recent_high = df["high"].tail(20).max()
        recent_low = df["low"].tail(20).min()

        if close < recent_high * 0.998 and latest["high"] >= recent_high * 0.999:
            # Swept highs, potential short
            sl = recent_high + 0.5 * atr_val
            tp1 = close - 3 * atr_val
            tp2 = close - 5 * atr_val
            rr = (close - tp1) / (sl - close) if sl > close else 0
            signals.append(TradeSignal(
                asset=symbol, direction=Direction.SHORT, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2), confidence_score=6.0,
                strategy="liquidity_sweep_short", timeframe=tf,
            ))

        if close > recent_low * 1.002 and latest["low"] <= recent_low * 1.001:
            # Swept lows, potential long
            sl = recent_low - 0.5 * atr_val
            tp1 = close + 3 * atr_val
            tp2 = close + 5 * atr_val
            rr = (tp1 - close) / (close - sl) if close > sl else 0
            signals.append(TradeSignal(
                asset=symbol, direction=Direction.LONG, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2), confidence_score=7.0,
                strategy="liquidity_sweep_long", timeframe=tf,
            ))

        return signals

    def _scalp_setup(
        self, symbol: str, df: pd.DataFrame, latest: pd.Series, tf: str,
        order_book: dict | None = None,
    ) -> TradeSignal | None:
        close = latest["close"]
        rsi_val = latest["rsi"]
        vwap_val = latest["vwap"]
        vd = latest.get("volume_delta", 0)

        if any(np.isnan(x) for x in [rsi_val, vwap_val]):
            return None

        ob_imbalance = order_book.get("imbalance", 0) if order_book else 0

        # LONG scalp
        if (
            close > vwap_val
            and rsi_val > self.config.rsi_long_threshold
            and vd > 0
            and ob_imbalance > 0.1
        ):
            sl = close * (1 - self.config.scalp_stop_loss)
            tp1 = close * (1 + self.config.scalp_take_profit_min)
            tp2 = close * (1 + self.config.scalp_take_profit_max)
            rr = self.config.scalp_take_profit_min / self.config.scalp_stop_loss
            confidence = min(5 + abs(ob_imbalance) * 3 + (rsi_val - 55) / 10, 10)
            return TradeSignal(
                asset=symbol, direction=Direction.LONG, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2), confidence_score=round(confidence, 1),
                strategy="scalp_momentum_long", timeframe=tf,
            )

        # SHORT scalp
        if (
            close < vwap_val
            and rsi_val < self.config.rsi_short_threshold
            and vd < 0
            and ob_imbalance < -0.1
        ):
            sl = close * (1 + self.config.scalp_stop_loss)
            tp1 = close * (1 - self.config.scalp_take_profit_min)
            tp2 = close * (1 - self.config.scalp_take_profit_max)
            rr = self.config.scalp_take_profit_min / self.config.scalp_stop_loss
            confidence = min(5 + abs(ob_imbalance) * 3 + (45 - rsi_val) / 10, 10)
            return TradeSignal(
                asset=symbol, direction=Direction.SHORT, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2), confidence_score=round(confidence, 1),
                strategy="scalp_momentum_short", timeframe=tf,
            )

        return None

    def _deduplicate(self, signals: list[TradeSignal]) -> list[TradeSignal]:
        best: dict[str, TradeSignal] = {}
        for sig in signals:
            key = f"{sig.asset}_{sig.direction.value}"
            if key not in best or sig.confidence_score > best[key].confidence_score:
                best[key] = sig
        return sorted(best.values(), key=lambda s: s.confidence_score, reverse=True)
