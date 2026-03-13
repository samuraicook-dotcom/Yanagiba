"""Emergency cleanup: Cancel ALL open orders and close ALL positions on Binance.

Usage:  cd ~/Yanagiba && python scripts/cleanup_positions.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Load .env the same way main.py does
try:
    from dotenv import load_dotenv
except ImportError:
    print("Installing python-dotenv...")
    os.system(f"{sys.executable} -m pip install python-dotenv -q")
    from dotenv import load_dotenv

# Check multiple .env locations (same as main.py)
primary_env = Path.home() / "Yanagiba" / ".env"
backup_env = Path.home() / ".yanagiba" / "backup" / ".env.bak"

if primary_env.exists():
    load_dotenv(primary_env)
elif backup_env.exists():
    load_dotenv(backup_env)
load_dotenv()  # Also check CWD

api_key = os.environ.get("BINANCE_API_KEY", "")
api_secret = os.environ.get("BINANCE_API_SECRET", "") or os.environ.get("BINANCE_SECRET", "")

if not api_key or not api_secret:
    print("\nERROR: No API keys found.")
    print(f"Checked: {primary_env}")
    print(f"Checked: {backup_env}")
    print(f"Checked: current directory .env")
    print("\nMake sure your .env file has:")
    print("  BINANCE_API_KEY=your-key-here")
    print("  BINANCE_API_SECRET=your-secret-here")
    sys.exit(1)

print(f"API key loaded: {api_key[:8]}...{api_key[-4:]}")


async def cleanup():
    try:
        import ccxt.async_support as ccxt
    except ImportError:
        print("Installing ccxt...")
        os.system(f"{sys.executable} -m pip install ccxt -q")
        import ccxt.async_support as ccxt

    exchange = ccxt.binance({
        "apiKey": api_key,
        "secret": api_secret,
        "options": {
            "defaultType": "future",
            "warnOnFetchOpenOrdersWithoutSymbol": False,
        },
        "enableRateLimit": True,
    })

    try:
        await exchange.load_markets()
        print("Connected to Binance Futures\n")

        # 1. Cancel all open orders
        print("=== CANCELLING ALL OPEN ORDERS ===")
        open_orders = await exchange.fetch_open_orders()
        print(f"Found {len(open_orders)} open orders")

        cancelled = 0
        for order in open_orders:
            try:
                await exchange.cancel_order(order["id"], order["symbol"])
                cancelled += 1
                print(f"  Cancelled: {order['side']} {order['amount']} {order['symbol']} ({order['type']})")
            except Exception as e:
                print(f"  Failed to cancel {order['id']}: {e}")

        print(f"\nCancelled {cancelled}/{len(open_orders)} orders\n")

        # 2. Close all open positions
        print("=== CLOSING ALL POSITIONS ===")
        positions = await exchange.fetch_positions()
        open_positions = [p for p in positions if abs(float(p.get("contracts", 0))) > 0]
        print(f"Found {len(open_positions)} open positions")

        closed = 0
        for pos in open_positions:
            try:
                symbol = pos["symbol"]
                contracts = abs(float(pos["contracts"]))
                side = "sell" if pos["side"] == "long" else "buy"
                await exchange.create_order(
                    symbol=symbol,
                    type="market",
                    side=side,
                    amount=contracts,
                    params={"reduceOnly": True},
                )
                closed += 1
                pnl = float(pos.get("unrealizedPnl", 0))
                print(f"  Closed: {pos['side']} {contracts} {symbol} (PnL: ${pnl:.2f})")
            except Exception as e:
                print(f"  Failed to close {pos['symbol']}: {e}")

        print(f"\nClosed {closed}/{len(open_positions)} positions")

        # 3. Final check
        remaining_orders = await exchange.fetch_open_orders()
        remaining_positions = [p for p in await exchange.fetch_positions() if abs(float(p.get("contracts", 0))) > 0]
        print(f"\n=== FINAL STATUS ===")
        print(f"Remaining orders: {len(remaining_orders)}")
        print(f"Remaining positions: {len(remaining_positions)}")

        if remaining_orders or remaining_positions:
            print("\nSome items couldn't be closed. Run again if needed.")
        else:
            print("\nAll clean! Safe to restart the bot.")

    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(cleanup())
