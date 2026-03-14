"""Trade Journal — Persists every trade to JSON for analysis.

Tracks:
- Every signal generated (approved or rejected)
- Every trade executed (with outcome)
- Win rate, average R:R, drawdown, best/worst trades
- Strategy-level performance (which strategies actually make money)

This data is ESSENTIAL for tuning the bot — without it you're flying blind.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

JOURNAL_DIR = Path.home() / "Yanagiba" / "journal"


class TradeJournal:
    """Persists trade data for performance analysis."""

    def __init__(self, journal_dir: Path | None = None):
        self.journal_dir = journal_dir or JOURNAL_DIR
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = self.journal_dir / "trades.jsonl"
        self.stats_file = self.journal_dir / "stats.json"
        self._stats = self._load_stats()

    def _load_stats(self) -> dict:
        if self.stats_file.exists():
            try:
                return json.loads(self.stats_file.read_text())
            except Exception:
                pass
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "peak_value": 0.0,
            "strategy_stats": {},
        }

    def _save_stats(self):
        try:
            self.stats_file.write_text(json.dumps(self._stats, indent=2))
        except Exception as e:
            logger.warning(f"Could not save stats: {e}")

    def log_trade(
        self,
        symbol: str,
        direction: str,
        strategy: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        position_size: float,
        confidence: float,
        risk_reward: float,
        status: str,
        pnl: float = 0.0,
    ):
        """Log a trade to the journal."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "direction": direction,
            "strategy": strategy,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "position_size": position_size,
            "confidence": confidence,
            "risk_reward": risk_reward,
            "status": status,
            "pnl": pnl,
        }

        # Append to JSONL file
        try:
            with open(self.trades_file, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.warning(f"Could not write trade journal: {e}")

        # Update stats — count trade entry (wins/losses tracked at close time)
        if status in ("placed", "simulated", "closed"):
            self._stats["total_trades"] += 1

            # Strategy-level tracking (trade count at entry)
            strat = self._stats["strategy_stats"].setdefault(strategy, {
                "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
            })
            strat["trades"] += 1

            self._save_stats()

    def close_trade(
        self,
        symbol: str,
        pnl: float,
        close_type: str = "closed",
        strategy: str = "",
    ):
        """Record a trade closure with PnL. Updates win/loss stats."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "status": "closed",
            "close_type": close_type,
            "pnl": pnl,
            "strategy": strategy,
        }

        # Append to JSONL
        try:
            with open(self.trades_file, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.warning(f"Could not write trade close: {e}")

        # Update PnL and wins/losses at close time (when we know the outcome)
        if pnl > 0:
            self._stats["wins"] += 1
        elif pnl < 0:
            self._stats["losses"] += 1
        self._stats["total_pnl"] += pnl

        # Update per-strategy stats (wins, losses, pnl)
        if strategy:
            strat = self._stats["strategy_stats"].setdefault(strategy, {
                "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
            })
            strat["pnl"] += pnl
            if pnl > 0:
                strat["wins"] += 1
            elif pnl < 0:
                strat["losses"] += 1

        self._save_stats()

    def update_drawdown(self, portfolio_value: float):
        """Track max drawdown for risk monitoring."""
        changed = False
        if portfolio_value > self._stats["peak_value"]:
            self._stats["peak_value"] = portfolio_value
            changed = True
        if self._stats["peak_value"] > 0:
            drawdown = (self._stats["peak_value"] - portfolio_value) / self._stats["peak_value"]
            if drawdown > self._stats["max_drawdown"]:
                self._stats["max_drawdown"] = drawdown
                changed = True
        if changed:
            self._save_stats()

    def get_performance(self) -> dict:
        """Get current performance metrics."""
        total = self._stats["total_trades"]
        wins = self._stats["wins"]
        return {
            "total_trades": total,
            "wins": wins,
            "losses": self._stats["losses"],
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(self._stats["total_pnl"], 2),
            "max_drawdown": round(self._stats["max_drawdown"] * 100, 1),
            "best_strategy": self._best_strategy(),
            "worst_strategy": self._worst_strategy(),
        }

    def _best_strategy(self) -> str:
        strats = self._stats.get("strategy_stats", {})
        if not strats:
            return "N/A"
        return max(strats, key=lambda s: strats[s]["pnl"])

    def _worst_strategy(self) -> str:
        strats = self._stats.get("strategy_stats", {})
        if not strats:
            return "N/A"
        return min(strats, key=lambda s: strats[s]["pnl"])
