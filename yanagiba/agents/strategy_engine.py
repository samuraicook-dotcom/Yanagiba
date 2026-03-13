"""Agent 2: Strategy Engine — Generates trade signals based on market regime."""

from __future__ import annotations

import numpy as np
import pandas as pd

from yanagiba.indicators import technical
from yanagiba.models.config import TradingConfig
from yanagiba.models.types import (
    Direction,
    MarketAnalysis,
    MarketRegime,
    OnChainMetrics,
    TradeSignal,
)


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
        on_chain: OnChainMetrics | None = None,
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

        # Liquidation cascade reversal signals
        if on_chain and on_chain.liq_cascade:
            cascade_sig = self._liquidation_cascade_signal(symbol, ohlcv_by_timeframe, on_chain)
            if cascade_sig:
                signals.append(cascade_sig)

        # Boost/penalize confidence based on on-chain data
        if on_chain:
            signals = self._adjust_confidence_with_on_chain(signals, on_chain)

        # Deduplicate: keep highest confidence per direction
        return self._deduplicate(signals)

    def _trending_long(
        self, symbol: str, df: pd.DataFrame, latest: pd.Series, tf: str
    ) -> TradeSignal | None:
        close = latest["close"]
        atr_val = latest["atr"]
        if np.isnan(atr_val) or atr_val <= 0:
            return None

        # EMA pullback: price near fast EMA in uptrend (tight ATR-scaled zone)
        if not np.isnan(latest["ema_fast"]) and not np.isnan(latest["ema_mid"]):
            atr_zone = min(atr_val / close * 0.8, 0.008)  # tight zone: must be close to EMA
            pullback = abs(close - latest["ema_fast"]) / close < atr_zone
            above_mid = close > latest["ema_mid"]
            # RSI 40-65: avoid overbought territory entirely
            rsi_ok = not np.isnan(latest["rsi"]) and 40 < latest["rsi"] < 65

            if pullback and above_mid and rsi_ok:
                sl = close - 1.5 * atr_val
                tp1 = close + 3 * atr_val
                tp2 = close + 5 * atr_val
                rr = (tp1 - close) / (close - sl) if close > sl else 0
                # Dynamic confidence based on RSI sweet spot (45-55 best)
                rsi_quality = max(0, 1.0 - abs(latest["rsi"] - 50) / 20)
                confidence = min(5.0 + rsi_quality * 3 + rr * 0.3, 9.0)
                return TradeSignal(
                    asset=symbol, direction=Direction.LONG, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2),
                    confidence_score=round(confidence, 1),
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
            atr_zone = min(atr_val / close * 0.8, 0.008)  # tight zone
            pullback = abs(close - latest["ema_fast"]) / close < atr_zone
            below_mid = close < latest["ema_mid"]
            # RSI 35-60: avoid oversold territory entirely
            rsi_ok = not np.isnan(latest["rsi"]) and 35 < latest["rsi"] < 60

            if pullback and below_mid and rsi_ok:
                sl = close + 1.5 * atr_val
                tp1 = close - 3 * atr_val
                tp2 = close - 5 * atr_val
                rr = (close - tp1) / (sl - close) if sl > close else 0
                rsi_quality = max(0, 1.0 - abs(latest["rsi"] - 50) / 20)
                confidence = min(5.0 + rsi_quality * 3 + rr * 0.3, 9.0)
                return TradeSignal(
                    asset=symbol, direction=Direction.SHORT, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2),
                    confidence_score=round(confidence, 1),
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

        # Mean reversion: price must be AT or BEYOND Bollinger band
        if not np.isnan(latest["bb_lower"]) and not np.isnan(latest["bb_upper"]):
            # Confirm ranging: RSI between 35-65 (not in a strong trend)
            rsi_val = latest["rsi"] if not np.isnan(latest["rsi"]) else 50
            is_ranging = 35 < rsi_val < 65

            # Long: price at or below lower band (true extreme)
            if close <= latest["bb_lower"] and is_ranging:
                sl = close - 1.0 * atr_val
                tp1 = latest["bb_mid"]  # TP at midline (match entry thesis)
                tp2 = latest["bb_upper"]
                rr = (tp1 - close) / (close - sl) if close > sl else 0
                confidence = min(5.0 + abs(rsi_val - 30) * 0.1 + rr * 0.3, 8.5)
                signals.append(TradeSignal(
                    asset=symbol, direction=Direction.LONG, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2),
                    confidence_score=round(confidence, 1),
                    strategy="bb_mean_reversion_long", timeframe=tf,
                ))
            # Short: price at or above upper band (true extreme)
            elif close >= latest["bb_upper"] and is_ranging:
                sl = close + 1.0 * atr_val
                tp1 = latest["bb_mid"]  # TP at midline (match entry thesis)
                tp2 = latest["bb_lower"]
                rr = (close - tp1) / (sl - close) if sl > close else 0
                confidence = min(5.0 + abs(rsi_val - 70) * 0.1 + rr * 0.3, 8.5)
                signals.append(TradeSignal(
                    asset=symbol, direction=Direction.SHORT, entry=close,
                    stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                    risk_reward=round(rr, 2),
                    confidence_score=round(confidence, 1),
                    strategy="bb_mean_reversion_short", timeframe=tf,
                ))

        # VWAP bounce (ATR-scaled zone, bigger targets)
        if not np.isnan(latest["vwap"]):
            vwap_dist = abs(close - latest["vwap"]) / close
            vwap_zone = min(atr_val / close, 0.015)  # dynamic zone based on volatility
            if vwap_dist < vwap_zone:
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

    def _liquidation_cascade_signal(
        self, symbol: str, ohlcv_by_timeframe: dict[str, pd.DataFrame],
        on_chain: OnChainMetrics,
    ) -> TradeSignal | None:
        """Generate a contrarian signal when a liquidation cascade is detected.

        Long liquidation cascade = longs flushed out → potential bottom → go long.
        Short liquidation cascade = shorts squeezed → potential top → go short.
        """
        # Use shortest available timeframe for entry
        for tf in ["1m", "3m", "5m", "15m"]:
            if tf in ohlcv_by_timeframe and len(ohlcv_by_timeframe[tf]) >= 20:
                df = ohlcv_by_timeframe[tf]
                break
        else:
            return None

        df = technical.compute_all(df, self.config)
        latest = df.iloc[-1]
        close = latest["close"]
        atr_val = latest["atr"]
        if np.isnan(atr_val) or atr_val <= 0:
            return None

        if on_chain.liq_cascade_side == "long":
            # Longs got liquidated → contrarian long (buy the flush)
            sl = close - 2.0 * atr_val
            tp1 = close + 3.0 * atr_val
            tp2 = close + 5.0 * atr_val
            rr = (tp1 - close) / (close - sl) if close > sl else 0
            return TradeSignal(
                asset=symbol, direction=Direction.LONG, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2), confidence_score=7.5,
                strategy="liq_cascade_reversal_long", timeframe=tf,
            )
        elif on_chain.liq_cascade_side == "short":
            # Shorts got liquidated → contrarian short (sell the squeeze)
            sl = close + 2.0 * atr_val
            tp1 = close - 3.0 * atr_val
            tp2 = close - 5.0 * atr_val
            rr = (close - tp1) / (sl - close) if sl > close else 0
            return TradeSignal(
                asset=symbol, direction=Direction.SHORT, entry=close,
                stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
                risk_reward=round(rr, 2), confidence_score=7.5,
                strategy="liq_cascade_reversal_short", timeframe=tf,
            )
        return None

    def _adjust_confidence_with_on_chain(
        self, signals: list[TradeSignal], on_chain: OnChainMetrics,
    ) -> list[TradeSignal]:
        """Adjust signal confidence based on on-chain confirmation/divergence.

        Confirming signals (same direction as on-chain flow) get a boost.
        Divergent signals (against on-chain flow) get penalized.
        """
        for sig in signals:
            boost = 0.0

            # Volume flow confirmation
            if sig.direction == Direction.LONG and on_chain.volume_delta_pct > 0.2:
                boost += 0.5  # real buying pressure confirms long
            elif sig.direction == Direction.SHORT and on_chain.volume_delta_pct < -0.2:
                boost += 0.5  # real selling pressure confirms short
            elif sig.direction == Direction.LONG and on_chain.volume_delta_pct < -0.3:
                boost -= 0.5  # buying against heavy selling
            elif sig.direction == Direction.SHORT and on_chain.volume_delta_pct > 0.3:
                boost -= 0.5  # shorting against heavy buying

            # OI buildup confirmation
            if on_chain.oi_signal == "buildup":
                boost += 0.3  # new money entering = higher conviction
            elif on_chain.oi_signal == "unwind":
                boost -= 0.3  # money leaving = lower conviction

            # Large trade imbalance
            large_diff = on_chain.large_buy_count - on_chain.large_sell_count
            if sig.direction == Direction.LONG and large_diff >= 2:
                boost += 0.5  # whales buying
            elif sig.direction == Direction.SHORT and large_diff <= -2:
                boost += 0.5  # whales selling

            sig.confidence_score = round(max(1.0, min(10.0, sig.confidence_score + boost)), 1)

        return signals

    def _deduplicate(self, signals: list[TradeSignal]) -> list[TradeSignal]:
        # Keep only the single highest-confidence signal per asset
        # Prevents contradictory LONG + SHORT on the same pair
        best: dict[str, TradeSignal] = {}
        for sig in signals:
            key = sig.asset
            if key not in best or sig.confidence_score > best[key].confidence_score:
                best[key] = sig
        return sorted(best.values(), key=lambda s: s.confidence_score, reverse=True)
