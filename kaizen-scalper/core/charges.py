"""
core/charges.py — Indian NSE Intraday (MIS) Transaction Charges Calculator

Handles exact charge calculation for NSE intraday trades.
ALL PnL calculations in this project must go through this module.
"""

from dataclasses import dataclass


@dataclass
class ChargeBreakdown:
    """Full breakdown of charges for a round-trip intraday trade."""
    brokerage_buy: float
    brokerage_sell: float
    stt: float
    exchange_txn: float
    gst: float
    sebi_fee: float
    stamp_duty: float
    total_charges: float


class ChargesCalculator:
    """
    Calculates NSE intraday (MIS) transaction charges.

    Charge rates (as of FY2024-25):
    - Brokerage: ₹20 per executed order OR 0.03% of turnover, whichever is lower
    - STT: 0.025% on sell side only (intraday equity)
    - Exchange Txn Fee: 0.00345% on turnover (NSE)
    - GST: 18% on (brokerage + exchange txn charges)
    - SEBI Turnover Fee: 0.0001% on turnover
    - Stamp Duty: 0.003% on buy side only
    """

    # Brokerage constants
    BROKERAGE_FLAT: float = 20.0          # ₹20 per order
    BROKERAGE_PCT: float = 0.03 / 100     # 0.03% of turnover

    # Statutory charges
    STT_PCT: float = 0.025 / 100          # 0.025% on sell turnover only
    EXCHANGE_TXN_PCT: float = 0.00345 / 100  # NSE exchange transaction charge
    GST_PCT: float = 0.18                 # 18% on brokerage + exchange txn
    SEBI_FEE_PCT: float = 0.0001 / 100   # SEBI turnover fee
    STAMP_DUTY_PCT: float = 0.003 / 100  # 0.003% on buy side only

    def _brokerage(self, price: float, quantity: int) -> float:
        """
        Calculate brokerage for a single order leg.
        Returns min(₹20, 0.03% of turnover).
        """
        turnover = price * quantity
        return min(self.BROKERAGE_FLAT, turnover * self.BROKERAGE_PCT)

    def calculate_charges(
        self,
        buy_price: float,
        sell_price: float,
        quantity: int,
    ) -> dict:
        """
        Calculate complete charge breakdown for a round-trip intraday trade.

        Args:
            buy_price: Entry price per unit (₹)
            sell_price: Exit price per unit (₹)
            quantity: Number of units traded

        Returns:
            dict with full charge breakdown and total_charges key
        """
        buy_turnover = buy_price * quantity
        sell_turnover = sell_price * quantity

        brokerage_buy = self._brokerage(buy_price, quantity)
        brokerage_sell = self._brokerage(sell_price, quantity)
        total_brokerage = brokerage_buy + brokerage_sell

        # STT: only on sell side for intraday
        stt = sell_turnover * self.STT_PCT

        # Exchange transaction charge: both legs
        exchange_txn = (buy_turnover + sell_turnover) * self.EXCHANGE_TXN_PCT

        # GST on brokerage + exchange txn charges
        gst = (total_brokerage + exchange_txn) * self.GST_PCT

        # SEBI turnover fee: both legs
        sebi_fee = (buy_turnover + sell_turnover) * self.SEBI_FEE_PCT

        # Stamp duty: buy side only
        stamp_duty = buy_turnover * self.STAMP_DUTY_PCT

        total_charges = (
            total_brokerage + stt + exchange_txn + gst + sebi_fee + stamp_duty
        )

        return {
            "brokerage_buy": round(brokerage_buy, 4),
            "brokerage_sell": round(brokerage_sell, 4),
            "total_brokerage": round(total_brokerage, 4),
            "stt": round(stt, 4),
            "exchange_txn": round(exchange_txn, 4),
            "gst": round(gst, 4),
            "sebi_fee": round(sebi_fee, 4),
            "stamp_duty": round(stamp_duty, 4),
            "total_charges": round(total_charges, 4),
        }

    def calculate_net_pnl(
        self,
        buy_price: float,
        sell_price: float,
        quantity: int,
    ) -> dict:
        """
        Calculate gross PnL, charges, and net PnL for a round-trip trade.

        Args:
            buy_price: Entry price per unit (₹)
            sell_price: Exit price per unit (₹)
            quantity: Number of units traded

        Returns:
            dict with gross_pnl, charges, net_pnl
        """
        gross_pnl = (sell_price - buy_price) * quantity
        charge_breakdown = self.calculate_charges(buy_price, sell_price, quantity)
        total_charges = charge_breakdown["total_charges"]
        net_pnl = gross_pnl - total_charges

        return {
            "gross_pnl": round(gross_pnl, 4),
            "charges": round(total_charges, 4),
            "net_pnl": round(net_pnl, 4),
            "charge_breakdown": charge_breakdown,
        }

    def breakeven_move_pct(self, price: float, quantity: int) -> float:
        """
        Calculate the minimum price move percentage needed to cover all charges.

        Assumes a round-trip trade (buy at `price`, sell at `price + delta`).
        Solves for delta such that net_pnl == 0.

        Args:
            price: Entry price per unit (₹)
            quantity: Number of units

        Returns:
            Minimum move as a percentage of entry price (e.g. 0.12 means 0.12%)
        """
        # Binary search for breakeven price
        low, high = price, price * 1.10
        for _ in range(60):
            mid = (low + high) / 2
            result = self.calculate_net_pnl(price, mid, quantity)
            if result["net_pnl"] < 0:
                low = mid
            else:
                high = mid

        breakeven_price = (low + high) / 2
        move_pct = ((breakeven_price - price) / price) * 100
        return round(move_pct, 6)
