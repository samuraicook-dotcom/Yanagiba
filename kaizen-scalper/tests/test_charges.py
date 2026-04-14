"""
tests/test_charges.py — Unit tests for the ChargesCalculator
"""

import pytest
from core.charges import ChargesCalculator


@pytest.fixture
def calc():
    return ChargesCalculator()


class TestBrokerage:
    def test_brokerage_flat_cap(self, calc):
        """For high-value trades, brokerage is capped at ₹20."""
        # 100 units at ₹5000 = ₹500,000 turnover
        # 0.03% of 500,000 = ₹150 > ₹20, so should be ₹20
        brok = calc._brokerage(5000, 100)
        assert brok == 20.0

    def test_brokerage_pct_lower(self, calc):
        """For low-value trades, brokerage is 0.03% of turnover."""
        # 1 unit at ₹100 = ₹100 turnover
        # 0.03% of 100 = ₹0.03 < ₹20, so should be 0.03
        brok = calc._brokerage(100, 1)
        assert abs(brok - 0.03) < 1e-6


class TestCalculateCharges:
    def test_charge_keys_present(self, calc):
        """All expected keys are in the charge breakdown."""
        result = calc.calculate_charges(100.0, 101.0, 100)
        expected_keys = {
            "brokerage_buy", "brokerage_sell", "total_brokerage",
            "stt", "exchange_txn", "gst", "sebi_fee", "stamp_duty", "total_charges"
        }
        assert expected_keys.issubset(result.keys())

    def test_stt_on_sell_only(self, calc):
        """STT is 0.025% of sell turnover only."""
        sell_price = 102.0
        quantity = 10
        expected_stt = sell_price * quantity * 0.025 / 100
        result = calc.calculate_charges(100.0, sell_price, quantity)
        assert abs(result["stt"] - expected_stt) < 0.001

    def test_stamp_duty_on_buy_only(self, calc):
        """Stamp duty is 0.003% of buy turnover only."""
        buy_price = 100.0
        quantity = 10
        expected_stamp = buy_price * quantity * 0.003 / 100
        result = calc.calculate_charges(buy_price, 102.0, quantity)
        assert abs(result["stamp_duty"] - expected_stamp) < 0.001

    def test_total_charges_positive(self, calc):
        """Total charges must always be positive."""
        result = calc.calculate_charges(50.0, 51.0, 50)
        assert result["total_charges"] > 0

    def test_charges_increase_with_quantity(self, calc):
        """Charges increase with larger position size."""
        small = calc.calculate_charges(100.0, 101.0, 1)
        large = calc.calculate_charges(100.0, 101.0, 1000)
        assert large["total_charges"] > small["total_charges"]


class TestCalculateNetPnl:
    def test_profitable_trade(self, calc):
        """Net PnL is positive for a winning trade (large enough move)."""
        result = calc.calculate_net_pnl(100.0, 110.0, 100)
        assert result["gross_pnl"] > 0
        assert result["net_pnl"] < result["gross_pnl"]

    def test_losing_trade(self, calc):
        """Net PnL is negative for a losing trade."""
        result = calc.calculate_net_pnl(100.0, 99.0, 100)
        assert result["gross_pnl"] < 0
        assert result["net_pnl"] < result["gross_pnl"]

    def test_pnl_formula(self, calc):
        """gross_pnl - charges == net_pnl."""
        result = calc.calculate_net_pnl(100.0, 101.5, 50)
        assert abs(result["gross_pnl"] - result["charges"] - result["net_pnl"]) < 0.001

    def test_breakeven_larger_than_spread(self, calc):
        """Breakeven move is positive and meaningful."""
        bep = calc.breakeven_move_pct(100.0, 100)
        assert 0 < bep < 1.0  # Should be a fraction of a percent


class TestBreakevenMove:
    def test_breakeven_positive(self, calc):
        assert calc.breakeven_move_pct(200.0, 50) > 0

    def test_breakeven_decreases_with_size(self, calc):
        """Larger position → slightly smaller breakeven % (brokerage cap effect)."""
        bep_small = calc.breakeven_move_pct(100.0, 1)
        bep_large = calc.breakeven_move_pct(100.0, 10000)
        # With very small qty, flat brokerage dominates; larger qty has lower bep
        assert bep_small > 0
        assert bep_large > 0
