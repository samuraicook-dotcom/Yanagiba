"""
core/executor.py — Trade Execution Engine

Receives trade decisions from the signal engine, runs risk checks,
places orders (real or simulated), manages SL/TP, and logs all activity.

CRITICAL: When paper_trade=True, NO real API orders are placed.
"""

import logging
import time
from datetime import datetime
from typing import Any

from core.charges import ChargesCalculator
from core.data_engine import DataEngine
from core.risk_manager import RiskManager
from broker.base import BaseBroker

logger = logging.getLogger("kaizen.executor")


class TradeExecutor:
    """
    Manages the full lifecycle of a trade: entry → SL/TP → exit → logging.

    In paper mode, all fills are simulated at current LTP.
    In live mode, real orders are placed via the broker API.
    """

    def __init__(
        self,
        broker: BaseBroker,
        data_engine: DataEngine,
        risk_manager: RiskManager,
        charges_calc: ChargesCalculator,
        config: dict,
        db: Any = None,
        telegram: Any = None,
    ) -> None:
        """
        Args:
            broker: Authenticated broker instance
            data_engine: Market data engine
            risk_manager: Risk management instance
            charges_calc: Charges calculator instance
            config: Full settings.yaml config dict
            db: Database instance for trade logging (optional)
            telegram: TelegramNotifier instance (optional)
        """
        self.broker = broker
        self.data_engine = data_engine
        self.risk_manager = risk_manager
        self.charges_calc = charges_calc
        self.db = db
        self.telegram = telegram

        self.paper_trade: bool = config["capital"]["paper_trade"]
        self.order_type: str = config["execution"]["order_type"]
        self.product_type: str = config["execution"]["product_type"]
        self.slippage_pct: float = config["execution"]["slippage_buffer_pct"]
        self.retry_attempts: int = config["execution"]["retry_attempts"]
        self.retry_delay: float = config["execution"]["retry_delay_seconds"]
        self.auto_square_off: str = config["execution"]["auto_square_off"]

        # Track open trades: symbol → trade dict
        self._open_trades: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Main execute method
    # ------------------------------------------------------------------

    def execute_trade(
        self,
        decision: dict,
        price: float,
        atr: float,
        symbol: str,
        exchange: str = "NSE",
    ) -> dict[str, Any] | None:
        """
        Execute a trade decision end-to-end.

        Flow:
          1. Validate decision is executable
          2. Calculate position (qty, SL, TP)
          3. Place entry order (real or simulated)
          4. Place SL order
          5. Register TP monitoring
          6. Log to DB and Telegram

        Args:
            decision: Output of signal_engine.combine_with_sentiment()
            price: Current price (latest close or LTP)
            atr: Current ATR value
            symbol: Trading symbol
            exchange: Exchange code

        Returns:
            Trade dict if executed, None if rejected
        """
        action = decision.get("action", "HOLD")
        executable = decision.get("executable", False)

        if not executable or action == "HOLD":
            if action == "SHORT":
                logger.info(
                    "SHORT signal for %s logged but NOT executed (NSE ETF restriction)",
                    symbol,
                )
            return None

        direction = "BUY" if action == "LONG" else "SELL"
        size_factor = decision.get("size_factor", 1.0)

        position = self.risk_manager.calculate_position(
            entry_price=price,
            atr=atr,
            direction=action,
            size_factor=size_factor,
        )

        quantity = position["quantity"]
        sl_price = position["sl_price"]
        tp_price = position["tp_price"]

        # Apply slippage buffer to entry price
        if direction == "BUY":
            entry_price = price * (1 + self.slippage_pct / 100)
        else:
            entry_price = price * (1 - self.slippage_pct / 100)
        entry_price = round(entry_price, 2)

        logger.info(
            "Executing %s %s: qty=%d, entry=%.2f, SL=%.2f, TP=%.2f",
            action, symbol, quantity, entry_price, sl_price, tp_price,
        )

        if self.paper_trade:
            trade = self._simulate_entry(
                symbol, exchange, direction, quantity, entry_price, sl_price, tp_price, decision
            )
        else:
            trade = self._live_entry(
                symbol, exchange, direction, quantity, entry_price, sl_price, tp_price, decision
            )

        if trade:
            self._open_trades[symbol] = trade
            self.risk_manager.record_trade_open(symbol, trade)
            self._log_trade_open(trade)

        return trade

    # ------------------------------------------------------------------
    # Paper trade simulation
    # ------------------------------------------------------------------

    def _simulate_entry(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        decision: dict,
    ) -> dict:
        """Simulate a trade entry. Fill at entry_price immediately."""
        trade_id = f"PAPER-{symbol}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        trade = {
            "id": trade_id,
            "symbol": symbol,
            "exchange": exchange,
            "direction": direction,
            "quantity": quantity,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "entry_time": datetime.now().isoformat(),
            "status": "OPEN",
            "paper": True,
            "tech_signal": decision.get("signal"),
            "sentiment_bias": decision.get("bias"),
            "order_id": trade_id,
            "sl_order_id": f"{trade_id}-SL",
            "tp_order_id": f"{trade_id}-TP",
        }

        logger.info(
            "[PAPER] Entry simulated: %s %s %d @ %.2f",
            direction, symbol, quantity, entry_price,
        )
        return trade

    def simulate_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str = "manual",
    ) -> dict | None:
        """
        Simulate closing an open paper trade at exit_price.

        Args:
            symbol: Symbol to close
            exit_price: Exit fill price
            exit_reason: 'sl_hit', 'tp_hit', 'square_off', 'manual'

        Returns:
            Closed trade dict with PnL, or None if no open trade
        """
        trade = self._open_trades.pop(symbol, None)
        if not trade:
            logger.warning("No open paper trade found for %s", symbol)
            return None

        direction = trade["direction"]
        quantity = trade["quantity"]
        entry_price = trade["entry_price"]

        buy_price = entry_price if direction == "BUY" else exit_price
        sell_price = exit_price if direction == "BUY" else entry_price

        pnl_result = self.charges_calc.calculate_net_pnl(buy_price, sell_price, quantity)

        trade.update({
            "exit_price": exit_price,
            "exit_time": datetime.now().isoformat(),
            "exit_reason": exit_reason,
            "gross_pnl": pnl_result["gross_pnl"],
            "charges": pnl_result["charges"],
            "net_pnl": pnl_result["net_pnl"],
            "status": "CLOSED",
        })

        self.risk_manager.record_trade_close(symbol, pnl_result["net_pnl"])
        self._log_trade_close(trade)

        logger.info(
            "[PAPER] Exit simulated: %s %s @ %.2f | net_pnl=%.2f | reason=%s",
            symbol, direction, exit_price, pnl_result["net_pnl"], exit_reason,
        )
        return trade

    # ------------------------------------------------------------------
    # Live execution
    # ------------------------------------------------------------------

    def _live_entry(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        decision: dict,
    ) -> dict | None:
        """Place real entry + SL orders via broker."""
        # Entry order
        entry_order_id = self._place_with_retry(
            lambda: self.broker.place_order(
                symbol=symbol,
                exchange=exchange,
                direction=direction,
                quantity=quantity,
                order_type=self.order_type,
                product_type=self.product_type,
            )
        )

        if not entry_order_id:
            logger.error("Entry order failed for %s after %d retries", symbol, self.retry_attempts)
            return None

        # Wait for fill confirmation
        fill_price = self._wait_for_fill(entry_order_id) or entry_price

        # SL order — opposite direction
        sl_direction = "SELL" if direction == "BUY" else "BUY"
        sl_order_id = self._place_with_retry(
            lambda: self.broker.place_sl_order(
                symbol=symbol,
                exchange=exchange,
                direction=sl_direction,
                quantity=quantity,
                trigger_price=sl_price,
            )
        )

        # TP as GTT order
        tp_direction = sl_direction
        tp_order_id = self._place_with_retry(
            lambda: self.broker.place_gtt(
                symbol=symbol,
                exchange=exchange,
                direction=tp_direction,
                quantity=quantity,
                trigger_price=tp_price,
                price=tp_price,
            )
        )

        trade = {
            "symbol": symbol,
            "exchange": exchange,
            "direction": direction,
            "quantity": quantity,
            "entry_price": fill_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "entry_time": datetime.now().isoformat(),
            "status": "OPEN",
            "paper": False,
            "tech_signal": decision.get("signal"),
            "sentiment_bias": decision.get("bias"),
            "order_id": entry_order_id,
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
        }

        logger.info(
            "[LIVE] Entry placed: %s %s %d @ %.2f | SL=%.2f | TP=%.2f",
            direction, symbol, quantity, fill_price, sl_price, tp_price,
        )
        return trade

    def _place_with_retry(self, fn) -> str | None:
        """Execute a broker call with retry logic."""
        for attempt in range(1, self.retry_attempts + 1):
            try:
                result = fn()
                return result
            except Exception as exc:
                logger.warning("Broker call attempt %d/%d failed: %s", attempt, self.retry_attempts, exc)
                if attempt < self.retry_attempts:
                    time.sleep(self.retry_delay)
        return None

    def _wait_for_fill(self, order_id: str, timeout_seconds: int = 30) -> float | None:
        """Poll order status until filled or timeout."""
        start = time.time()
        while time.time() - start < timeout_seconds:
            status = self.broker.get_order_status(order_id)
            if status.get("status") == "COMPLETE":
                return float(status.get("average_price", 0))
            time.sleep(2)
        logger.warning("Order %s not filled within %ds", order_id, timeout_seconds)
        return None

    # ------------------------------------------------------------------
    # Auto square-off
    # ------------------------------------------------------------------

    def square_off_all(self) -> None:
        """
        Close all open positions immediately (called at auto_square_off time).
        In paper mode, simulates exits at current LTP.
        In live mode, places MARKET orders to flatten.
        """
        symbols = list(self._open_trades.keys())
        if not symbols:
            logger.info("No open positions to square off")
            return

        for symbol in symbols:
            trade = self._open_trades.get(symbol)
            if not trade:
                continue

            if self.paper_trade:
                ltp = self.data_engine.get_ltp(symbol)
                self.simulate_exit(symbol, ltp, exit_reason="square_off")
            else:
                # Cancel existing SL and TP orders first
                if trade.get("sl_order_id"):
                    self.broker.cancel_order(trade["sl_order_id"])

                direction = "SELL" if trade["direction"] == "BUY" else "BUY"
                self._place_with_retry(
                    lambda: self.broker.place_order(
                        symbol=trade["symbol"],
                        exchange=trade["exchange"],
                        direction=direction,
                        quantity=trade["quantity"],
                        order_type="MARKET",
                        product_type=self.product_type,
                    )
                )
                logger.info("[LIVE] Square-off placed for %s", symbol)

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log_trade_open(self, trade: dict) -> None:
        """Log trade open to DB and Telegram."""
        if self.db:
            try:
                self.db.insert_trade(trade)
            except Exception as exc:
                logger.error("DB trade insert failed: %s", exc)

        if self.telegram:
            mode = "PAPER" if trade.get("paper") else "LIVE"
            msg = (
                f"📈 [{mode}] TRADE OPEN\n"
                f"Symbol: {trade['symbol']}\n"
                f"Direction: {trade['direction']}\n"
                f"Qty: {trade['quantity']}\n"
                f"Entry: ₹{trade['entry_price']:.2f}\n"
                f"SL: ₹{trade['sl_price']:.2f}\n"
                f"TP: ₹{trade['tp_price']:.2f}\n"
                f"Signal: {trade.get('tech_signal')} | Sentiment: {trade.get('sentiment_bias')}"
            )
            self.telegram.send(msg)

    def _log_trade_close(self, trade: dict) -> None:
        """Log trade close to DB and Telegram."""
        if self.db:
            try:
                self.db.update_trade_close(trade)
            except Exception as exc:
                logger.error("DB trade update failed: %s", exc)

        if self.telegram:
            mode = "PAPER" if trade.get("paper") else "LIVE"
            pnl = trade.get("net_pnl", 0)
            emoji = "✅" if pnl >= 0 else "❌"
            msg = (
                f"{emoji} [{mode}] TRADE CLOSED\n"
                f"Symbol: {trade['symbol']}\n"
                f"Direction: {trade['direction']}\n"
                f"Exit: ₹{trade.get('exit_price', 0):.2f}\n"
                f"Reason: {trade.get('exit_reason')}\n"
                f"Gross PnL: ₹{trade.get('gross_pnl', 0):.2f}\n"
                f"Charges: ₹{trade.get('charges', 0):.2f}\n"
                f"Net PnL: ₹{pnl:.2f}"
            )
            self.telegram.send(msg)
