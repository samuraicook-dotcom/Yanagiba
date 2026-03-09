"""CME Gap Tracker — Detects BTC CME futures gaps for trade bias.

CME gaps form when BTC moves over the weekend while CME futures are
closed (Friday 21:00 UTC close → Sunday 23:00 UTC open).

Historical fill rate: 65-98% depending on timeframe. The bot uses
open gaps to bias BTC trade direction toward the gap fill.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# CME closes Friday 21:00 UTC, opens Sunday 23:00 UTC
CME_CLOSE_DAY = 4  # Friday
CME_CLOSE_HOUR = 21
CME_OPEN_DAY = 6  # Sunday
CME_OPEN_HOUR = 23

GAP_FILE = Path.home() / "Yanagiba" / "journal" / "cme_gaps.json"


class CMEGapTracker:
    """Tracks BTC CME gaps and provides directional bias."""

    def __init__(self):
        self.gaps: list[dict] = []
        self._load()

    def _load(self):
        """Load persisted gaps from disk."""
        if GAP_FILE.exists():
            try:
                self.gaps = json.loads(GAP_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                self.gaps = []

    def _save(self):
        """Persist gaps to disk."""
        GAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        GAP_FILE.write_text(json.dumps(self.gaps, indent=2))

    def check_and_record_gap(
        self, friday_close: float, sunday_open: float,
    ) -> dict | None:
        """Record a new CME gap if significant (>1% move)."""
        if friday_close <= 0 or sunday_open <= 0:
            return None

        gap_pct = (sunday_open - friday_close) / friday_close
        if abs(gap_pct) < 0.01:  # ignore gaps < 1%
            return None

        gap = {
            "friday_close": friday_close,
            "sunday_open": sunday_open,
            "gap_pct": round(gap_pct, 4),
            "fill_target": friday_close,
            "direction": "down" if gap_pct > 0 else "up",
            "created": datetime.now(timezone.utc).isoformat(),
            "filled": False,
        }
        self.gaps.append(gap)
        self._save()
        logger.info(
            f"CME GAP detected: {gap_pct:+.2%} "
            f"(fill target: ${friday_close:,.0f})"
        )
        return gap

    def check_fills(self, current_price: float):
        """Check if any open gaps have been filled."""
        for gap in self.gaps:
            if gap["filled"]:
                continue
            target = gap["fill_target"]
            if gap["direction"] == "down" and current_price <= target:
                gap["filled"] = True
                gap["filled_at"] = current_price
                gap["filled_date"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                logger.info(
                    f"CME GAP FILLED: target ${target:,.0f} "
                    f"(price ${current_price:,.0f})"
                )
            elif gap["direction"] == "up" and current_price >= target:
                gap["filled"] = True
                gap["filled_at"] = current_price
                gap["filled_date"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                logger.info(
                    f"CME GAP FILLED: target ${target:,.0f} "
                    f"(price ${current_price:,.0f})"
                )
        self._save()

    def get_bias(self, current_price: float) -> float:
        """Get directional bias from open CME gaps.

        Returns:
            float: Positive = bullish bias (gap below, price should
                   fill down). Negative = bearish bias. 0 = no gap.
        """
        self.check_fills(current_price)
        open_gaps = [g for g in self.gaps if not g["filled"]]
        if not open_gaps:
            return 0.0

        # Use the most recent open gap
        latest = open_gaps[-1]
        gap_distance = (
            (current_price - latest["fill_target"])
            / current_price
        )
        # Bias toward fill: if gap is below, bearish bias
        # If gap is above, bullish bias
        if latest["direction"] == "down":
            return -abs(gap_distance) * 10  # bearish
        return abs(gap_distance) * 10  # bullish

    @staticmethod
    def is_cme_close_window() -> bool:
        """Check if we're near CME Friday close (record price)."""
        now = datetime.now(timezone.utc)
        return (
            now.weekday() == CME_CLOSE_DAY
            and CME_CLOSE_HOUR - 1 <= now.hour <= CME_CLOSE_HOUR
        )

    @staticmethod
    def is_cme_open_window() -> bool:
        """Check if we're near CME Sunday open (check for gap)."""
        now = datetime.now(timezone.utc)
        return (
            now.weekday() == CME_OPEN_DAY
            and CME_OPEN_HOUR <= now.hour <= CME_OPEN_HOUR + 1
        )
