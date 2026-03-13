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
        # Sentiment modifier: penalize/boost confidence based on sentiment
        sentiment = analysis.sentiment_score  # -10 to +10

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
        deduped = self._deduplicate(signals)

        # Apply sentiment modifier: boost/penalize based on alignment
        for sig in deduped:
            if sentiment > 3 and sig.direction == Direction.LONG:
                sig.confidence_score = min(sig.confidence_score + 0.5, 10)
            elif sentiment < -3 and sig.direction == Direction.SHORT:
                sig.confidence_score = min(sig.confidence_score + 0.5, 10)
            elif sentiment > 3 and sig.direction == Direction.SHORT:
                sig.confidence_score = max(sig.confidence_score - 1.0, 1)
            elif sentiment < -3 and sig.direction == Direction.LONG:
                sig.confidence_score = max(sig.confidence_score - 1.0, 1)

        return deduped

    def _trending_long(
        self, symbol: str, df: pd.DataFrame, latest: pd.Series, tf: str
    ) -> TradeSignal | None:
        close = latest["close"]
        atr_val = latest["atr"]
        if np.isnan(atr_val) or atr_val <= 0:
            return None

        # EMA pullback: price near fast EMA in uptrend (ATR-scaled zone)
        if not np.isnan(latest["ema_fast"]) and not np.isnan(latest["ema_mid"]):
            atr_zone = min(atr_val / close * 1.5, 0.02)  # dynamic zone based on volatility
            pullback = abs(close - latest["ema_fast"]) / close < atr_zone
            above_mid = close > latest["ema_mid"]
            rsi_ok = not np.isnan(latest["rsi"]) and 40 < latest["rsi"] < 75

            if pullback and above_mid and rsi_ok:
                sl = close - 1.5 * atr_val  # tighter SL
                tp1 = close + 3 * atr_val   # bigger TP
                tp2 = close + 5 * atr_val
                rr = (tp1 - close) / (close - sl) if close > sl else 0
                # Dynamic confidence: RSI quality + volume + MACD
                conf = self._trend_confidence(
                    latest, df, is_long=True,
                )
                return TradeSignal(
                    asset=symbol, direction=Direction.LONG, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2),
                    confidence_score=round(conf, 1),
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
            atr_zone = min(atr_val / close * 1.5, 0.02)
            pullback = abs(close - latest["ema_fast"]) / close < atr_zone
            below_mid = close < latest["ema_mid"]
            rsi_ok = not np.isnan(latest["rsi"]) and 25 < latest["rsi"] < 60

            if pullback and below_mid and rsi_ok:
                sl = close + 1.5 * atr_val
                tp1 = close - 3 * atr_val
                tp2 = close - 5 * atr_val
                rr = (close - tp1) / (sl - close) if sl > close else 0
                conf = self._trend_confidence(
                    latest, df, is_long=False,
                )
                return TradeSignal(
                    asset=symbol, direction=Direction.SHORT, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2),
                    confidence_score=round(conf, 1),
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

        # Mean reversion: Bollinger band proximity (wider zone = more setups)
        if not np.isnan(latest["bb_lower"]) and not np.isnan(latest["bb_upper"]):
            rsi_val = latest["rsi"]
            rsi_ranging = (
                not np.isnan(rsi_val) and 35 < rsi_val < 65
            )
            # Long near lower band (1.5% zone above lower band)
            if close <= latest["bb_lower"] * 1.015:
                sl = close - 1.0 * atr_val  # tighter SL
                tp1 = latest["bb_mid"]
                tp2 = latest["bb_upper"]
                rr = (tp1 - close) / (close - sl) if close > sl else 0
                # Dynamic: RSI confirming oversold bounce + ranging
                bb_conf = 6.0
                if rsi_ranging:
                    bb_conf += 1.0
                if not np.isnan(rsi_val) and rsi_val < 40:
                    bb_conf += 0.5  # deeper oversold = better bounce
                signals.append(TradeSignal(
                    asset=symbol, direction=Direction.LONG, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2),
                    confidence_score=round(min(bb_conf, 10), 1),
                    strategy="bb_mean_reversion_long", timeframe=tf,
                ))
            # Short near upper band (1.5% zone below upper band)
            elif close >= latest["bb_upper"] * 0.985:
                sl = close + 1.0 * atr_val
                tp1 = latest["bb_mid"]
                tp2 = latest["bb_lower"]
                rr = (close - tp1) / (sl - close) if sl > close else 0
                bb_conf = 6.0
                if rsi_ranging:
                    bb_conf += 1.0
                if not np.isnan(rsi_val) and rsi_val > 60:
                    bb_conf += 0.5
                signals.append(TradeSignal(
                    asset=symbol, direction=Direction.SHORT, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2),
                    confidence_score=round(min(bb_conf, 10), 1),
                    strategy="bb_mean_reversion_short", timeframe=tf,
                ))

        # VWAP bounce (ATR-scaled zone, bigger targets)
        if not np.isnan(latest["vwap"]):
            vwap_dist = abs(close - latest["vwap"]) / close
            vwap_zone = min(atr_val / close, 0.015)
            vd = latest.get("volume_delta", 0)
            # Skip doji candles — no clear direction
            if pd.isna(vd):
                vd = 0
            if vwap_dist < vwap_zone and vd != 0:
                direction = Direction.LONG if vd > 0 else Direction.SHORT
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
        # Exclude current candle to avoid self-comparison
        if len(df) < 21:
            return signals
        vd = latest.get("volume_delta", 0)
        if pd.isna(vd):
            vd = 0
        recent_high = df["high"].iloc[-21:-1].max()
        recent_low = df["low"].iloc[-21:-1].min()

        if (
            close < recent_high * 0.998
            and latest["high"] >= recent_high * 0.999
        ):
            # Swept highs, potential short
            sl = recent_high + 0.5 * atr_val
            tp1 = close - 3 * atr_val
            tp2 = close - 5 * atr_val
            rr = (close - tp1) / (sl - close) if sl > close else 0
            # Dynamic: wick depth + volume confirm reversal
            wick_pct = (latest["high"] - close) / close if close > 0 else 0
            sweep_conf = min(5.5 + wick_pct * 100 + (1 if vd < 0 else 0), 9)
            signals.append(TradeSignal(
                asset=symbol, direction=Direction.SHORT, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2),
                confidence_score=round(sweep_conf, 1),
                strategy="liquidity_sweep_short", timeframe=tf,
            ))

        if (
            close > recent_low * 1.002
            and latest["low"] <= recent_low * 1.001
        ):
            # Swept lows, potential long
            sl = recent_low - 0.5 * atr_val
            tp1 = close + 3 * atr_val
            tp2 = close + 5 * atr_val
            rr = (tp1 - close) / (close - sl) if close > sl else 0
            wick_pct = (close - latest["low"]) / close if close > 0 else 0
            sweep_conf = min(5.5 + wick_pct * 100 + (1 if vd > 0 else 0), 9)
            signals.append(TradeSignal(
                asset=symbol, direction=Direction.LONG, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2),
                confidence_score=round(sweep_conf, 1),
                strategy="liquidity_sweep_long", timeframe=tf,
            ))

        return signals

    def _get_scalp_params(self, symbol: str) -> tuple[float, float, float]:
        """Get asset-specific scalp SL/TP or fall back to defaults."""
        overrides = self.config.asset_overrides.get(symbol, {})
        sl = overrides.get("scalp_stop_loss", self.config.scalp_stop_loss)
        tp_min = overrides.get(
            "scalp_take_profit_min", self.config.scalp_take_profit_min
        )
        tp_max = overrides.get(
            "scalp_take_profit_max", self.config.scalp_take_profit_max
        )
        return sl, tp_min, tp_max

    def _scalp_setup(
        self, symbol: str, df: pd.DataFrame, latest: pd.Series, tf: str,
        order_book: dict | None = None,
    ) -> TradeSignal | None:
        close = latest["close"]
        rsi_val = latest["rsi"]
        vwap_val = latest["vwap"]
        vd = latest.get("volume_delta", 0)
        if pd.isna(vd):
            vd = 0

        if any(np.isnan(x) for x in [rsi_val, vwap_val]):
            return None

        ob_imbalance = order_book.get("imbalance", 0) if order_book else 0

        # Get asset-specific SL/TP (BTC needs tighter params than altcoins)
        scalp_sl, scalp_tp_min, scalp_tp_max = self._get_scalp_params(symbol)

        # LONG scalp: RSI in sweet spot (not overbought), strong order book
        if (
            close > vwap_val
            and rsi_val > self.config.rsi_long_threshold
            and rsi_val < 70  # reject overbought — don't chase
            and vd > 0
            and ob_imbalance > 0.1
        ):
            sl = close * (1 - scalp_sl)
            tp1 = close * (1 + scalp_tp_min)
            tp2 = close * (1 + scalp_tp_max)
            rr = scalp_tp_min / scalp_sl
            # Confidence: order book + RSI sweet zone (55-65 best)
            rsi_quality = max(0, 1.0 - abs(rsi_val - 60) / 15)
            confidence = min(
                5 + abs(ob_imbalance) * 3 + rsi_quality * 2, 10
            )
            return TradeSignal(
                asset=symbol, direction=Direction.LONG, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2),
                confidence_score=round(confidence, 1),
                strategy="scalp_momentum_long", timeframe=tf,
            )

        # SHORT scalp: RSI in sweet spot (not oversold), weak order book
        if (
            close < vwap_val
            and rsi_val < self.config.rsi_short_threshold
            and rsi_val > 30  # reject oversold — don't chase
            and vd < 0
            and ob_imbalance < -0.1
        ):
            sl = close * (1 + scalp_sl)
            tp1 = close * (1 - scalp_tp_min)
            tp2 = close * (1 - scalp_tp_max)
            rr = scalp_tp_min / scalp_sl
            # Confidence: order book weakness + RSI sweet zone (35-45)
            rsi_quality = max(0, 1.0 - abs(rsi_val - 40) / 15)
            confidence = min(
                5 + abs(ob_imbalance) * 3 + rsi_quality * 2, 10
            )
            return TradeSignal(
                asset=symbol, direction=Direction.SHORT, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2),
                confidence_score=round(confidence, 1),
                strategy="scalp_momentum_short", timeframe=tf,
            )

        return None

    @staticmethod
    def _trend_confidence(
        latest: pd.Series, df: pd.DataFrame, is_long: bool,
    ) -> float:
        """Dynamic confidence for trend-following strategies.

        Scores RSI quality, volume delta confirmation, and MACD alignment.
        """
        conf = 5.5
        rsi_val = latest["rsi"]
        # RSI sweet spot: 50-65 for longs, 35-50 for shorts
        if not np.isnan(rsi_val):
            if is_long:
                quality = max(0, 1.0 - abs(rsi_val - 57) / 20)
            else:
                quality = max(0, 1.0 - abs(rsi_val - 43) / 20)
            conf += quality * 1.5

        # Volume delta confirmation (last 3 candles)
        recent_vd = df["volume_delta"].tail(3).sum(min_count=1)
        if not np.isnan(recent_vd):
            if (is_long and recent_vd > 0) or (not is_long and recent_vd < 0):
                conf += 1.0

        # MACD alignment
        if not np.isnan(latest["macd_hist"]):
            if (is_long and latest["macd_hist"] > 0) or (
                not is_long and latest["macd_hist"] < 0
            ):
                conf += 0.5

        return min(conf, 10)

    @staticmethod
    def _validate_prices(signals: list[TradeSignal]) -> list[TradeSignal]:
        """Remove signals with invalid prices (negative, zero, or NaN)."""
        valid = []
        for sig in signals:
            prices = [sig.entry, sig.stop_loss, sig.take_profit_1, sig.take_profit_2]
            if all(p > 0 and not np.isnan(p) for p in prices):
                valid.append(sig)
            else:
                logger.warning(
                    f"Rejected {sig.asset} {sig.strategy}: invalid prices "
                    f"(entry={sig.entry}, sl={sig.stop_loss}, "
                    f"tp1={sig.take_profit_1}, tp2={sig.take_profit_2})"
                )
        return valid

    def _deduplicate(self, signals: list[TradeSignal]) -> list[TradeSignal]:
        # Validate prices first, then keep highest-confidence per asset
        signals = self._validate_prices(signals)
        # Prevents contradictory LONG + SHORT on the same pair
        best: dict[str, TradeSignal] = {}
        for sig in signals:
            key = sig.asset
            if key not in best or sig.confidence_score > best[key].confidence_score:
                best[key] = sig
        return sorted(
            best.values(),
            key=lambda s: s.confidence_score, reverse=True,
        )
