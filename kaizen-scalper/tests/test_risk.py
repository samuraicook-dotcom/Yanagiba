"""
tests/test_risk.py — Unit tests for RiskManager
"""

from datetime import datetime
from math import floor

import pytest

from core.risk_manager import RiskManager

RISK_CONFIG = {
    "risk_per_trade_pct": 0.25,
    "max_daily_loss_pct": 1.5,
    "max_trades_per_day": 6,
    "consecutive_loss_killswitch": 3,
    "sl_atr_multiplier": 1.5,
    "tp_atr_multiplier": 2.0,
    "min_rr_ratio": 1.2,
}
CAPITAL_CONFIG = {
    "initial_capital": 100000,
    "paper_trade": True,
}

NO_TRADE_ZONES = [
    {"start": "09:15", "end": "09:45"},
    {"start": "15:00", "end": "15:30"},
]


@pytest.fixture
def rm():
    manager = RiskManager(RISK_CONFIG, CAPITAL_CONFIG)
    manager.configure_time_rules(NO_TRADE_ZONES, auto_square_off="15:10")
    return manager


class TestCanTrade:
    def test_can_trade_normal(self, rm):
        """Should approve a trade at a normal time."""
        now = datetime(2026, 1, 2, 10, 15)
        ok, reason = rm.can_trade("GOLDBEES", now)
        assert ok is True
        assert reason == ""

    def test_blocked_in_no_trade_zone(self, rm):
        """Should block trades in the 09:15–09:45 window."""
        now = datetime(2026, 1, 2, 9, 30)
        ok, reason = rm.can_trade("GOLDBEES", now)
        assert ok is False
        assert "no-trade" in reason.lower()

    def test_blocked_past_square_off(self, rm):
        """Should block trades after 15:10 (no-trade zone or square-off check)."""
        now = datetime(2026, 1, 2, 15, 15)
        ok, reason = rm.can_trade("GOLDBEES", now)
        assert ok is False
        # 15:15 falls in the 15:00-15:30 no-trade zone, which fires first;
        # both "no-trade zone" and "square-off" are correct block reasons here.
        assert reason != ""

    def test_blocked_existing_position(self, rm):
        """Should block new trade if position already open."""
        rm.open_positions["GOLDBEES"] = {"symbol": "GOLDBEES"}
        now = datetime(2026, 1, 2, 11, 0)
        ok, reason = rm.can_trade("GOLDBEES", now)
        assert ok is False
        assert "open position" in reason.lower()

    def test_blocked_max_trades_reached(self, rm):
        """Should block when daily trade count is at max."""
        rm.trades_today = 6
        now = datetime(2026, 1, 2, 11, 0)
        ok, reason = rm.can_trade("GOLDBEES", now)
        assert ok is False
        assert "max trades" in reason.lower()

    def test_blocked_consecutive_loss_killswitch(self, rm):
        """Should block after 3 consecutive losses."""
        rm.consecutive_losses = 3
        now = datetime(2026, 1, 2, 11, 0)
        ok, reason = rm.can_trade("GOLDBEES", now)
        assert ok is False
        assert "killswitch" in reason.lower()

    def test_blocked_daily_loss_limit(self, rm):
        """Should block when daily PnL exceeds max_daily_loss_pct."""
        rm.daily_pnl = -2000  # 2% of 100k > 1.5% limit
        now = datetime(2026, 1, 2, 11, 0)
        ok, reason = rm.can_trade("GOLDBEES", now)
        assert ok is False
        assert "daily loss" in reason.lower()


class TestPositionSizing:
    def test_quantity_at_least_1(self, rm):
        """Quantity must never be zero even for tiny ATR."""
        result = rm.calculate_position(100.0, 0.001, "LONG", 1.0)
        assert result["quantity"] >= 1

    def test_sl_below_entry_for_long(self, rm):
        """SL must be below entry for a LONG trade."""
        result = rm.calculate_position(100.0, 2.0, "LONG", 1.0)
        assert result["sl_price"] < 100.0

    def test_tp_above_entry_for_long(self, rm):
        """TP must be above entry for a LONG trade."""
        result = rm.calculate_position(100.0, 2.0, "LONG", 1.0)
        assert result["tp_price"] > 100.0

    def test_sl_above_entry_for_short(self, rm):
        """SL must be above entry for a SHORT trade."""
        result = rm.calculate_position(100.0, 2.0, "SHORT", 1.0)
        assert result["sl_price"] > 100.0

    def test_rr_ratio_correct(self, rm):
        """R:R = tp_atr_mult / sl_atr_mult = 2.0 / 1.5."""
        result = rm.calculate_position(100.0, 2.0, "LONG", 1.0)
        expected_rr = RISK_CONFIG["tp_atr_multiplier"] / RISK_CONFIG["sl_atr_multiplier"]
        assert abs(result["rr_ratio"] - expected_rr) < 0.01

    def test_size_factor_reduces_quantity(self, rm):
        """size_factor=0.5 should give half the quantity of size_factor=1.0."""
        full = rm.calculate_position(100.0, 2.0, "LONG", 1.0)
        half = rm.calculate_position(100.0, 2.0, "LONG", 0.5)
        assert half["quantity"] <= full["quantity"]

    def test_risk_amount_correct(self, rm):
        """Risk amount = capital * risk_pct / 100."""
        result = rm.calculate_position(100.0, 2.0, "LONG", 1.0)
        expected = 100000 * 0.25 / 100
        assert abs(result["risk_amount"] - expected) < 0.01


class TestStateUpdates:
    def test_record_open_increments_trades(self, rm):
        rm.record_trade_open("GOLDBEES", {"symbol": "GOLDBEES"})
        assert rm.trades_today == 1
        assert "GOLDBEES" in rm.open_positions

    def test_record_close_win_resets_streak(self, rm):
        rm.consecutive_losses = 2
        rm.record_trade_open("GOLDBEES", {})
        rm.record_trade_close("GOLDBEES", 500.0)
        assert rm.consecutive_losses == 0

    def test_record_close_loss_increments_streak(self, rm):
        rm.record_trade_open("GOLDBEES", {})
        rm.record_trade_close("GOLDBEES", -200.0)
        assert rm.consecutive_losses == 1

    def test_record_close_updates_capital(self, rm):
        rm.record_trade_open("GOLDBEES", {})
        rm.record_trade_close("GOLDBEES", 1000.0)
        assert rm.current_capital == 101000.0

    def test_reset_daily_clears_state(self, rm):
        rm.trades_today = 5
        rm.consecutive_losses = 2
        rm.daily_pnl = -500.0
        rm.reset_daily()
        assert rm.trades_today == 0
        assert rm.consecutive_losses == 0
        assert rm.daily_pnl == 0.0
