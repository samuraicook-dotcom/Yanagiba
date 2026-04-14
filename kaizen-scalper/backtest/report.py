"""
backtest/report.py — Backtest Performance Report Generator

Formats backtest results into a human-readable report.
Prints to console and saves to file.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("kaizen.backtest.report")

DIVIDER = "=" * 60
THIN_LINE = "-" * 60


def generate_report(results: dict[str, Any], output_path: str | None = None) -> str:
    """
    Generate a formatted performance report from backtest results.

    Args:
        results: Output dict from BacktestEngine.run()
        output_path: Optional file path to save report (default: auto-generated)

    Returns:
        Report string
    """
    m = results["metrics"]
    symbol = results.get("symbol", "UNKNOWN")
    bias = results.get("sentiment_bias", "NEUTRAL")
    initial = results.get("initial_capital", 0)
    final = results.get("final_capital", 0)
    pnl_pct = ((final - initial) / initial * 100) if initial else 0.0

    trades = results.get("trades", [])
    date_range = _get_date_range(trades)

    lines = [
        DIVIDER,
        "  KAIZEN SCALPER — BACKTEST REPORT",
        DIVIDER,
        f"  Symbol        : {symbol}",
        f"  Sentiment Bias: {bias}",
        f"  Date Range    : {date_range}",
        f"  Generated     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        THIN_LINE,
        "  CAPITAL",
        THIN_LINE,
        f"  Initial Capital : ₹{initial:>12,.2f}",
        f"  Final Capital   : ₹{final:>12,.2f}",
        f"  Total Return    : ₹{final - initial:>+12,.2f}  ({pnl_pct:+.2f}%)",
        THIN_LINE,
        "  TRADE STATISTICS",
        THIN_LINE,
        f"  Total Trades    : {m['total_trades']}",
        f"  Wins            : {m['wins']}",
        f"  Losses          : {m['losses']}",
        f"  Win Rate        : {m['win_rate_pct']:.2f}%",
        f"  Avg Win         : ₹{m['avg_win']:>10,.2f}",
        f"  Avg Loss        : ₹{m['avg_loss']:>10,.2f}",
        THIN_LINE,
        "  PnL BREAKDOWN",
        THIN_LINE,
        f"  Gross PnL       : ₹{m['total_gross_pnl']:>+12,.2f}",
        f"  Total Charges   : ₹{m['total_charges']:>12,.2f}",
        f"  Net PnL         : ₹{m['total_net_pnl']:>+12,.2f}",
        THIN_LINE,
        "  RISK METRICS",
        THIN_LINE,
        f"  Max Drawdown    : ₹{m['max_drawdown']:>12,.2f}",
        f"  Profit Factor   : {m['profit_factor']:.3f}",
        f"  Sharpe Ratio    : {m['sharpe_ratio']:.3f}",
        DIVIDER,
    ]

    # Trade log (last 20 trades)
    if trades:
        lines.append("  RECENT TRADES (last 20)")
        lines.append(THIN_LINE)
        header = f"  {'#':>3}  {'Entry':>10}  {'Exit':>10}  {'Dir':>4}  {'Qty':>5}  {'Net PnL':>10}  {'Reason'}"
        lines.append(header)
        lines.append(THIN_LINE)
        for i, t in enumerate(trades[-20:], 1):
            row = (
                f"  {i:>3}  "
                f"₹{t['entry_price']:>9.2f}  "
                f"₹{t.get('exit_price', 0):>9.2f}  "
                f"{t['direction']:>4}  "
                f"{t['quantity']:>5}  "
                f"₹{t['net_pnl']:>+9.2f}  "
                f"{t.get('exit_reason', '-')}"
            )
            lines.append(row)
        lines.append(DIVIDER)

    report = "\n".join(lines)

    # Print to console
    print(report)

    # Save to file
    if output_path is None:
        Path("data").mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"data/backtest_{symbol}_{ts}.txt"

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info("Backtest report saved to %s", output_path)
    except Exception as exc:
        logger.error("Failed to save report: %s", exc)

    return report


def _get_date_range(trades: list[dict]) -> str:
    """Extract date range from trade list."""
    if not trades:
        return "N/A"
    times = [t.get("entry_time", "") for t in trades if t.get("entry_time")]
    if not times:
        return "N/A"
    return f"{min(times)[:10]} to {max(times)[:10]}"
