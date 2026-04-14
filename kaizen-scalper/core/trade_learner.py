"""
core/trade_learner.py — Adaptive Learning from Trade History

Tracks per-asset, per-signal-type win rates over a rolling window.
Adjusts size_factor and enforces cooldown periods when the bot is losing.

Rules:
  - Rolling window: last 10 closed trades per asset
  - Win rate >= 50%  → full size (size_factor unchanged)
  - Win rate 30-49%  → half size (size_factor *= 0.5)
  - Win rate < 30%   → cooldown: skip next 3 cycles for this asset
  - 3+ consecutive losses on any asset → cooldown: skip next 5 cycles
  - Logs reason every time it overrides size or blocks a trade
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("kaizen.learner")

WINDOW_SIZE = 10          # Rolling trade history per asset
COOLDOWN_LOW_WINRATE = 3  # Cycles to skip when win_rate < 30%
COOLDOWN_CONSEC_LOSS = 5  # Cycles to skip after 3 consecutive losses
WINRATE_FULL = 0.50       # >= 50% → full size
WINRATE_HALF = 0.30       # 30-49% → half size; < 30% → cooldown


@dataclass
class AssetStats:
    """Rolling performance stats for a single asset."""
    symbol: str
    outcomes: deque = field(default_factory=lambda: deque(maxlen=WINDOW_SIZE))
    consecutive_losses: int = 0
    cooldown_cycles_remaining: int = 0

    def record(self, net_pnl: float) -> None:
        """Record a trade outcome (True=win, False=loss)."""
        win = net_pnl > 0
        self.outcomes.append(win)
        if win:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

    @property
    def win_rate(self) -> float:
        if not self.outcomes:
            return 1.0  # No history → optimistic
        return sum(self.outcomes) / len(self.outcomes)

    @property
    def sample_size(self) -> int:
        return len(self.outcomes)


class TradeLearner:
    """
    Monitors trade outcomes per asset and adapts position sizing dynamically.

    The executor calls:
      - record_outcome(symbol, net_pnl)  after every trade closes
      - get_size_adjustment(symbol)      before every new trade
      - tick_cooldown(symbol)            at the start of each cycle
    """

    def __init__(self) -> None:
        self._stats: dict[str, AssetStats] = {}

    def _get_or_create(self, symbol: str) -> AssetStats:
        if symbol not in self._stats:
            self._stats[symbol] = AssetStats(symbol=symbol)
        return self._stats[symbol]

    # ------------------------------------------------------------------
    # Record outcomes
    # ------------------------------------------------------------------

    def record_outcome(self, symbol: str, net_pnl: float) -> None:
        """
        Record the result of a closed trade and adjust the asset's stats.

        Args:
            symbol: Trading symbol
            net_pnl: Net PnL after charges (positive = win, negative = loss)
        """
        stats = self._get_or_create(symbol)
        was_win = net_pnl > 0
        stats.record(net_pnl)

        logger.info(
            "Learner [%s]: %s | consec_losses=%d | win_rate=%.0f%% (%d trades)",
            symbol,
            "WIN ✅" if was_win else "LOSS ❌",
            stats.consecutive_losses,
            stats.win_rate * 100,
            stats.sample_size,
        )

        # Trigger cooldown if rules breach
        if stats.consecutive_losses >= 3 and stats.cooldown_cycles_remaining == 0:
            stats.cooldown_cycles_remaining = COOLDOWN_CONSEC_LOSS
            logger.warning(
                "Learner [%s]: 3 consecutive losses — COOLDOWN %d cycles",
                symbol, COOLDOWN_CONSEC_LOSS,
            )
            return

        if stats.sample_size >= 5 and stats.win_rate < WINRATE_HALF:
            if stats.cooldown_cycles_remaining == 0:
                stats.cooldown_cycles_remaining = COOLDOWN_LOW_WINRATE
                logger.warning(
                    "Learner [%s]: win_rate=%.0f%% < 30%% — COOLDOWN %d cycles",
                    symbol, stats.win_rate * 100, COOLDOWN_LOW_WINRATE,
                )

    # ------------------------------------------------------------------
    # Size adjustment
    # ------------------------------------------------------------------

    def get_size_adjustment(self, symbol: str) -> tuple[float, str]:
        """
        Return a size multiplier and reason string for the next trade.

        Returns:
            (multiplier, reason)
            multiplier=1.0 → full size
            multiplier=0.5 → half size
            multiplier=0.0 → skip (cooldown active)
        """
        stats = self._get_or_create(symbol)

        if stats.cooldown_cycles_remaining > 0:
            return 0.0, (
                f"cooldown ({stats.cooldown_cycles_remaining} cycles left, "
                f"consec_losses={stats.consecutive_losses}, "
                f"win_rate={stats.win_rate*100:.0f}%)"
            )

        if stats.sample_size < 3:
            return 1.0, "insufficient_history_full_size"

        wr = stats.win_rate
        if wr >= WINRATE_FULL:
            return 1.0, f"win_rate={wr*100:.0f}%_full_size"
        elif wr >= WINRATE_HALF:
            return 0.5, f"win_rate={wr*100:.0f}%_half_size"
        else:
            # Should have been put in cooldown by record_outcome,
            # but guard here too
            return 0.0, f"win_rate={wr*100:.0f}%_below_threshold"

    # ------------------------------------------------------------------
    # Cooldown tick
    # ------------------------------------------------------------------

    def tick_cooldown(self, symbol: str) -> None:
        """
        Decrement cooldown counter by 1. Call at the start of each trading cycle
        for every asset.
        """
        stats = self._get_or_create(symbol)
        if stats.cooldown_cycles_remaining > 0:
            stats.cooldown_cycles_remaining -= 1
            logger.info(
                "Learner [%s]: cooldown tick → %d cycles remaining",
                symbol, stats.cooldown_cycles_remaining,
            )
            if stats.cooldown_cycles_remaining == 0:
                logger.info("Learner [%s]: cooldown expired — resuming normal trading", symbol)

    # ------------------------------------------------------------------
    # State reporting
    # ------------------------------------------------------------------

    def get_summary(self) -> dict:
        """Return a dict summary of all asset stats for logging/Telegram."""
        summary = {}
        for symbol, stats in self._stats.items():
            summary[symbol] = {
                "win_rate_pct": round(stats.win_rate * 100, 1),
                "sample_size": stats.sample_size,
                "consecutive_losses": stats.consecutive_losses,
                "cooldown_remaining": stats.cooldown_cycles_remaining,
            }
        return summary

    def load_from_db(self, db) -> None:
        """
        Bootstrap learner from recent DB trade history on startup.
        Loads last WINDOW_SIZE closed trades per asset.

        Args:
            db: TradeDB instance
        """
        try:
            conn = db._connect()
            rows = conn.execute(
                """
                SELECT symbol, net_pnl FROM trades
                WHERE status = 'CLOSED' AND net_pnl IS NOT NULL
                ORDER BY id DESC
                LIMIT 200
                """
            ).fetchall()
            conn.close()

            # Process in chronological order (reversed) per symbol
            by_symbol: dict[str, list[float]] = {}
            for row in reversed(rows):
                sym = row["symbol"]
                by_symbol.setdefault(sym, []).append(row["net_pnl"])

            for sym, pnls in by_symbol.items():
                stats = self._get_or_create(sym)
                for pnl in pnls[-WINDOW_SIZE:]:
                    stats.record(pnl)

            logger.info("Learner: bootstrapped from DB for %d assets", len(by_symbol))
        except Exception as exc:
            logger.warning("Learner: could not load from DB: %s", exc)
