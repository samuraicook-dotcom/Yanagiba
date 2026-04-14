"""
core/executor.py — Trade Execution Engine (Robust Version)

Guarantees:
  1. No duplicate orders — OrderGuard blocks re-entry on open positions
  2. Proper SL/TP cleanup — when SL hits, TP order is cancelled; vice versa
  3. No orphan orders — square-off cancels ALL child orders before exit
  4. Learns from mistakes — TradeLearner adjusts size after losses
  5. Paper mode — zero real API calls when paper_trade=True

Order lifecycle per trade:
  IDLE → place entry → PENDING_ENTRY
       → fill confirmed → place SL + TP → OPEN
       → SL or TP fires → cancel complementary order → record close → IDLE
       → OR square-off → cancel SL+TP → place market exit → IDLE
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Optional

from core.charges import ChargesCalculator
from core.data_engine import DataEngine
from core.order_guard import OrderGuard, PositionState
from core.risk_manager import RiskManager
from core.trade_learner import TradeLearner
from broker.base import BaseBroker

logger = logging.getLogger("kaizen.executor")


class TradeExecutor:
    """
    Manages the full trade lifecycle with duplicate-order protection,
    SL/TP coordination, and adaptive sizing via TradeLearner.
    """

    def __init__(
        self,
        broker: BaseBroker,
        data_engine: DataEngine,
        risk_manager: RiskManager,
        charges_calc: ChargesCalculator,
        order_guard: OrderGuard,
        trade_learner: TradeLearner,
        config: dict,
        db: Any = None,
        telegram: Any = None,
    ) -> None:
        self.broker = broker
        self.data_engine = data_engine
        self.risk_manager = risk_manager
        self.charges_calc = charges_calc
        self.order_guard = order_guard
        self.trade_learner = trade_learner
        self.db = db
        self.telegram = telegram

        self.paper_trade: bool = config["capital"]["paper_trade"]
        self.order_type: str = config["execution"]["order_type"]
        self.product_type: str = config["execution"]["product_type"]
        self.slippage_pct: float = config["execution"]["slippage_buffer_pct"]
        self.retry_attempts: int = config["execution"]["retry_attempts"]
        self.retry_delay: float = config["execution"]["retry_delay_seconds"]

        # Build asset config map: symbol → asset dict
        self._asset_cfg: dict[str, dict] = {
            a["symbol"]: a for a in config.get("assets", [])
        }

        # Paper trade open positions: symbol → trade dict
        self._paper_trades: dict[str, dict] = {}

        # Background SL/TP monitor thread
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False

    # ------------------------------------------------------------------
    # Public: execute a trade decision
    # ------------------------------------------------------------------

    def execute_trade(
        self,
        decision: dict,
        price: float,
        atr: float,
        symbol: str,
        exchange: str = "NSE",
    ) -> Optional[dict]:
        """
        Execute a trade decision end-to-end with full safety checks.

        Flow:
          1. Check OrderGuard — block if already open or pending
          2. Check TradeLearner — get size adjustment or cooldown block
          3. Calculate position (qty, SL, TP)
          4. Mark PENDING in OrderGuard
          5. Place entry, SL, TP
          6. Mark OPEN in OrderGuard
          7. Log to DB and Telegram

        Args:
            decision: Output of signal_engine.combine_with_sentiment()
            price: Current price (latest close or LTP)
            atr: Current ATR value
            symbol: Trading symbol
            exchange: Exchange code

        Returns:
            Trade dict if executed, None if blocked or failed
        """
        action = decision.get("action", "HOLD")
        executable = decision.get("executable", False)

        if not executable or action == "HOLD":
            if action == "SHORT" and not executable:
                logger.info(
                    "SHORT signal for %s — not executable (ETF restriction)", symbol
                )
            return None

        # 1. Duplicate order guard
        if not self.order_guard.can_enter(symbol):
            logger.warning(
                "BLOCKED: %s already has an active order (state=%s)",
                symbol,
                self.order_guard.get_state(symbol).value,
            )
            return None

        # 2. TradeLearner size adjustment
        self.trade_learner.tick_cooldown(symbol)
        learn_factor, learn_reason = self.trade_learner.get_size_adjustment(symbol)

        if learn_factor == 0.0:
            logger.info("BLOCKED by TradeLearner [%s]: %s", symbol, learn_reason)
            if self.telegram:
                self.telegram.send_risk_alert(
                    f"⏸ {symbol} skipped — learner cooldown\n{learn_reason}"
                )
            return None

        direction = "BUY" if action == "LONG" else "SELL"
        base_size_factor = decision.get("size_factor", 1.0)
        final_size_factor = base_size_factor * learn_factor

        logger.info(
            "TradeLearner [%s]: size_factor=%.2f × learn_factor=%.2f = %.2f (%s)",
            symbol, base_size_factor, learn_factor, final_size_factor, learn_reason,
        )

        # 3. Calculate position
        position = self.risk_manager.calculate_position(
            entry_price=price,
            atr=atr,
            direction=action,
            size_factor=final_size_factor,
        )
        quantity = position["quantity"]
        sl_price = position["sl_price"]
        tp_price = position["tp_price"]

        # Apply slippage to entry estimate
        if direction == "BUY":
            entry_price = round(price * (1 + self.slippage_pct / 100), 2)
        else:
            entry_price = round(price * (1 - self.slippage_pct / 100), 2)

        logger.info(
            "Executing %s %s: qty=%d entry=%.2f SL=%.2f TP=%.2f",
            action, symbol, quantity, entry_price, sl_price, tp_price,
        )

        # 4. Mark pending (atomic — prevents any racing cycle from entering)
        self.order_guard.mark_pending(symbol)

        try:
            if self.paper_trade:
                trade = self._paper_entry(
                    symbol, exchange, direction, quantity,
                    entry_price, sl_price, tp_price, decision,
                )
            else:
                trade = self._live_entry(
                    symbol, exchange, direction, quantity,
                    entry_price, sl_price, tp_price, decision,
                )
        except Exception as exc:
            logger.exception("Entry failed for %s: %s", symbol, exc)
            self.order_guard.mark_idle(symbol)  # rollback guard
            return None

        if not trade:
            self.order_guard.mark_idle(symbol)
            return None

        # 5. Mark open in guard
        self.order_guard.mark_open(
            symbol=symbol,
            entry_order_id=trade.get("order_id", ""),
            sl_order_id=trade.get("sl_order_id"),
            tp_order_id=trade.get("tp_order_id"),
            direction=direction,
            quantity=quantity,
            entry_price=trade["entry_price"],
            sl_price=sl_price,
            tp_price=tp_price,
        )

        # 6. Register with risk manager
        self.risk_manager.record_trade_open(symbol, trade)

        # 7. Log
        self._log_trade_open(trade)

        return trade

    # ------------------------------------------------------------------
    # Paper trade simulation
    # ------------------------------------------------------------------

    def _paper_entry(
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
        """Simulate a fill at entry_price immediately."""
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
        self._paper_trades[symbol] = trade
        logger.info("[PAPER] Entry: %s %s %d @ %.2f", direction, symbol, quantity, entry_price)
        return trade

    def paper_check_sl_tp(self, symbol: str, current_low: float, current_high: float) -> None:
        """
        Check if current candle's low/high triggered SL or TP for a paper trade.
        Called by the monitoring loop every cycle.

        Args:
            symbol: Symbol to check
            current_low: Candle low
            current_high: Candle high
        """
        trade = self._paper_trades.get(symbol)
        if not trade:
            return

        direction = trade["direction"]
        sl = trade["sl_price"]
        tp = trade["tp_price"]

        exit_price = None
        exit_reason = None

        if direction == "BUY":
            if current_low <= sl:
                exit_price, exit_reason = sl, "sl_hit"
            elif current_high >= tp:
                exit_price, exit_reason = tp, "tp_hit"
        else:  # SELL (short)
            if current_high >= sl:
                exit_price, exit_reason = sl, "sl_hit"
            elif current_low <= tp:
                exit_price, exit_reason = tp, "tp_hit"

        if exit_price is not None:
            self._close_paper_trade(symbol, exit_price, exit_reason)

    def _close_paper_trade(
        self, symbol: str, exit_price: float, exit_reason: str
    ) -> Optional[dict]:
        """Close an open paper trade, compute PnL, update all state."""
        trade = self._paper_trades.pop(symbol, None)
        if not trade:
            return None

        direction = trade["direction"]
        quantity = trade["quantity"]
        entry_price = trade["entry_price"]

        if direction == "BUY":
            pnl = self.charges_calc.calculate_net_pnl(entry_price, exit_price, quantity)
        else:  # SHORT — we sold high, bought back low
            pnl = self.charges_calc.calculate_net_pnl(exit_price, entry_price, quantity)

        trade.update({
            "exit_price": exit_price,
            "exit_time": datetime.now().isoformat(),
            "exit_reason": exit_reason,
            "gross_pnl": pnl["gross_pnl"],
            "charges": pnl["charges"],
            "net_pnl": pnl["net_pnl"],
            "status": "CLOSED",
        })

        # Update all state managers atomically
        self.risk_manager.record_trade_close(symbol, pnl["net_pnl"])
        self.trade_learner.record_outcome(symbol, pnl["net_pnl"])
        self.order_guard.mark_idle(symbol)

        # Log
        self._log_trade_close(trade)

        logger.info(
            "[PAPER] %s closed: %s @ %.2f | net_pnl=₹%.2f | reason=%s",
            symbol, direction, exit_price, pnl["net_pnl"], exit_reason,
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
    ) -> Optional[dict]:
        """Place real entry + SL + TP orders via broker with retry."""
        # Entry order
        entry_order_id = self._place_with_retry(
            lambda: self.broker.place_order(
                symbol=symbol, exchange=exchange, direction=direction,
                quantity=quantity, order_type=self.order_type,
                product_type=self.product_type,
            )
        )
        if not entry_order_id:
            logger.error("Entry order FAILED for %s after retries", symbol)
            return None

        # Wait for fill
        fill_price = self._wait_for_fill(entry_order_id) or entry_price

        # SL order — opposite direction to flatten
        sl_direction = "SELL" if direction == "BUY" else "BUY"
        sl_order_id = self._place_with_retry(
            lambda: self.broker.place_sl_order(
                symbol=symbol, exchange=exchange, direction=sl_direction,
                quantity=quantity, trigger_price=sl_price,
            )
        )
        if not sl_order_id:
            logger.error("SL order FAILED for %s — cancelling entry", symbol)
            self._cancel_safe(entry_order_id)
            return None

        # TP as GTT
        tp_order_id = self._place_with_retry(
            lambda: self.broker.place_gtt(
                symbol=symbol, exchange=exchange, direction=sl_direction,
                quantity=quantity, trigger_price=tp_price, price=tp_price,
            )
        )
        # TP GTT failure is non-fatal — bot will monitor manually
        if not tp_order_id:
            logger.warning("GTT TP order failed for %s — will monitor manually", symbol)

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
            "[LIVE] Entry: %s %s %d @ %.2f | SL=%.2f TP=%.2f",
            direction, symbol, quantity, fill_price, sl_price, tp_price,
        )
        return trade

    def _close_live_position(
        self, symbol: str, exit_price: float, exit_reason: str
    ) -> None:
        """
        Cancel all child orders (SL + TP) then place market exit for live trade.
        Called when SL or TP fires, or on square-off.
        """
        record = self.order_guard.get_record(symbol)
        if not record:
            return

        # Cancel the complementary order
        if exit_reason == "sl_hit" and record.tp_order_id:
            self._cancel_safe(record.tp_order_id)
            logger.info("Cancelled TP order %s after SL hit for %s", record.tp_order_id, symbol)
        elif exit_reason == "tp_hit" and record.sl_order_id:
            self._cancel_safe(record.sl_order_id)
            logger.info("Cancelled SL order %s after TP hit for %s", record.sl_order_id, symbol)
        elif exit_reason == "square_off":
            # Cancel both
            if record.sl_order_id:
                self._cancel_safe(record.sl_order_id)
            if record.tp_order_id:
                self._cancel_safe(record.tp_order_id)

        self.order_guard.mark_pending_exit(symbol, "")

        # Compute PnL
        if record.direction == "BUY":
            pnl = self.charges_calc.calculate_net_pnl(
                record.entry_price, exit_price, record.quantity
            )
        else:
            pnl = self.charges_calc.calculate_net_pnl(
                exit_price, record.entry_price, record.quantity
            )

        trade = {
            "symbol": symbol,
            "direction": record.direction,
            "quantity": record.quantity,
            "entry_price": record.entry_price,
            "exit_price": exit_price,
            "sl_price": record.sl_price,
            "tp_price": record.tp_price,
            "exit_time": datetime.now().isoformat(),
            "exit_reason": exit_reason,
            "gross_pnl": pnl["gross_pnl"],
            "charges": pnl["charges"],
            "net_pnl": pnl["net_pnl"],
            "status": "CLOSED",
            "paper": False,
        }

        self.risk_manager.record_trade_close(symbol, pnl["net_pnl"])
        self.trade_learner.record_outcome(symbol, pnl["net_pnl"])
        self.order_guard.mark_idle(symbol)
        self._log_trade_close(trade)

    # ------------------------------------------------------------------
    # Square-off all positions
    # ------------------------------------------------------------------

    def square_off_all(self) -> None:
        """
        Close all open positions at 15:10 IST.

        Paper mode: close at current LTP.
        Live mode: cancel SL+TP orders, place MARKET exit.
        """
        open_symbols = self.order_guard.all_open_symbols()
        if not open_symbols:
            logger.info("Square-off: no open positions")
            return

        logger.info("Square-off: closing %d positions: %s", len(open_symbols), open_symbols)

        for symbol in open_symbols:
            try:
                asset_cfg = self._asset_cfg.get(symbol, {})
                exchange = asset_cfg.get("exchange", "NSE")
                ltp = self.data_engine.get_ltp(symbol)

                if self.paper_trade:
                    self._close_paper_trade(symbol, ltp, "square_off")
                else:
                    record = self.order_guard.get_record(symbol)
                    if record:
                        exit_dir = "SELL" if record.direction == "BUY" else "BUY"
                        self._close_live_position(symbol, ltp, "square_off")
                        self._place_with_retry(
                            lambda: self.broker.place_order(
                                symbol=symbol, exchange=exchange,
                                direction=exit_dir, quantity=record.quantity,
                                order_type="MARKET", product_type=self.product_type,
                            )
                        )
            except Exception as exc:
                logger.exception("Square-off failed for %s: %s", symbol, exc)

    # ------------------------------------------------------------------
    # SL/TP monitor (background thread)
    # ------------------------------------------------------------------

    def start_monitor(self, poll_interval_seconds: int = 30) -> None:
        """
        Start background thread that checks SL/TP hits every N seconds.
        Used for paper trading and as a fallback for live trading.
        """
        if self._monitor_running:
            return
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(poll_interval_seconds,),
            daemon=True,
            name="sl-tp-monitor",
        )
        self._monitor_thread.start()
        logger.info("SL/TP monitor started (interval=%ds)", poll_interval_seconds)

    def stop_monitor(self) -> None:
        self._monitor_running = False
        logger.info("SL/TP monitor stopped")

    def _monitor_loop(self, interval: int) -> None:
        """Poll open positions and check SL/TP every `interval` seconds."""
        while self._monitor_running:
            time.sleep(interval)
            open_symbols = self.order_guard.all_open_symbols()
            for symbol in open_symbols:
                try:
                    self._check_position(symbol)
                except Exception as exc:
                    logger.error("Monitor error for %s: %s", symbol, exc)

    def _check_position(self, symbol: str) -> None:
        """Check if SL or TP was hit for a single open position."""
        record = self.order_guard.get_record(symbol)
        if not record or record.state != PositionState.OPEN:
            return

        ltp = self.data_engine.get_ltp(symbol)
        if ltp <= 0:
            return

        direction = record.direction
        sl = record.sl_price
        tp = record.tp_price

        if self.paper_trade:
            # In paper mode use LTP as a proxy for intra-candle prices
            if direction == "BUY":
                if ltp <= sl:
                    self._close_paper_trade(symbol, sl, "sl_hit")
                elif ltp >= tp:
                    self._close_paper_trade(symbol, tp, "tp_hit")
            else:  # SHORT
                if ltp >= sl:
                    self._close_paper_trade(symbol, sl, "sl_hit")
                elif ltp <= tp:
                    self._close_paper_trade(symbol, tp, "tp_hit")
        else:
            # Live: check broker order status for SL-M or GTT completion
            sl_status = self.broker.get_order_status(record.sl_order_id or "")
            if sl_status.get("status") == "COMPLETE":
                fill = float(sl_status.get("average_price", sl))
                self._close_live_position(symbol, fill, "sl_hit")
                return

            if record.tp_order_id:
                tp_status = self.broker.get_order_status(record.tp_order_id)
                if tp_status.get("status") == "COMPLETE":
                    fill = float(tp_status.get("average_price", tp))
                    self._close_live_position(symbol, fill, "tp_hit")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _place_with_retry(self, fn) -> Optional[str]:
        """Execute a broker call with retry logic."""
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return fn()
            except Exception as exc:
                logger.warning(
                    "Broker call attempt %d/%d failed: %s",
                    attempt, self.retry_attempts, exc,
                )
                if attempt < self.retry_attempts:
                    time.sleep(self.retry_delay)
        return None

    def _cancel_safe(self, order_id: str) -> None:
        """Cancel an order, logging but not raising on failure."""
        if not order_id:
            return
        try:
            self.broker.cancel_order(order_id)
        except Exception as exc:
            logger.warning("Could not cancel order %s: %s", order_id, exc)

    def _wait_for_fill(self, order_id: str, timeout: int = 30) -> Optional[float]:
        """Poll until order is filled or timeout."""
        start = time.time()
        while time.time() - start < timeout:
            status = self.broker.get_order_status(order_id)
            if status.get("status") == "COMPLETE":
                return float(status.get("average_price", 0))
            time.sleep(2)
        logger.warning("Order %s not filled within %ds", order_id, timeout)
        return None

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log_trade_open(self, trade: dict) -> None:
        if self.db:
            try:
                self.db.insert_trade(trade)
            except Exception as exc:
                logger.error("DB insert failed: %s", exc)
        if self.telegram:
            self.telegram.send_trade_open(trade)

    def _log_trade_close(self, trade: dict) -> None:
        if self.db:
            try:
                self.db.update_trade_close(trade)
            except Exception as exc:
                logger.error("DB update failed: %s", exc)
        if self.telegram:
            self.telegram.send_trade_close(trade)
