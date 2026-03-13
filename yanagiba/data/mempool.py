"""BTC mempool whale monitor via Blockchain.com API.

Monitors the Bitcoin mempool for large unconfirmed transactions.
Whale-sized BTC movements to known exchange addresses signal
incoming sell pressure. No API key required.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger("yanagiba")

# Blockchain.com API (free, no key needed)
MEMPOOL_URL = "https://blockchain.info/unconfirmed-transactions?format=json"

# Known exchange deposit address prefixes (BTC)
# These are well-known hot wallet prefixes for major exchanges
KNOWN_EXCHANGE_PREFIXES = [
    "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3",  # Binance cold
    "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo",  # Bitfinex
    "3M219KR5vEneNb47ewrPfWyb5jQ2DjxRP6",  # Coinbase
    "bc1qa5wkgaew2dkv56kc6hp3",  # Kraken (prefix)
]

# Minimum BTC value to consider a "whale" transaction
WHALE_THRESHOLD_BTC = 10.0  # ~$1M+ at current prices


@dataclass
class MempoolWhaleAlert:
    """A large unconfirmed BTC transaction."""

    tx_hash: str
    value_btc: float
    value_usd: float  # estimated
    to_exchange: bool  # True if destination looks like exchange
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "tx_hash": self.tx_hash[:16] + "...",
            "value_btc": round(self.value_btc, 4),
            "value_usd": round(self.value_usd, 2),
            "to_exchange": self.to_exchange,
        }


@dataclass
class MempoolSummary:
    """Aggregated mempool whale activity."""

    whale_tx_count: int = 0
    total_whale_btc: float = 0.0
    total_whale_usd: float = 0.0
    exchange_bound_btc: float = 0.0  # BTC heading to exchanges
    exchange_bound_count: int = 0
    alerts: list[MempoolWhaleAlert] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "whale_tx_count": self.whale_tx_count,
            "total_whale_btc": round(self.total_whale_btc, 4),
            "total_whale_usd": round(self.total_whale_usd, 2),
            "exchange_bound_btc": round(self.exchange_bound_btc, 4),
            "exchange_bound_count": self.exchange_bound_count,
        }


class MempoolMonitor:
    """Monitors BTC mempool for whale transactions.

    Fetches unconfirmed transactions from Blockchain.com and filters
    for whale-sized transfers. Detects exchange-bound flows.
    """

    def __init__(self, whale_threshold_btc: float = WHALE_THRESHOLD_BTC):
        self._threshold = whale_threshold_btc
        self._session: aiohttp.ClientSession | None = None
        self._last_fetch: float = 0
        self._min_interval = 60  # 1 minute between fetches (be nice to free API)
        self._seen_hashes: set[str] = set()  # dedup

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def scan_mempool(self, btc_price_usd: float = 100_000) -> MempoolSummary:
        """Scan mempool for whale transactions.

        Args:
            btc_price_usd: Current BTC price for USD estimation.
        """
        now = time.time()
        if now - self._last_fetch < self._min_interval:
            return MempoolSummary()

        summary = MempoolSummary()

        try:
            session = await self._get_session()
            async with session.get(MEMPOOL_URL) as resp:
                if resp.status != 200:
                    return summary

                data = await resp.json()
                txs = data.get("txs", [])

                self._last_fetch = now

                for tx in txs:
                    tx_hash = tx.get("hash", "")
                    if tx_hash in self._seen_hashes:
                        continue

                    # Sum outputs to get total value
                    total_satoshi = sum(
                        out.get("value", 0)
                        for out in tx.get("out", [])
                    )
                    total_btc = total_satoshi / 1e8

                    if total_btc < self._threshold:
                        continue

                    self._seen_hashes.add(tx_hash)
                    usd_value = total_btc * btc_price_usd

                    # Check if any output goes to a known exchange address
                    to_exchange = False
                    for out in tx.get("out", []):
                        addr = out.get("addr", "")
                        if any(addr.startswith(prefix) for prefix in KNOWN_EXCHANGE_PREFIXES):
                            to_exchange = True
                            break

                    alert = MempoolWhaleAlert(
                        tx_hash=tx_hash,
                        value_btc=total_btc,
                        value_usd=usd_value,
                        to_exchange=to_exchange,
                        timestamp=now,
                    )
                    summary.alerts.append(alert)
                    summary.whale_tx_count += 1
                    summary.total_whale_btc += total_btc
                    summary.total_whale_usd += usd_value

                    if to_exchange:
                        summary.exchange_bound_btc += total_btc
                        summary.exchange_bound_count += 1

                # Cap seen hashes to prevent memory bloat
                if len(self._seen_hashes) > 5000:
                    self._seen_hashes = set(list(self._seen_hashes)[-2000:])

                if summary.whale_tx_count > 0:
                    logger.info(
                        f"Mempool whales: {summary.whale_tx_count} txs, "
                        f"{summary.total_whale_btc:.2f} BTC "
                        f"({summary.exchange_bound_count} exchange-bound)"
                    )

        except Exception as e:
            logger.debug(f"Mempool scan failed: {e}")

        return summary

    def get_mempool_score(self, summary: MempoolSummary) -> float:
        """Score mempool activity for BTC analysis (-2 to +2).

        High exchange-bound whale activity = sell pressure coming (-1 to -2)
        High non-exchange whale activity = accumulation (+0.5 to +1)
        No whale activity = neutral (0)
        """
        if summary.whale_tx_count == 0:
            return 0.0

        if summary.exchange_bound_count >= 3:
            return -2.0  # heavy exchange inflow, dump incoming
        elif summary.exchange_bound_count >= 1:
            return -1.0  # some sell pressure

        # Non-exchange whale activity = likely accumulation
        if summary.whale_tx_count >= 3:
            return 1.0
        elif summary.whale_tx_count >= 1:
            return 0.5

        return 0.0
