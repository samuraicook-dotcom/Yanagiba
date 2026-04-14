"""
backtest/engine.py — Historical Backtesting Engine

Simulates the full signal → risk → execution pipeline on historical OHLCV data.
Uses the same signal logic and charges calculator as live trading.
"""

import logging
from math import floor
from typing import Any

import numpy as np
import pandas as pd

from core.charges import ChargesCalculator
from core.signals import SignalEngine
from core.technicals import TechnicalEngine

logger = logging.getLogger("kaizen.backtest")


class BacktestEngine:
    """
    Runs the trading strategy on historical data and tracks performance metrics.

    Uses identical signal logic to the live system. Charges are applied
    to every trade using the ChargesCalculator for realistic PnL.
    """

    def __init__(self, config: dict) -> None:
        """
        Args:
            config: Full settings.yaml config dict
        """
        self.config = config
        self.risk_cfg = config["risk"]
        self.capital_cfg = config["capital"]

        self.technical_engine = TechnicalEngine(config["technicals"])
        self.signal_engine = SignalEngine(config["technicals"])
        self.charges_calc = ChargesCalculator()

        self.initial_capital: float = self.capital_cfg["initial_capital"]
        self.risk_per_trade_pct: float = self.risk_cfg["risk_per_trade_pct"]
        self.sl_atr_mult: float = self.risk_cfg["sl_atr_multiplier"]
        self.tp_atr_mult: float = self.risk_cfg["tp_atr_multiplier"]

    def run(
        self,
        df: pd.DataFrame,
        symbol: str = "UNKNOWN",
        sentiment_bias: str = "NEUTRAL",
    ) -> dict[str, Any]:
        """
        Run the backtest on a historical OHLCV DataFrame.

        Args:
            df: OHLCV DataFrame (columns: datetime, open, high, low, close, volume)
            symbol: Symbol name for reporting
            sentiment_bias: Static bias to use (default NEUTRAL)

        Returns:
            Results dict with trades list, equity_curve, and performance metrics
        """
        df = self.technical_engine.compute_all(df.copy())
        df = df.dropna(subset=["ema_fast", "ema_slow", "rsi", "atr"]).reset_index(drop=True)

        capital = self.initial_capital
        equity_curve: list[float] = [capital]
        trades: list[dict] = []

        in_trade = False
        trade_entry: dict = {}

        for i in range(2, len(df)):
            row = df.iloc[i]
            window = df.iloc[: i + 1]

            # Check if in trade and should exit (SL or TP hit this candle)
            if in_trade:
                exit_result = self._check_exit(row, trade_entry)
                if exit_result:
                    buy_p = trade_entry["entry_price"]
                    sell_p = exit_result["exit_price"]
                    qty = trade_entry["quantity"]

                    if trade_entry["direction"] == "BUY":
                        pnl = self.charges_calc.calculate_net_pnl(buy_p, sell_p, qty)
                    else:
                        pnl = self.charges_calc.calculate_net_pnl(sell_p, buy_p, qty)

                    net_pnl = pnl["net_pnl"]
                    capital += net_pnl

                    trade_record = {
                        **trade_entry,
                        "exit_price": exit_result["exit_price"],
                        "exit_reason": exit_result["exit_reason"],
                        "exit_index": i,
                        "gross_pnl": pnl["gross_pnl"],
                        "charges": pnl["charges"],
                        "net_pnl": net_pnl,
                        "capital_after": capital,
                    }
                    trades.append(trade_record)
                    in_trade = False
                    trade_entry = {}

                equity_curve.append(capital)
                continue

            # Generate signal
            tech_signal = self.signal_engine.generate_technical_signal(window)
            decision = self.signal_engine.combine_with_sentiment(
                tech_signal, sentiment_bias
            )

            if not decision.get("executable") or decision["action"] == "HOLD":
                equity_curve.append(capital)
                continue

            action = decision["action"]
            if action == "SHORT":
                # SHORT not executable on NSE ETFs
                equity_curve.append(capital)
                continue

            # Size position
            atr = float(row["atr"])
            entry_price = float(row["close"])
            size_factor = decision.get("size_factor", 1.0)

            sl_distance = atr * self.sl_atr_mult
            tp_distance = atr * self.tp_atr_mult

            if sl_distance <= 0:
                equity_curve.append(capital)
                continue

            risk_amount = capital * (self.risk_per_trade_pct / 100)
            quantity = floor(risk_amount / sl_distance)
            quantity = floor(quantity * size_factor)
            quantity = max(quantity, 1)

            sl_price = entry_price - sl_distance
            tp_price = entry_price + tp_distance

            in_trade = True
            trade_entry = {
                "symbol": symbol,
                "direction": "BUY",
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "quantity": quantity,
                "entry_index": i,
                "entry_time": str(row.get("datetime", i)),
                "tech_signal": tech_signal.get("signal"),
                "sentiment_bias": sentiment_bias,
                "size_factor": size_factor,
            }

            equity_curve.append(capital)

        # Force-close any remaining open trade at last candle
        if in_trade and len(df) > 0:
            last_row = df.iloc[-1]
            exit_price = float(last_row["close"])
            buy_p = trade_entry["entry_price"]
            qty = trade_entry["quantity"]
            pnl = self.charges_calc.calculate_net_pnl(buy_p, exit_price, qty)
            capital += pnl["net_pnl"]
            trade_record = {
                **trade_entry,
                "exit_price": exit_price,
                "exit_reason": "end_of_data",
                "exit_index": len(df) - 1,
                "gross_pnl": pnl["gross_pnl"],
                "charges": pnl["charges"],
                "net_pnl": pnl["net_pnl"],
                "capital_after": capital,
            }
            trades.append(trade_record)

        metrics = self._compute_metrics(trades, equity_curve)
        logger.info(
            "Backtest complete: %d trades, net_pnl=%.2f, win_rate=%.1f%%",
            len(trades),
            metrics["total_net_pnl"],
            metrics["win_rate_pct"],
        )

        return {
            "symbol": symbol,
            "sentiment_bias": sentiment_bias,
            "trades": trades,
            "equity_curve": equity_curve,
            "initial_capital": self.initial_capital,
            "final_capital": capital,
            "metrics": metrics,
        }

    def _check_exit(self, row: pd.Series, trade: dict) -> dict | None:
        """
        Check if SL or TP was hit during this candle.

        Checks intra-candle low/high against SL/TP levels.

        Args:
            row: Current candle
            trade: Open trade dict

        Returns:
            dict with exit_price and exit_reason, or None if no exit
        """
        low = float(row["low"])
        high = float(row["high"])
        sl = trade["sl_price"]
        tp = trade["tp_price"]
        direction = trade["direction"]

        if direction == "BUY":
            if low <= sl:
                return {"exit_price": sl, "exit_reason": "sl_hit"}
            if high >= tp:
                return {"exit_price": tp, "exit_reason": "tp_hit"}
        else:  # SHORT
            if high >= sl:
                return {"exit_price": sl, "exit_reason": "sl_hit"}
            if low <= tp:
                return {"exit_price": tp, "exit_reason": "tp_hit"}

        return None

    def _compute_metrics(
        self, trades: list[dict], equity_curve: list[float]
    ) -> dict[str, Any]:
        """
        Compute performance metrics from completed trades.

        Metrics:
          - total_trades, wins, losses, win_rate_pct
          - total_gross_pnl, total_net_pnl, total_charges
          - avg_win, avg_loss, profit_factor
          - max_drawdown
          - sharpe_ratio (annualised, assuming 252 trading days)
        """
        if not trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "total_gross_pnl": 0.0,
                "total_net_pnl": 0.0,
                "total_charges": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
            }

        net_pnls = [t["net_pnl"] for t in trades]
        wins = [p for p in net_pnls if p > 0]
        losses = [p for p in net_pnls if p <= 0]

        total_gross = sum(t["gross_pnl"] for t in trades)
        total_net = sum(net_pnls)
        total_charges = sum(t["charges"] for t in trades)

        win_rate = (len(wins) / len(trades)) * 100 if trades else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        profit_factor = (
            abs(sum(wins)) / abs(sum(losses)) if losses and sum(losses) != 0 else 0.0
        )

        # Max drawdown from equity curve
        max_dd = self._max_drawdown(equity_curve)

        # Annualised Sharpe (simplified: daily returns from equity curve)
        sharpe = self._sharpe_ratio(equity_curve)

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 2),
            "total_gross_pnl": round(total_gross, 2),
            "total_net_pnl": round(total_net, 2),
            "total_charges": round(total_charges, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 3),
            "max_drawdown": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 3),
        }

    @staticmethod
    def _max_drawdown(equity_curve: list[float]) -> float:
        """Compute maximum peak-to-trough drawdown."""
        if len(equity_curve) < 2:
            return 0.0
        arr = np.array(equity_curve)
        peak = np.maximum.accumulate(arr)
        drawdown = peak - arr
        return float(np.max(drawdown))

    @staticmethod
    def _sharpe_ratio(equity_curve: list[float], periods_per_year: int = 252 * 13) -> float:
        """
        Annualised Sharpe ratio.
        periods_per_year = 252 trading days × ~13 candles/day for 30-min bars.
        """
        if len(equity_curve) < 2:
            return 0.0
        arr = np.array(equity_curve)
        returns = np.diff(arr) / arr[:-1]
        returns = returns[returns != 0]
        if len(returns) < 2:
            return 0.0
        mean_r = np.mean(returns)
        std_r = np.std(returns)
        if std_r == 0:
            return 0.0
        return float((mean_r / std_r) * np.sqrt(periods_per_year))
