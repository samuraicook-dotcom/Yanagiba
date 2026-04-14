"""
main.py — Kaizen Scalper Orchestrator

Entry point for the trading bot. Initialises all modules in order,
wires the scheduler, and runs until interrupted.

Usage:
    python main.py --mode paper     # Paper trading (default)
    python main.py --mode live      # Live trading (real orders)
"""

import argparse
import logging
import signal
import sys
import time
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load environment variables before anything else
load_dotenv()

from utils.logger import setup_logging

# Logger is configured after setup_logging() is called below
logger = logging.getLogger("kaizen.main")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Kaizen Scalper Trading Bot")
    parser.add_argument(
        "--mode",
        type=str,
        default="paper",
        choices=["paper", "live"],
        help="Trading mode: 'paper' (simulated) or 'live' (real orders)",
    )
    parser.add_argument(
        "--exchange",
        type=str,
        default="NSE",
        help="Exchange (default: NSE)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Override candle interval in seconds (for testing)",
    )
    return parser.parse_args()


def load_config() -> tuple[dict, dict]:
    """
    Load settings.yaml and keywords.yaml.

    Returns:
        (config dict, keywords dict)
    """
    with open("config/settings.yaml") as f:
        config = yaml.safe_load(f)
    with open("config/keywords.yaml") as f:
        keywords = yaml.safe_load(f)
    return config, keywords


def build_trading_cycle(
    symbol: str,
    can_short: bool,
    data_engine,
    technical_engine,
    sentiment_engine,
    signal_engine,
    risk_manager,
    executor,
    config: dict,
):
    """
    Build and return the trading cycle function for a single symbol.

    The cycle:
      1. Fetch last N candles
      2. Compute all indicators
      3. Read cached sentiment
      4. Generate tech signal
      5. Apply decision matrix
      6. Run risk checks
      7. Execute if approved

    Returns:
        Callable that accepts (symbol: str)
    """
    def trading_cycle(sym: str = symbol) -> None:
        try:
            logger.info("--- Trading cycle start: %s ---", sym)

            # 1. Fetch historical candles
            df = data_engine.fetch_historical(sym)
            if df.empty or len(df) < 5:
                logger.warning("Insufficient data for %s, skipping cycle", sym)
                return

            # 2. Compute indicators
            df = technical_engine.compute_all(df)

            # 3. Read sentiment (cached — never blocks here)
            sentiment = sentiment_engine.read_cache()

            # 4. Technical signal
            tech_signal = signal_engine.generate_technical_signal(df)

            # 5. Decision matrix — pass can_short so MCX assets allow SHORT
            decision = signal_engine.combine_with_sentiment(
                tech_signal, sentiment["bias"], can_short=can_short
            )

            logger.info(
                "%s: signal=%s, bias=%s, action=%s, size_factor=%s",
                sym,
                tech_signal.get("signal"),
                sentiment["bias"],
                decision["action"],
                decision.get("size_factor"),
            )

            # 6. Risk check
            if decision["action"] == "HOLD" or not decision.get("executable"):
                logger.info("%s: HOLD — no trade this cycle", sym)
                return

            can_trade, reason = risk_manager.can_trade(sym)
            if not can_trade:
                logger.info("%s: Risk check FAILED — %s", sym, reason)
                return

            # 7. Execute
            atr = float(df["atr"].iloc[-1])
            price = float(df["close"].iloc[-1])
            asset_cfg = next(
                (a for a in config["assets"] if a["symbol"] == sym), {}
            )
            exchange = asset_cfg.get("exchange", "NSE")

            executor.execute_trade(decision, price, atr, sym, exchange)

        except Exception as exc:
            logger.exception("Error in trading cycle for %s: %s", sym, exc)

    return trading_cycle


def generate_daily_summary(risk_manager, db, telegram, config: dict) -> None:
    """Generate and send the end-of-day summary."""
    try:
        state = risk_manager.get_state()
        trades_today = db.get_trades_today() if db else []

        wins = [t for t in trades_today if (t.get("net_pnl") or 0) > 0]
        losses = [t for t in trades_today if (t.get("net_pnl") or 0) <= 0]
        total_net = sum(t.get("net_pnl") or 0 for t in trades_today)
        total_gross = sum(t.get("gross_pnl") or 0 for t in trades_today)
        total_charges = sum(t.get("charges") or 0 for t in trades_today)

        summary = {
            "date": date.today().isoformat(),
            "total_trades": len(trades_today),
            "wins": len(wins),
            "losses": len(losses),
            "gross_pnl": total_gross,
            "net_pnl": total_net,
            "total_charges": total_charges,
            "max_drawdown": 0.0,  # Simplified: full calc needs equity curve
            "capital_eod": state["current_capital"],
        }

        if db:
            db.upsert_daily_summary(summary)

        if telegram:
            telegram.send_daily_summary(summary)

        logger.info("Daily summary generated: %s", summary)

    except Exception as exc:
        logger.exception("Failed to generate daily summary: %s", exc)


