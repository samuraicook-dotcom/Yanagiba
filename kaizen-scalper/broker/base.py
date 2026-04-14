"""
broker/base.py — Abstract Broker Interface

Defines the contract all broker implementations must satisfy.
"""

from abc import ABC, abstractmethod


class BaseBroker(ABC):
    """
    Abstract base class for broker integrations.

    All methods must be implemented by concrete broker classes.
    """

    @abstractmethod
    def login(self) -> bool:
        """
        Authenticate with the broker.

        Returns:
            True if login succeeded, False otherwise.
        """

    @abstractmethod
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
        """
        Place a regular order.

        Args:
            symbol: Trading symbol (e.g. 'GOLDBEES')
            exchange: Exchange code (e.g. 'NSE')
            direction: 'BUY' or 'SELL'
            quantity: Number of units
            order_type: 'MARKET' or 'LIMIT'
            product_type: 'MIS' (intraday) or 'CNC' (delivery)
            price: Limit price (required for LIMIT orders)

        Returns:
            Order ID string
        """

    @abstractmethod
    def place_sl_order(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        trigger_price: float,
        price: float | None = None,
    ) -> str:
        """
        Place a Stop-Loss Market (SL-M) order.

        Args:
            symbol: Trading symbol
            exchange: Exchange code
            direction: 'BUY' or 'SELL'
            quantity: Number of units
            trigger_price: Price at which the SL triggers
            price: Limit price for SL-L orders (optional)

        Returns:
            Order ID string
        """

    @abstractmethod
    def place_gtt(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        trigger_price: float,
        price: float,
    ) -> str:
        """
        Place a Good-Till-Triggered (GTT) order for take-profit.

        Args:
            symbol: Trading symbol
            exchange: Exchange code
            direction: 'BUY' or 'SELL'
            quantity: Number of units
            trigger_price: Price at which GTT activates
            price: Limit price for the triggered order

        Returns:
            GTT order ID string
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if cancelled successfully
        """

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """
        Fetch the current status of an order.

        Args:
            order_id: Order ID to query

        Returns:
            dict with order details (status, filled_quantity, average_price, etc.)
        """

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """
        Fetch all current open positions.

        Returns:
            List of position dicts
        """

    @abstractmethod
    def get_ltp(self, symbol: str, exchange: str) -> float:
        """
        Get the last traded price for a symbol.

        Args:
            symbol: Trading symbol
            exchange: Exchange code

        Returns:
            Last traded price as float
        """

    @abstractmethod
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
            instrument_token: Kite instrument token integer
            from_date: Start date string (YYYY-MM-DD)
            to_date: End date string (YYYY-MM-DD)
            interval: Candle interval ('30minute', 'day', etc.)

        Returns:
            List of candle dicts or lists
        """
