"""
scripts/backtest_run.py — Backtest Runner

Fetches historical data, runs the backtest engine, and prints a performance report.
Default: last 60 trading days for all configured assets.

Usage:
    python scripts/backtest_run.py
    python scripts/backtest_run.py --symbol GOLDBEES --days 30 --bias BULLISH
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import yaml

from backtest.engine import BacktestEngine
from backtest.report import generate_report
from broker.zerodha import ZerodhaBroker
from core.data_engine import DataEngine
from utils.logger import setup_logging


def main() -> None:
    """Run backtest for configured assets and print report."""
    parser = argparse.ArgumentParser(description="Kaizen Scalper Backtest")
    parser.add_argument(
        "--symbol", type=str, default=None,
        help="Symbol to backtest (default: all assets in config)"
    )
    parser.add_argument(
        "--days", type=int, default=60,
        help="Number of trading days to backtest (default: 60)"
    )
    parser.add_argument(
        "--bias", type=str, default="NEUTRAL",
        choices=["BULLISH", "BEARISH", "NEUTRAL"],
        help="Static sentiment bias to use (default: NEUTRAL)"
    )
    args = parser.parse_args()

    # Load config
    with open("config/settings.yaml") as f:
        config = yaml.safe_load(f)

    setup_logging(
        level=config["logging"]["level"],
        log_file=config["logging"]["log_file"],
    )

    # Determine symbols to test
    all_symbols = [a["symbol"] for a in config["assets"]]
    symbols = [args.symbol] if args.symbol else all_symbols

    # Date range
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=args.days + 10)).strftime("%Y-%m-%d")

    # Connect broker
    broker = ZerodhaBroker()
    if not broker.login():
        print("Broker login failed — cannot fetch historical data.")
        print("Ensure KITE_API_KEY and KITE_ACCESS_TOKEN are set in .env")
        sys.exit(1)

    data_engine = DataEngine(broker, config)
    backtest_engine = BacktestEngine(config)

    for symbol in symbols:
        print(f"\nRunning backtest for {symbol} ({args.days} days, bias={args.bias})...")
        df = data_engine.fetch_historical(
            symbol=symbol,
            from_date=from_date,
            to_date=to_date,
            lookback_candles=args.days * 13,  # ~13 30-min candles per trading day
        )

        if df.empty:
            print(f"No data available for {symbol}. Skipping.")
            continue

        results = backtest_engine.run(df, symbol=symbol, sentiment_bias=args.bias)
        generate_report(results)


if __name__ == "__main__":
    main()
