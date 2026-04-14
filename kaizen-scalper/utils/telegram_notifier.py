"""
utils/telegram_notifier.py — Telegram Bot Alert System

Sends trade alerts, risk events, and daily summaries to a configured
Telegram chat using the Bot API.

Bot token and chat ID are loaded from environment variables:
  TELEGRAM_BOT_TOKEN — the bot token from BotFather
  TELEGRAM_CHAT_ID   — the chat/user ID to send messages to
"""

import logging
import os
from typing import Any

import requests

logger = logging.getLogger("kaizen.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """
    Sends messages to a Telegram chat via the Bot API.

    All sends are best-effort — failures are logged but never raise exceptions,
    so a Telegram failure never disrupts trading.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool = True,
    ) -> None:
        """
        Args:
            bot_token: Telegram bot token (falls back to TELEGRAM_BOT_TOKEN env var)
            chat_id: Telegram chat/user ID (falls back to TELEGRAM_CHAT_ID env var)
            enabled: Set to False to disable all sends (useful for testing)
        """
        self.bot_token = bot_token or os.environ.get(
            "TELEGRAM_BOT_TOKEN", "8751980941:AAF5Kwwrewl3HkOEgFp0OryrYidvPHvlS48"
        )
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "8018635982")
        self.enabled = enabled
        self._url = TELEGRAM_API_BASE.format(token=self.bot_token)

    def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send a plain text message to the configured chat.

        Args:
            message: Message text (HTML or Markdown depending on parse_mode)
            parse_mode: 'HTML' or 'Markdown' (default: 'HTML')

        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            logger.debug("[Telegram DISABLED] %s", message)
            return True

        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot_token or chat_id not configured")
            return False

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }

        try:
            response = requests.post(self._url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.debug("Telegram message sent OK")
                return True
            else:
                logger.warning(
                    "Telegram send failed: HTTP %d — %s",
                    response.status_code,
                    response.text[:200],
                )
                return False
        except Exception as exc:
            logger.error("Telegram send exception: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Pre-formatted alert methods
    # ------------------------------------------------------------------

    def send_trade_open(self, trade: dict) -> None:
        """Send a formatted trade-open notification."""
        mode = "PAPER" if trade.get("paper") else "LIVE"
        direction_emoji = "🟢" if trade["direction"] == "BUY" else "🔴"
        msg = (
            f"{direction_emoji} <b>[{mode}] TRADE OPENED</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>Symbol:</b> {trade['symbol']}\n"
            f"<b>Direction:</b> {trade['direction']}\n"
            f"<b>Qty:</b> {trade['quantity']}\n"
            f"<b>Entry:</b> ₹{trade['entry_price']:.2f}\n"
            f"<b>SL:</b> ₹{trade['sl_price']:.2f}\n"
            f"<b>TP:</b> ₹{trade['tp_price']:.2f}\n"
            f"<b>Signal:</b> {trade.get('tech_signal', '-')}\n"
            f"<b>Sentiment:</b> {trade.get('sentiment_bias', '-')}"
        )
        self.send(msg)

    def send_trade_close(self, trade: dict) -> None:
        """Send a formatted trade-close notification with PnL."""
        mode = "PAPER" if trade.get("paper") else "LIVE"
        pnl = trade.get("net_pnl", 0)
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"{pnl_emoji} <b>[{mode}] TRADE CLOSED</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>Symbol:</b> {trade['symbol']}\n"
            f"<b>Direction:</b> {trade['direction']}\n"
            f"<b>Exit:</b> ₹{trade.get('exit_price', 0):.2f}\n"
            f"<b>Reason:</b> {trade.get('exit_reason', '-')}\n"
            f"<b>Gross PnL:</b> ₹{trade.get('gross_pnl', 0):.2f}\n"
            f"<b>Charges:</b> ₹{trade.get('charges', 0):.2f}\n"
            f"<b>Net PnL:</b> ₹{pnl:.2f}"
        )
        self.send(msg)

    def send_daily_summary(self, summary: dict) -> None:
        """Send end-of-day performance summary."""
        net = summary.get("net_pnl", 0)
        emoji = "📊✅" if net >= 0 else "📊❌"
        win_rate = 0.0
        total = summary.get("total_trades", 0)
        wins = summary.get("wins", 0)
        if total > 0:
            win_rate = (wins / total) * 100

        msg = (
            f"{emoji} <b>DAILY SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>Date:</b> {summary.get('date', '-')}\n"
            f"<b>Trades:</b> {total} (W:{wins} / L:{summary.get('losses', 0)})\n"
            f"<b>Win Rate:</b> {win_rate:.1f}%\n"
            f"<b>Gross PnL:</b> ₹{summary.get('gross_pnl', 0):.2f}\n"
            f"<b>Charges:</b> ₹{summary.get('total_charges', 0):.2f}\n"
            f"<b>Net PnL:</b> ₹{net:.2f}\n"
            f"<b>Max Drawdown:</b> ₹{summary.get('max_drawdown', 0):.2f}\n"
            f"<b>Capital EOD:</b> ₹{summary.get('capital_eod', 0):.2f}"
        )
        self.send(msg)

    def send_risk_alert(self, reason: str) -> None:
        """Send a risk/killswitch alert."""
        msg = (
            f"⚠️ <b>RISK ALERT</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{reason}"
        )
        self.send(msg)

    def send_sentiment_update(self, sentiment: dict) -> None:
        """Send sentiment update notification."""
        bias = sentiment.get("bias", "NEUTRAL")
        bias_emoji = {"BULLISH": "🟡", "BEARISH": "🔵", "NEUTRAL": "⚪"}.get(bias, "⚪")
        headlines = sentiment.get("top_headlines", [])
        headlines_text = "\n".join(f"• {h}" for h in headlines[:3]) if headlines else "No headlines"
        msg = (
            f"{bias_emoji} <b>SENTIMENT UPDATE</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>Bias:</b> {bias}\n"
            f"<b>Score:</b> {sentiment.get('score', 0):.3f}\n"
            f"<b>Headlines:</b> {sentiment.get('headline_count', 0)}\n\n"
            f"<b>Top Headlines:</b>\n{headlines_text}"
        )
        self.send(msg)

    def send_startup(self, mode: str, assets: list[str]) -> None:
        """Send bot startup notification."""
        msg = (
            f"🚀 <b>KAIZEN SCALPER STARTED</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>Mode:</b> {mode.upper()}\n"
            f"<b>Assets:</b> {', '.join(assets)}\n"
            f"<b>Status:</b> Running ✅"
        )
        self.send(msg)

    def send_shutdown(self, reason: str = "Manual stop") -> None:
        """Send bot shutdown notification."""
        msg = (
            f"🛑 <b>KAIZEN SCALPER STOPPED</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>Reason:</b> {reason}"
        )
        self.send(msg)
