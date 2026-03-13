"""Market data provider using ccxt."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import ccxt.async_support as ccxt
import numpy as np
import pandas as pd

logger = logging.getLogger("yanagiba")


class ExchangeErrorKind:
    AUTH = "auth"           # Bad API key, revoked, IP whitelist
    RATE_LIMIT = "rate"     # Rate limited, temporary
    NETWORK = "network"     # Timeout, DNS, connection reset
    EXCHANGE = "exchange"   # Exchange-side error (maintenance, etc.)
    OTHER = "other"


def classify_exchange_error(exc: Exception) -> str:
    """Classify a ccxt exception as fatal (auth) or transient (network/rate).

    Checks most-specific subclasses first since ccxt uses deep inheritance.
    """
    if isinstance(exc, ccxt.AuthenticationError):
        return ExchangeErrorKind.AUTH
    if isinstance(exc, (ccxt.RateLimitExceeded, ccxt.DDoSProtection)):
        return ExchangeErrorKind.RATE_LIMIT
    if isinstance(exc, ccxt.ExchangeNotAvailable):
        return ExchangeErrorKind.EXCHANGE
    if isinstance(exc, (ccxt.NetworkError, ccxt.RequestTimeout)):
        return ExchangeErrorKind.NETWORK
    return ExchangeErrorKind.OTHER


class MarketDataProvider:
    """Fetches market data from exchanges via ccxt."""

    def __init__(
        self,
        exchange_id: str = "binance",
        sandbox: bool = True,
        market_type: str = "future",
        api_key: str = "",
        api_secret: str = "",
    ):
        exchange_class = getattr(ccxt, exchange_id)
        options: dict = {"enableRateLimit": True}
        self._market_type = market_type
        if market_type == "future":
            # Binance USDM perpetual futures use "swap" in ccxt
            options["defaultType"] = "swap"
            options["options"] = {"defaultType": "swap"}
        if api_key:
            options["apiKey"] = api_key
        if api_secret:
            options["secret"] = api_secret
        self.exchange: ccxt.Exchange = exchange_class(options)
        if sandbox:
            self.exchange.set_sandbox_mode(True)
        self._markets_loaded = False

    async def load_markets(self):
        if not self._markets_loaded:
            await self.exchange.load_markets()
            self._markets_loaded = True

    def resolve_symbol(self, symbol: str) -> str:
        """Resolve a base symbol (e.g. BTC/USDT) to the correct market symbol.

        For futures/swap markets, ccxt uses BTC/USDT:USDT format.
        Returns the resolved symbol, or the original if not found.
        """
        if not self._markets_loaded or not self.exchange.markets:
            return symbol
        # Already in exchange markets — use as-is
        if symbol in self.exchange.markets:
            return symbol
        # Try futures/swap format: SYMBOL:USDT
        if self._market_type == "future":
            swap_symbol = f"{symbol}:USDT"
            if swap_symbol in self.exchange.markets:
                return swap_symbol
        return symbol

    def symbol_exists(self, symbol: str) -> bool:
        """Check if a symbol (or its futures variant) exists on the exchange."""
        if not self._markets_loaded:
            return True  # assume exists if markets not loaded yet
        resolved = self.resolve_symbol(symbol)
        return resolved in self.exchange.markets

    async def close(self):
        await self.exchange.close()

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 200
    ) -> pd.DataFrame:
        resolved = self.resolve_symbol(symbol)
        raw = await self.exchange.fetch_ohlcv(resolved, timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    async def fetch_order_book(self, symbol: str, limit: int = 50) -> dict[str, Any]:
        resolved = self.resolve_symbol(symbol)
        book = await self.exchange.fetch_order_book(resolved, limit)
        bids = np.array(book["bids"])
        asks = np.array(book["asks"])

        bid_volume = float(bids[:, 1].sum()) if len(bids) > 0 else 0.0
        ask_volume = float(asks[:, 1].sum()) if len(asks) > 0 else 0.0
        total = bid_volume + ask_volume
        imbalance = (bid_volume - ask_volume) / total if total > 0 else 0.0

        best_bid = float(bids[0][0]) if len(bids) > 0 else 0.0
        best_ask = float(asks[0][0]) if len(asks) > 0 else 0.0

        return {
            "bids": book["bids"],
            "asks": book["asks"],
            "bid_volume": bid_volume,
            "ask_volume": ask_volume,
            "imbalance": imbalance,  # positive = buy pressure
            "spread": float(best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0,
            "best_bid": best_bid,
            "best_ask": best_ask,
        }

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        resolved = self.resolve_symbol(symbol)
        return await self.exchange.fetch_ticker(resolved)

    async def fetch_funding_rate(self, symbol: str) -> float | None:
        try:
            resolved = self.resolve_symbol(symbol)
            funding = await self.exchange.fetch_funding_rate(resolved)
            return funding.get("fundingRate")
        except Exception:
            return None

    async def fetch_multi_timeframe(
        self, symbol: str, timeframes: list[str], limit: int = 200
    ) -> dict[str, pd.DataFrame]:
        tasks = [self.fetch_ohlcv(symbol, tf, limit) for tf in timeframes]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out = {}
        for tf, result in zip(timeframes, results):
            if isinstance(result, pd.DataFrame):
                out[tf] = result
            elif isinstance(result, Exception):
                logger.warning(f"Failed to fetch {symbol} {tf}: {result}")
        return out
