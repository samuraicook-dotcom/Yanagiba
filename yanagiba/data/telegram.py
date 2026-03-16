"""Telegram notification module for Yanagiba trading bot.

Supports:
- Outgoing notifications (trade updates, portfolio status)
- Incoming commands via long-polling (/status, /pnl, /trades, /stop, /help)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

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
        # Dedup: track rejection reasons this cycle to avoid spam
        self._cycle_rejections: dict[str, int] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, message: str, retries: int = 1) -> bool:
        if not self.enabled:
            return False
        for attempt in range(retries + 1):
            try:
                session = await self._get_session()
                async with session.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": message[:4096],
                        "parse_mode": "Markdown",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return True
                    body = await resp.text()
                    if resp.status == 401:
                        logger.error(
                            "Telegram bot token invalid — disabling"
                        )
                        self.enabled = False
                        return False
                    # Retry without Markdown on parse errors
                    if resp.status == 400 and "parse entities" in body:
                        async with session.post(
                            f"{self.base_url}/sendMessage",
                            json={
                                "chat_id": self.chat_id,
                                "text": message[:4096],
                            },
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as plain_resp:
                            if plain_resp.status == 200:
                                return True
                    logger.warning(
                        f"Telegram send failed ({resp.status}): "
                        f"{body[:200]}"
                    )
            except asyncio.TimeoutError:
                logger.warning(f"Telegram timeout (attempt {attempt + 1})")
            except Exception as e:
                logger.warning(f"Telegram error (attempt {attempt + 1}): {e}")
            if attempt < retries:
                await asyncio.sleep(2)
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
        # Suppress duplicate rejections (same reason) within a cycle
        if reason in self._cycle_rejections:
            self._cycle_rejections[reason] += 1
            return  # silently skip — summary sent at cycle end
        self._cycle_rejections[reason] = 1
        await self.send(
            f"❌ *Trade Rejected*\n"
            f"Strategy: {strategy}\n"
            f"Reason: {reason}"
        )

    async def flush_rejections(self):
        """Send summary of suppressed rejections at end of cycle."""
        dupes = {r: c for r, c in self._cycle_rejections.items() if c > 1}
        if dupes:
            lines = [f"❌ *Rejected ({c}x):* {r}" for r, c in dupes.items()]
            await self.send("\n".join(lines))
        self._cycle_rejections.clear()

    async def notify_position_closed(self, event: dict):
        pnl = event.get("pnl", 0)
        emoji = "🟢" if pnl >= 0 else "🔴"
        hit_type = event.get("type", "closed").replace("sandbox_", "").upper()
        await self.send(
            f"{emoji} *Position Closed ({hit_type})*\n"
            f"Symbol: `{event.get('symbol', '?')}`\n"
            f"Price: `${event.get('price', 0):,.2f}`\n"
            f"PnL: `${pnl:+.2f}`\n"
            f"Portfolio: `${event.get('portfolio_value', 0):,.2f}`"
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

    # --- Incoming command handling ---

    async def start_command_listener(
        self,
        handler: Callable[[str], Coroutine[Any, Any, str | None]],
    ):
        """Long-poll Telegram for incoming /commands and dispatch to handler.

        handler(command_text) should return a reply string or None.
        Runs forever — launch as an asyncio task.
        """
        if not self.enabled:
            return
        offset = 0
        logger.info("Telegram command listener started")
        while True:
            try:
                session = await self._get_session()
                async with session.get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=aiohttp.ClientTimeout(total=40),
                ) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(5)
                        continue
                    data = await resp.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message", {})
                        text = msg.get("text", "")
                        chat_id = str(msg.get("chat", {}).get("id", ""))
                        # Only respond to our authorized chat
                        if chat_id != self.chat_id or not text.startswith("/"):
                            continue
                        try:
                            reply = await handler(text.strip())
                            if reply:
                                await self.send(reply)
                        except Exception as e:
                            logger.warning(f"Command handler error: {e}")
                            await self.send(f"Command error: {e}")
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"Command poll error: {e}")
                await asyncio.sleep(5)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
