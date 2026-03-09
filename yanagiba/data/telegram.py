"""Telegram notification module for Yanagiba trading bot."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger("yanagiba.telegram")


class TelegramNotifier:
    """Sends trading updates to Telegram via Bot API."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self._session: aiohttp.ClientSession | None = None
        self.enabled = bool(bot_token and chat_id)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Telegram send failed: {resp.status}")
                    return False
                return True
        except Exception as e:
            logger.warning(f"Telegram error: {e}")
            return False

    async def notify_cycle_start(self, timestamp: str, assets: list[str]):
        await self.send(
            f"🔄 *Yanagiba Cycle*\n"
            f"Time: `{timestamp}`\n"
            f"Scanning: {len(assets)} assets"
        )

    async def notify_trade_approved(self, signal: dict[str, Any]):
        await self.send(
            f"✅ *Trade Approved*\n"
            f"Asset: `{signal.get('asset', '?')}`\n"
            f"Direction: {signal.get('direction', '?')}\n"
            f"Strategy: {signal.get('strategy', '?')}\n"
            f"Entry: `{signal.get('entry', '?')}`\n"
            f"SL: `{signal.get('stop_loss', '?')}`\n"
            f"TP1: `{signal.get('take_profit_1', '?')}`\n"
            f"R:R: {signal.get('risk_reward', '?')}\n"
            f"Confidence: {signal.get('confidence_score', '?')}"
        )

    async def notify_trade_rejected(self, strategy: str, reason: str):
        await self.send(
            f"❌ *Trade Rejected*\n"
            f"Strategy: {strategy}\n"
            f"Reason: {reason}"
        )

    async def notify_portfolio(self, portfolio: dict[str, Any]):
        await self.send(
            f"💰 *Portfolio Update*\n"
            f"Value: `${portfolio.get('total_value', 0):,.2f}`\n"
            f"Cash: `${portfolio.get('cash', 0):,.2f}`\n"
            f"Exposure: `{portfolio.get('exposure', 0):.2%}`\n"
            f"Daily P&L: `{portfolio.get('daily_pnl', 0):+.2f}`"
        )

    async def notify_error(self, symbol: str, error: str):
        await self.send(
            f"⚠️ *Error*\n"
            f"Symbol: `{symbol}`\n"
            f"Error: {error}"
        )

    async def notify_shutdown(self):
        await self.send("🛑 *Yanagiba bot shut down*")

    async def notify_startup(self, exchange: str, sandbox: bool, assets: list[str], interval: int):
        mode = "SANDBOX" if sandbox else "LIVE"
        await self.send(
            f"🚀 *Yanagiba Started*\n"
            f"Mode: *{mode}*\n"
            f"Exchange: {exchange}\n"
            f"Assets: {len(assets)}\n"
            f"Interval: {interval}s"
        )

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
