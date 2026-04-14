"""
core/risk_manager.py — Risk Management and Position Sizing

Performs pre-trade checks (kill-switch logic, daily limits, time filters)
and calculates position size, SL, and TP based on ATR and config.
"""

import logging
from datetime import datetime, time
from math import floor
from typing import Any

logger = logging.getLogger("kaizen.risk")


class RiskManager:
    """
    Enforces all risk rules before trade execution and sizes positions correctly.

    All checks must pass before an order is allowed. Any single failure blocks
    the trade with a descriptive reason.
    """

    def __init__(self, config: dict, capital: dict) -> None:
        """
        Args:
            config: risk section from settings.yaml
            capital: capital section from settings.yaml
        """
        self.risk_per_trade_pct: float = config["risk_per_trade_pct"]
        self.max_daily_loss_pct: float = config["max_daily_loss_pct"]
        self.max_trades_per_day: int = config["max_trades_per_day"]
        self.consecutive_loss_killswitch: int = config["consecutive_loss_killswitch"]
        self.sl_atr_multiplier: float = config["sl_atr_multiplier"]
        self.tp_atr_multiplier: float = config["tp_atr_multiplier"]
        self.min_rr_ratio: float = config["min_rr_ratio"]

        self.initial_capital: float = capital["initial_capital"]

        # Parse auto square-off time from execution config (stored externally)
        self._auto_square_off_str: str = "15:10"
        self._no_trade_zones: list[tuple[time, time]] = []

        # Mutable state — reset daily
        self.current_capital: float = self.initial_capital
        self.daily_pnl: float = 0.0
        self.trades_today: int = 0
        self.consecutive_losses: int = 0
        self.open_positions: dict[str, Any] = {}  # symbol → position dict

    def configure_time_rules(
        self,
        no_trade_zones: list[dict],
        auto_square_off: str,
    ) -> None:
        """
        Load time-based rules from config.

        Args:
            no_trade_zones: List of {start, end} dicts from timeframe config
            auto_square_off: HH:MM string from execution config
        """
        self._auto_square_off_str = auto_square_off
        self._no_trade_zones = []
        for zone in no_trade_zones:
            start = self._parse_time(zone["start"])
            end = self._parse_time(zone["end"])
            self._no_trade_zones.append((start, end))

    @staticmethod
    def _parse_time(t_str: str) -> time:
        """Parse 'HH:MM' string to time object."""
        h, m = t_str.split(":")
        return time(int(h), int(m))

    def _in_no_trade_zone(self, now: time) -> bool:
        """Return True if current time falls in any no-trade zone."""
        for start, end in self._no_trade_zones:
            if start <= now <= end:
                return True
        return False

    def _past_auto_square_off(self, now: time) -> bool:
        """Return True if current time is at or past auto square-off time."""
        sq_time = self._parse_time(self._auto_square_off_str)
        return now >= sq_time

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    def can_trade(self, symbol: str, now: datetime | None = None) -> tuple[bool, str]:
        """
        Run all pre-trade checks. All must pass for a trade to proceed.

        Checks (in order):
          1. Daily PnL loss < max_daily_loss_pct
          2. Trades today < max_trades_per_day
          3. Consecutive losses < killswitch threshold
          4. Current time not in no_trade_zone
          5. No existing open position in same asset
          6. Current time < auto_square_off time

        Args:
            symbol: Trading symbol (e.g. 'GOLDBEES')
            now: Datetime to use for time checks (defaults to current time)

        Returns:
            (True, '') if all checks pass
            (False, reason_string) if any check fails
        """
        if now is None:
            now = datetime.now()

        current_time = now.time()
        max_loss_amount = self.current_capital * (self.max_daily_loss_pct / 100)

        # 1. Daily loss limit
        if self.daily_pnl < -max_loss_amount:
            reason = (
                f"Daily loss limit hit: PnL={self.daily_pnl:.2f}, "
                f"limit={-max_loss_amount:.2f}"
            )
            logger.warning(reason)
            return False, reason

        # 2. Max trades per day
        if self.trades_today >= self.max_trades_per_day:
            reason = (
                f"Max trades/day reached: {self.trades_today}/{self.max_trades_per_day}"
            )
            logger.warning(reason)
            return False, reason

        # 3. Consecutive loss killswitch
        if self.consecutive_losses >= self.consecutive_loss_killswitch:
            reason = (
                f"Killswitch triggered: {self.consecutive_losses} consecutive losses"
            )
            logger.warning(reason)
            return False, reason

        # 4. No-trade zone
        if self._in_no_trade_zone(current_time):
            reason = f"In no-trade zone at {current_time}"
            logger.info(reason)
            return False, reason

        # 5. Existing open position
        if symbol in self.open_positions:
            reason = f"Already have open position in {symbol}"
            logger.info(reason)
            return False, reason

        # 6. Past auto square-off time
        if self._past_auto_square_off(current_time):
            reason = f"Past auto square-off time ({self._auto_square_off_str})"
            logger.info(reason)
            return False, reason

        return True, ""

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position(
        self,
        entry_price: float,
        atr: float,
        direction: str,
        size_factor: float = 1.0,
    ) -> dict[str, Any]:
        """
        Calculate quantity, SL, and TP for a trade.

        Sizing formula:
            risk_amount = capital * risk_per_trade_pct / 100
            sl_distance = ATR * sl_atr_multiplier
            quantity = floor(risk_amount / sl_distance)
            quantity = floor(quantity * size_factor)
            quantity = max(quantity, 1)

        SL/TP:
            LONG:  SL = entry - (ATR * sl_mult), TP = entry + (ATR * tp_mult)
            SHORT: SL = entry + (ATR * sl_mult), TP = entry - (ATR * tp_mult)

        Args:
            entry_price: Expected fill price
            atr: ATR value from technical engine
            direction: 'LONG' or 'SHORT'
            size_factor: Multiplier from signal engine (0.5 or 1.0)

        Returns:
            dict with quantity, sl_price, tp_price, risk_amount, sl_distance, rr_ratio
        """
        risk_amount = self.current_capital * (self.risk_per_trade_pct / 100)
        sl_distance = atr * self.sl_atr_multiplier
        tp_distance = atr * self.tp_atr_multiplier

        # Avoid division by zero
        if sl_distance <= 0:
            sl_distance = entry_price * 0.005  # fallback: 0.5% of price

        quantity = floor(risk_amount / sl_distance)
        quantity = floor(quantity * size_factor)
        quantity = max(quantity, 1)

        if direction.upper() == "LONG":
            sl_price = entry_price - sl_distance
            tp_price = entry_price + tp_distance
        else:  # SHORT
            sl_price = entry_price + sl_distance
            tp_price = entry_price - tp_distance

        rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0.0

        result = {
            "quantity": quantity,
            "sl_price": round(sl_price, 2),
            "tp_price": round(tp_price, 2),
            "risk_amount": round(risk_amount, 2),
            "sl_distance": round(sl_distance, 4),
            "tp_distance": round(tp_distance, 4),
            "rr_ratio": round(rr_ratio, 3),
            "size_factor": size_factor,
        }

        # Warn if R:R is below minimum
        if rr_ratio < self.min_rr_ratio:
            logger.warning(
                "R:R ratio %.2f below minimum %.2f — trade may be suboptimal",
                rr_ratio,
                self.min_rr_ratio,
            )

        logger.info(
            "Position sized: qty=%d, entry=%.2f, SL=%.2f, TP=%.2f, R:R=%.2f",
            quantity,
            entry_price,
            sl_price,
            tp_price,
            rr_ratio,
        )
        return result

    # ------------------------------------------------------------------
    # State updates (called by executor after trades)
    # ------------------------------------------------------------------

    def record_trade_open(self, symbol: str, position: dict) -> None:
        """Mark a position as open."""
        self.open_positions[symbol] = position
        self.trades_today += 1

    def record_trade_close(self, symbol: str, net_pnl: float) -> None:
        """
        Update state after a trade closes.

        Args:
            symbol: Symbol of closed trade
            net_pnl: Net PnL (after charges) of the closed trade
        """
        self.open_positions.pop(symbol, None)
        self.daily_pnl += net_pnl
        self.current_capital += net_pnl

        if net_pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        logger.info(
            "Trade closed: %s, net_pnl=%.2f, daily_pnl=%.2f, consecutive_losses=%d",
            symbol,
            net_pnl,
            self.daily_pnl,
            self.consecutive_losses,
        )

    def reset_daily(self) -> None:
        """Reset all daily counters. Call at 09:10 each trading day."""
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.open_positions = {}
        logger.info("Daily risk counters reset. Capital=%.2f", self.current_capital)

    def get_state(self) -> dict[str, Any]:
        """Return current risk state as a dict for logging/monitoring."""
        return {
            "current_capital": self.current_capital,
            "daily_pnl": self.daily_pnl,
            "trades_today": self.trades_today,
            "consecutive_losses": self.consecutive_losses,
            "open_positions": list(self.open_positions.keys()),
        }
