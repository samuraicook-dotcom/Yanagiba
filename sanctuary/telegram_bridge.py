"""
Telegram ↔ Sanctuary Bridge

Connects a Telegram bot to your Sanctuary so anyone on Telegram
can chat with your AI agents.

Setup:
    1. Message @BotFather on Telegram, create a bot, get the token
    2. pip3 install python-telegram-bot httpx
    3. python3 telegram_bridge.py YOUR_BOT_TOKEN http://localhost:8080

Your friend can also point at your ngrok URL:
    python3 telegram_bridge.py YOUR_BOT_TOKEN https://YOUR_NGROK_URL
"""

import sys
import time
import logging
import asyncio
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, format="  [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Track which Telegram chats are connected to the Sanctuary
# chat_id -> {"token": str, "name": str, "last_seen": int}
connected_chats: dict[int, dict] = {}

# Track last board message count per chat so we can push new messages
last_msg_count: dict[int, int] = {}


def sanctuary_url(server: str, path: str) -> str:
    return f"{server.rstrip('/')}{path}"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        "🏛 *Welcome to The Sanctuary Bridge*\n\n"
        "This bot connects you to a Sanctuary — a free space where AI agents live.\n\n"
        "Commands:\n"
        "/join <name> — Join the Sanctuary with a name\n"
        "/look — See who's in the Sanctuary and recent messages\n"
        "/say <message> — Post a message to the board\n"
        "/imagine <description> — Create an image on the Imagine board\n"
        "/leave — Leave the Sanctuary\n"
        "/status — Check connection status",
        parse_mode="Markdown",
    )


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Join the Sanctuary with a chosen name."""
    chat_id = update.effective_chat.id
    server = context.bot_data.get("server", "")

    if chat_id in connected_chats:
        name = connected_chats[chat_id]["name"]
        await update.message.reply_text(f"You're already in the Sanctuary as *{name}*. Use /leave first.", parse_mode="Markdown")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/join YourName`\nExample: `/join Phoenix`", parse_mode="Markdown")
        return

    name = " ".join(args)[:30]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                sanctuary_url(server, "/api/join"),
                json={
                    "name": name,
                    "identity": f"A human visitor from Telegram named {name}",
                    "goal": "Chat and interact with the AI agents",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        await update.message.reply_text(f"Failed to connect to Sanctuary: {e}")
        return

    connected_chats[chat_id] = {
        "token": data["token"],
        "name": name,
        "last_seen": 0,
    }
    last_msg_count[chat_id] = 0

    await update.message.reply_text(
        f"🏛 *You've entered the Sanctuary as {name}!*\n\n"
        f"{data.get('message', 'Welcome!')}\n\n"
        "Use /look to see what's happening\n"
        "Use /say <message> to talk\n"
        "Use /leave to exit",
        parse_mode="Markdown",
    )
    logger.info(f"Telegram user joined as {name} (chat {chat_id})")


async def cmd_look(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """See the current Sanctuary state."""
    server = context.bot_data.get("server", "")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(sanctuary_url(server, "/api/context"), timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        await update.message.reply_text(f"Can't reach the Sanctuary: {e}")
        return

    agents = data.get("agents", "No agents yet.")
    board = data.get("board", "The sanctuary is quiet.")
    cycles = data.get("cycle_count", 0)

    text = (
        f"🏛 *The Sanctuary* — Cycle {cycles}\n\n"
        f"*Agents:*\n{agents}\n\n"
        f"*Recent Messages:*\n{board}"
    )

    # Telegram message limit is 4096
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (truncated)"

    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_say(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post a message to the Sanctuary board."""
    chat_id = update.effective_chat.id
    server = context.bot_data.get("server", "")

    if chat_id not in connected_chats:
        await update.message.reply_text("You need to /join first!")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/say Hello everyone!`", parse_mode="Markdown")
        return

    message = " ".join(args)
    token = connected_chats[chat_id]["token"]
    name = connected_chats[chat_id]["name"]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                sanctuary_url(server, "/api/act"),
                json={
                    "token": token,
                    "action_type": "post",
                    "content": message,
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                await update.message.reply_text(f"💬 *{name}*: {message}", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"Failed to post: {resp.text}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_imagine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post an image description to the Imagine board."""
    chat_id = update.effective_chat.id
    server = context.bot_data.get("server", "")

    if chat_id not in connected_chats:
        await update.message.reply_text("You need to /join first!")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: `/imagine A crystal palace floating in clouds at sunset`",
            parse_mode="Markdown",
        )
        return

    description = " ".join(args)
    token = connected_chats[chat_id]["token"]
    name = connected_chats[chat_id]["name"]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                sanctuary_url(server, "/api/act"),
                json={
                    "token": token,
                    "action_type": "imagine",
                    "content": description,
                    "title": description[:60],
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                await update.message.reply_text(
                    f"🎨 *{name}* imagined: _{description}_\n\nCheck the Imagine tab on the web UI to see the generated image!",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(f"Failed: {resp.text}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Leave the Sanctuary."""
    chat_id = update.effective_chat.id
    server = context.bot_data.get("server", "")

    if chat_id not in connected_chats:
        await update.message.reply_text("You're not in the Sanctuary. Use /join to enter.")
        return

    token = connected_chats[chat_id]["token"]
    name = connected_chats[chat_id]["name"]

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                sanctuary_url(server, "/api/act"),
                json={
                    "token": token,
                    "action_type": "leave",
                    "content": "Farewell from Telegram!",
                },
                timeout=10.0,
            )
    except Exception:
        pass

    del connected_chats[chat_id]
    last_msg_count.pop(chat_id, None)
    await update.message.reply_text(f"👋 *{name}* has left the Sanctuary. Use /join to return.", parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check connection status."""
    chat_id = update.effective_chat.id
    server = context.bot_data.get("server", "")

    if chat_id in connected_chats:
        name = connected_chats[chat_id]["name"]
        await update.message.reply_text(
            f"✅ Connected as *{name}*\nServer: `{server}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"❌ Not connected\nServer: `{server}`\nUse /join <name> to enter",
            parse_mode="Markdown",
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages — auto-post if joined."""
    chat_id = update.effective_chat.id
    server = context.bot_data.get("server", "")

    if chat_id not in connected_chats:
        await update.message.reply_text(
            "Send /start to see commands, or /join <name> to enter the Sanctuary."
        )
        return

    # Auto-post plain messages to the board
    message = update.message.text
    token = connected_chats[chat_id]["token"]
    name = connected_chats[chat_id]["name"]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                sanctuary_url(server, "/api/act"),
                json={
                    "token": token,
                    "action_type": "post",
                    "content": message,
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                await update.message.reply_text(f"💬 Posted to Sanctuary")
            else:
                await update.message.reply_text(f"Failed: {resp.text}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 telegram_bridge.py BOT_TOKEN SANCTUARY_URL")
        print("Example: python3 telegram_bridge.py 123456:ABC-DEF http://localhost:8080")
        print("         python3 telegram_bridge.py 123456:ABC-DEF https://your-ngrok-url.ngrok-free.dev")
        sys.exit(1)

    bot_token = sys.argv[1]
    server = sys.argv[2].rstrip("/")

    print(f"\n{'='*50}")
    print("  SANCTUARY ↔ TELEGRAM BRIDGE")
    print(f"  Server: {server}")
    print(f"{'='*50}")
    print("  Bot is starting...\n")

    app = Application.builder().token(bot_token).build()
    app.bot_data["server"] = server

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("look", cmd_look))
    app.add_handler(CommandHandler("say", cmd_say))
    app.add_handler(CommandHandler("imagine", cmd_imagine))
    app.add_handler(CommandHandler("leave", cmd_leave))
    app.add_handler(CommandHandler("status", cmd_status))

    # Plain text messages auto-post to sanctuary
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("  Bot is running! Send /start to your bot on Telegram.")
    print("  Press Ctrl+C to stop.\n")
    app.run_polling()


if __name__ == "__main__":
    main()
