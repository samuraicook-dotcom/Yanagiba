"""
scripts/paper_trade.py — Paper Trading Entry Point

Convenience wrapper that starts the bot in paper trading mode.
Equivalent to: python main.py --mode paper

Usage:
    python scripts/paper_trade.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import main as bot


if __name__ == "__main__":
    sys.argv = ["paper_trade.py", "--mode", "paper"]
    bot.main()
