"""
broker/zerodha.py — Zerodha Kite Connect Broker Implementation

Implements BaseBroker using the kiteconnect Python SDK.
Enforces a rate limiter (max 3 requests/second) on all API calls.
Stores instrument token mapping in memory — fetched once daily.
"""

import logging
import os
import time
from threading import Lock
from typing import Any

from broker.base import BaseBroker

logger = logging.getLogger("kaizen.zerodha")


class RateLimiter:
    """
    Thread-safe token-bucket rate limiter.
    Ensures Kite API calls don't exceed 3 requests/second.
    """

    def __init__(self, max_per_second: int = 3) -> None:
        self.min_interval: float = 1.0 / max_per_second
        self.last_call: float = 0.0
        self.lock: Lock = Lock()

    def wait(self) -> None:
        """Block until the next API call is safe to make."""
        with self.lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_call = time.time()


class ZerodhaBroker(BaseBroker):
    """
    Zerodha Kite Connect broker implementation.

    API credentials are read from environment variables:
      KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN

    Instrument tokens are cached after the first fetch_instruments() call.
    """

    def __init__(self) -> None:
        self.api_key: str = os.environ.get("KITE_API_KEY", "")
        self.api_secret: str = os.environ.get("KITE_API_SECRET", "")
        self.access_token: str = os.environ.get("KITE_ACCESS_TOKEN", "")

        self._kite: Any = None
        self._rate_limiter: RateLimiter = RateLimiter(max_per_second=3)
        self._instrument_map: dict[str, int] = {}  # symbol → instrument_token

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """
        Initialize KiteConnect and set the access token.
        The access token must already be generated via scripts/login.py.

        Returns:
            True if login succeeded
        """
        try:
            from kiteconnect import KiteConnect  # type: ignore

            if not self.api_key:
                logger.error("KITE_API_KEY not set in environment")
                return False
            if not self.access_token:
                logger.error("KITE_ACCESS_TOKEN not set — run scripts/login.py first")
                return False

            self._kite = KiteConnect(api_key=self.api_key)
            self._kite.set_access_token(self.access_token)

            # Validate by fetching profile
            self._rate_limiter.wait()
            profile = self._kite.profile()
            logger.info("Kite login OK — user: %s", profile.get("user_name", "?"))
            return True

        except Exception as exc:
            logger.error("Kite login failed: %s", exc)
            return False

    def _ensure_logged_in(self) -> None:
        """Raise if kite client is not initialized."""
        if self._kite is None:
            raise RuntimeError("ZerodhaBroker not logged in. Call login() first.")

    # ------------------------------------------------------------------
    # Instrument token management
    # ------------------------------------------------------------------

    def fetch_instruments(self, exchange: str = "NSE") -> None:
        """
        Fetch and cache instrument tokens for all symbols on an exchange.
        Call once at market open (09:10) to populate the token map.

        Args:
            exchange: Exchange to fetch instruments for
        """
        self._ensure_logged_in()
        self._rate_limiter.wait()
        try:
            instruments = self._kite.instruments(exchange)
            self._instrument_map = {
                inst["tradingsymbol"]: inst["instrument_token"]
                for inst in instruments
            }
            logger.info(
                "Fetched %d instruments for %s", len(self._instrument_map), exchange
            )
        except Exception as exc:
            logger.error("Failed to fetch instruments: %s", exc)

    def get_instrument_token(self, symbol: str, exchange: str = "NSE") -> int:
        """
        Get the instrument token for a symbol.
        Fetches instruments if map is empty.

        Args:
            symbol: Trading symbol
            exchange: Exchange code

        Returns:
            Integer instrument token
        """
        if not self._instrument_map:
            self.fetch_instruments(exchange)

        token = self._instrument_map.get(symbol)
        if token is None:
            raise ValueError(f"Instrument token not found for {symbol}:{exchange}")
        return token

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        order_type: str,
        product_type: str,
        price: float | None = None,
    ) -> str:
        """Place a regular MARKET or LIMIT order via Kite."""
        self._ensure_logged_in()
        self._rate_limiter.wait()

        transaction_type = (
            self._kite.TRANSACTION_TYPE_BUY
            if direction.upper() == "BUY"
            else self._kite.TRANSACTION_TYPE_SELL
        )
        kite_order_type = (
            self._kite.ORDER_TYPE_MARKET
            if order_type.upper() == "MARKET"
            else self._kite.ORDER_TYPE_LIMIT
        )
        kite_product = (
            self._kite.PRODUCT_MIS
            if product_type.upper() == "MIS"
            else self._kite.PRODUCT_CNC
        )

        params: dict[str, Any] = {
            "variety": self._kite.VARIETY_REGULAR,
            "exchange": exchange,
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": kite_order_type,
            "product": kite_product,
        }
        if price is not None:
            params["price"] = price

        order_id = self._kite.place_order(**params)
        logger.info(
            "Order placed: %s %s %d @ %s — order_id=%s",
            direction,
            symbol,
            quantity,
            price or "MARKET",
            order_id,
        )
        return str(order_id)

    def place_sl_order(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        trigger_price: float,
        price: float | None = None,
    ) -> str:
        """Place a Stop-Loss Market (SL-M) order via Kite."""
        self._ensure_logged_in()
        self._rate_limiter.wait()

        transaction_type = (
            self._kite.TRANSACTION_TYPE_BUY
            if direction.upper() == "BUY"
            else self._kite.TRANSACTION_TYPE_SELL
        )

        params: dict[str, Any] = {
            "variety": self._kite.VARIETY_REGULAR,
            "exchange": exchange,
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": self._kite.ORDER_TYPE_SLM,
            "product": self._kite.PRODUCT_MIS,
            "trigger_price": trigger_price,
        }
        if price is not None:
            params["price"] = price
            params["order_type"] = self._kite.ORDER_TYPE_SL

        order_id = self._kite.place_order(**params)
        logger.info(
            "SL order placed: %s %s %d trigger=%.2f — order_id=%s",
            direction,
            symbol,
            quantity,
            trigger_price,
            order_id,
        )
        return str(order_id)

    def place_gtt(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        trigger_price: float,
        price: float,
    ) -> str:
        """Place a GTT (Good-Till-Triggered) order for take-profit."""
        self._ensure_logged_in()
        self._rate_limiter.wait()

        transaction_type = (
            self._kite.TRANSACTION_TYPE_BUY
            if direction.upper() == "BUY"
            else self._kite.TRANSACTION_TYPE_SELL
        )

        gtt_id = self._kite.place_gtt(
            trigger_type=self._kite.GTT_TYPE_SINGLE,
            tradingsymbol=symbol,
            exchange=exchange,
            trigger_values=[trigger_price],
            last_price=price,
            orders=[
                {
                    "transaction_type": transaction_type,
                    "quantity": quantity,
                    "order_type": self._kite.ORDER_TYPE_LIMIT,
                    "product": self._kite.PRODUCT_CNC,  # GTT uses CNC
                    "price": price,
                }
            ],
        )
        logger.info(
            "GTT placed: %s %s %d trigger=%.2f — gtt_id=%s",
            direction,
            symbol,
            quantity,
            trigger_price,
            gtt_id,
        )
        return str(gtt_id)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending regular order."""
        self._ensure_logged_in()
        self._rate_limiter.wait()
        try:
            self._kite.cancel_order(
                variety=self._kite.VARIETY_REGULAR, order_id=order_id
            )
            logger.info("Order cancelled: %s", order_id)
            return True
        except Exception as exc:
            logger.error("Failed to cancel order %s: %s", order_id, exc)
            return False

    def get_order_status(self, order_id: str) -> dict:
        """Fetch order status from Kite."""
        self._ensure_logged_in()
        self._rate_limiter.wait()
        try:
            orders = self._kite.orders()
            for order in orders:
                if str(order.get("order_id")) == str(order_id):
                    return order
            return {}
        except Exception as exc:
            logger.error("Failed to get order status for %s: %s", order_id, exc)
            return {}

    def get_positions(self) -> list[dict]:
        """Fetch all current intraday and delivery positions."""
        self._ensure_logged_in()
        self._rate_limiter.wait()
        try:
            positions = self._kite.positions()
            return positions.get("day", []) + positions.get("net", [])
        except Exception as exc:
            logger.error("Failed to fetch positions: %s", exc)
            return []

    def get_ltp(self, symbol: str, exchange: str) -> float:
        """Fetch last traded price for a symbol."""
        self._ensure_logged_in()
        self._rate_limiter.wait()
        try:
            key = f"{exchange}:{symbol}"
            ltp_data = self._kite.ltp([key])
            return float(ltp_data[key]["last_price"])
        except Exception as exc:
            logger.error("Failed to get LTP for %s:%s: %s", symbol, exchange, exc)
            return 0.0

    def get_historical_data(
        self,
        instrument_token: int,
        from_date: str,
        to_date: str,
        interval: str,
    ) -> list:
        """
        Fetch historical OHLCV candle data.

        Args:
            instrument_token: Kite integer instrument token
            from_date: 'YYYY-MM-DD' start date
            to_date: 'YYYY-MM-DD' end date
            interval: '30minute', 'day', etc.

        Returns:
            List of candle records
        """
        self._ensure_logged_in()
        self._rate_limiter.wait()
        try:
            data = self._kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
                continuous=False,
                oi=False,
            )
            return data
        except Exception as exc:
            logger.error(
                "Failed to fetch historical data for token %d: %s",
                instrument_token,
                exc,
            )
            return []