def main() -> None:
    """Main entry point — initialise and start the bot."""
    args = parse_args()

    # Must be run from project root
    if not Path("config/settings.yaml").exists():
        print("ERROR: Run from the kaizen-scalper/ directory.")
        sys.exit(1)

    config, keywords = load_config()

    # Override paper_trade based on CLI mode
    config["capital"]["paper_trade"] = (args.mode != "live")

    # 1. Logging
    setup_logging(
        level=config["logging"]["level"],
        log_file=config["logging"]["log_file"],
    )

    logger.info("=" * 50)
    logger.info("KAIZEN SCALPER starting in %s mode", args.mode.upper())
    logger.info("=" * 50)

    # 2. Database
    from utils.db import TradeDB
    db = TradeDB(config["logging"]["db_path"])

    # 3. Telegram
    from utils.telegram_notifier import TelegramNotifier
    telegram = TelegramNotifier()

    # 4. Broker
    from broker.zerodha import ZerodhaBroker
    broker = ZerodhaBroker()
    if not broker.login():
        logger.error("Broker login failed. Run scripts/login.py first.")
        telegram.send_risk_alert("Bot failed to start — Kite login failed.")
        sys.exit(1)

    # 5. Data engine
    from core.data_engine import DataEngine
    data_engine = DataEngine(broker, config)

    # 6. Technical engine
    from core.technicals import TechnicalEngine
    technical_engine = TechnicalEngine(config["technicals"])

    # 7. Sentiment engine
    from core.sentiment import SentimentEngine
    sentiment_engine = SentimentEngine(config["sentiment"], keywords)

    # 8. Signal engine
    from core.signals import SignalEngine
    signal_engine = SignalEngine(config["technicals"])

    # 9. Risk manager
    from core.risk_manager import RiskManager
    risk_manager = RiskManager(config["risk"], config["capital"])
    risk_manager.configure_time_rules(
        no_trade_zones=config["timeframe"]["no_trade_zones"],
        auto_square_off=config["execution"]["auto_square_off"],
    )

    # 10. Charges calculator
    from core.charges import ChargesCalculator
    charges_calc = ChargesCalculator()

    # 11. Order guard (duplicate-order prevention)
    from core.order_guard import OrderGuard
    order_guard = OrderGuard()

    # 12. Trade learner (adaptive sizing)
    from core.trade_learner import TradeLearner
    trade_learner = TradeLearner()
    trade_learner.load_from_db(db)  # bootstrap from historical trade outcomes

    # 13. Executor
    from core.executor import TradeExecutor
    executor = TradeExecutor(
        broker=broker,
        data_engine=data_engine,
        risk_manager=risk_manager,
        charges_calc=charges_calc,
        order_guard=order_guard,
        trade_learner=trade_learner,
        config=config,
        db=db,
        telegram=telegram,
    )
    executor.start_monitor(poll_interval_seconds=30)

    # 12. Scheduler
    from utils.scheduler import KaizenScheduler
    scheduler = KaizenScheduler()

    assets = [a["symbol"] for a in config["assets"]]

    # Build asset config map for quick lookup
    asset_map = {a["symbol"]: a for a in config["assets"]}

    # Wire up trading cycles — one per asset
    for symbol in assets:
        asset_cfg = asset_map.get(symbol, {})
        can_short = asset_cfg.get("can_short", False)
        cycle_fn = build_trading_cycle(
            symbol=symbol,
            can_short=can_short,
            data_engine=data_engine,
            technical_engine=technical_engine,
            sentiment_engine=sentiment_engine,
            signal_engine=signal_engine,
            risk_manager=risk_manager,
            executor=executor,
            config=config,
        )
        scheduler.add_trading_loop(cycle_fn, [symbol])
        logger.info("Registered cycle for %s (can_short=%s)", symbol, can_short)

    # Sentiment update job
    scheduler.add_sentiment_update(sentiment_engine.update)

    # Daily open: reset counters + load instruments
    def daily_open():
        logger.info("Daily open — resetting counters and loading instruments")
        risk_manager.reset_daily()
        broker.fetch_instruments()

    scheduler.add_daily_open(daily_open)

    # Auto square-off
    scheduler.add_square_off(executor.square_off_all)

    # Daily summary
    def daily_summary_job():
        generate_daily_summary(risk_manager, db, telegram, config)

    scheduler.add_daily_summary(daily_summary_job)

    # Start scheduler
    scheduler.start()

    # Notify startup
    telegram.send_startup(args.mode, assets)

    logger.info("Scheduler started. Jobs: %s", scheduler.list_jobs())
    logger.info("Bot running. Press Ctrl+C to stop.")

    # Graceful shutdown handler
    def shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping...")
        executor.square_off_all()
        executor.stop_monitor()
        scheduler.shutdown(wait=False)
        telegram.send_shutdown("Manual stop (Ctrl+C)")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep alive
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
