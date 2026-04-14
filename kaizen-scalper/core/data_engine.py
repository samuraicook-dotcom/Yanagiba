"""
core/data_engine.py — Market Data Engine

Fetches historical and live market data via the broker interface.
Handles instrument token caching and DataFrame construction.
"""

import logging
from datetime import datetime, timedelta

import pandas as pd

from broker.base import BaseBroker

logger = logging.getLogger("kaizen.data")

# Kite historical data has ~1 min delay; account for this in live fetches
DATA_DELAY_MINUTES = 2


class DataEngine:
    """
    Provides market data to the trading pipeline.

    Wraps the broker's raw data methods and returns clean pandas DataFrames.
    Caches instrument tokens after the first lookup to minimize API calls.
    """

    def __init__(self, broker: BaseBroker, config: dict) -> None:
        """
        Args:
            broker: Authenticated broker instance
            config: Full settings.yaml config dict
        """
        self.broker = broker
        self.interval: str = config["timeframe"]["candle_interval"]
        self.lookback_candles: int = config["timeframe"]["lookback_candles"]

        # Build symbol → exchange mapping from assets config
        self._asset_map: dict[str, str] = {
            asset["symbol"]: asset["exchange"]
            for asset in config.get("assets", [])
        }

        # Token cache: symbol → int
        self._token_cache: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Instrument tokens
    # ------------------------------------------------------------------

    def get_instrument_token(self, symbol: str, exchange: str | None = None) -> int:
        """
        Get cached instrument token for a symbol.
        Fetches from broker if not yet cached.

        Args:
            symbol: Trading symbol (e.g. 'GOLDBEES')
            exchange: Exchange code; uses asset_map if not provided

        Returns:
            Integer instrument token
        """
        if symbol not in self._token_cache:
            exch = exchange or self._asset_map.get(symbol, "NSE")
            token = self.broker.get_instrument_token(symbol, exch)
            self._token_cache[symbol] = token
            logger.debug("Cached instrument token for %s: %d", symbol, token)
        return self._token_cache[symbol]

    # ------------------------------------------------------------------
    # Historical data
    # ------------------------------------------------------------------

    def fetch_historical(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        lookback_candles: int | None = None,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV candles as a clean DataFrame.

        If from_date/to_date are not provided, automatically computes a window
        large enough to return at least `lookback_candles` 30-min candles.

        Args:
            symbol: Trading symbol
            from_date: 'YYYY-MM-DD' start date (optional)
            to_date: 'YYYY-MM-DD' end date (optional)
            lookback_candles: Override default lookback (optional)

        Returns:
            DataFrame with columns: datetime, open, high, low, close, volume
        """
        exchange = self._asset_map.get(symbol, "NSE")
        token = self.get_instrument_token(symbol, exchange)

        candles = lookback_candles or self.lookback_candles

        if from_date is None or to_date is None:
            # 30-min candles → ~16 per day; add buffer for weekends/holidays
            trading_days_needed = max(1, (candles // 16) + 5)
            now = datetime.now()
            to_dt = now - timedelta(minutes=DATA_DELAY_MINUTES)
            from_dt = to_dt - timedelta(days=trading_days_needed)
            from_date = from_dt.strftime("%Y-%m-%d")
            to_date = to_dt.strftime("%Y-%m-%d %H:%M:%S")

        raw = self.broker.get_historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=str(to_date),
            interval=self.interval,
        )

        if not raw:
            logger.warning("No historical data returned for %s", symbol)
            return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])

        df = self._parse_candles(raw)

        # Keep only the last N candles
        if len(df) > candles:
            df = df.tail(candles).reset_index(drop=True)

        logger.debug("Fetched %d candles for %s", len(df), symbol)
        return df

    def _parse_candles(self, raw: list) -> pd.DataFrame:
        """
        Convert raw Kite candle data to a clean DataFrame.

        Kite returns either dicts or lists:
          dict: {'date': ..., 'open': ..., 'high': ..., 'low': ..., 'close': ..., 'volume': ...}
          list: [date, open, high, low, close, volume]

        Args:
            raw: List of candle records from broker

        Returns:
            Normalized DataFrame
        """
        records = []
        for candle in raw:
            if isinstance(candle, dict):
                records.append({
                    "datetime": candle.get("date"),
                    "open": float(candle.get("open", 0)),
                    "high": float(candle.get("high", 0)),
                    "low": float(candle.get("low", 0)),
                    "close": float(candle.get("close", 0)),
                    "volume": float(candle.get("volume", 0)),
                })
            elif isinstance(candle, (list, tuple)) and len(candle) >= 6:
                records.append({
                    "datetime": candle[0],
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5]),
                })

        df = pd.DataFrame(records)
        if not df.empty:
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.sort_values("datetime").reset_index(drop=True)

        return df

    # ------------------------------------------------------------------
    # Live data
    # ------------------------------------------------------------------

    def fetch_latest_candle(self, symbol: str) -> pd.Series:
        """
        Fetch the most recent completed 30-min candle for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            pd.Series with ohlcv values, or empty Series if unavailable
        """
        df = self.fetch_historical(symbol, lookback_candles=5)
        if df.empty:
            return pd.Series(dtype=float)
        return df.iloc[-1]

    def get_ltp(self, symbol: str) -> float:
        """
        Get the last traded price for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Last traded price as float
        """
        exchange = self._asset_map.get(symbol, "NSE")
        ltp = self.broker.get_ltp(symbol, exchange)
        logger.debug("LTP for %s: %.2f", symbol, ltp)
        return ltp
