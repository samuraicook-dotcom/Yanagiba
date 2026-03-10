"""Yanagiba — Main trading orchestrator.

Coordinates all four agents:
1. Market Analyst  → regime detection + sentiment
2. Strategy Engine → signal generation
3. Risk Manager    → trade approval gate
4. Execution Engine → order placement

All trades must pass through all four agents before execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime

from rich.console import Console
from rich.table import Table

from yanagiba.agents.execution_engine import ExecutionEngine
from yanagiba.agents.market_analyst import MarketAnalyst
from yanagiba.agents.position_tracker import PositionTracker
from yanagiba.agents.risk_manager import RiskManager
from yanagiba.agents.strategy_engine import StrategyEngine
from yanagiba.data.cme_gap import CMEGapTracker
from yanagiba.data.market_data import MarketDataProvider
from yanagiba.data.sentiment import SentimentTracker
from yanagiba.data.telegram import TelegramNotifier
from yanagiba.data.trade_journal import TradeJournal
from yanagiba.models.config import TradingConfig
from yanagiba.models.types import PortfolioState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("yanagiba")
console = Console()


class TradingBot:
    """Main orchestrator that runs the 4-agent trading pipeline."""

    def __init__(self, config: TradingConfig | None = None, telegram: TelegramNotifier | None = None):
        self.config = config or TradingConfig()
        self.data_provider = MarketDataProvider(
            self.config.exchange, self.config.sandbox, self.config.market_type,
            self.config.api_key, self.config.api_secret,
        )
        self.sentiment_tracker = SentimentTracker()
        self.analyst = MarketAnalyst(self.config)
        self.strategy = StrategyEngine(self.config)
        self.risk = RiskManager(self.config)
        self.execution = ExecutionEngine(self.config)
        self.position_tracker = PositionTracker()
        self.journal = TradeJournal()
        self.cme_gap = CMEGapTracker()
        self.telegram = telegram or TelegramNotifier("", "")
        self.portfolio = PortfolioState(total_value=49.0, cash=49.0)
        self._running = False

    async def run_cycle(self) -> list[dict]:
        """Run one full analysis + trading cycle across all assets."""
        # Ensure markets are loaded (needed for futures symbol resolution)
        await self.data_provider.load_markets()

        cycle_results = []
        timestamp = datetime.utcnow().isoformat()

        console.rule(f"[bold cyan]Yanagiba Cycle — {timestamp}")

        # 0. Sync open positions with exchange (detect closed trades)
        if self.config.sandbox:
            events = await self.position_tracker.simulate_sandbox_fills(
                self.data_provider.exchange, self.portfolio,
            )
        else:
            events = await self.position_tracker.sync_with_exchange(
                self.data_provider.exchange, self.portfolio,
            )
        for ev in events:
            logger.info(f"POSITION EVENT: {ev}")
            await self.telegram.notify_position_closed(ev)
        if events:
            self.journal.update_drawdown(self.portfolio.total_value)

        # 1. Fetch geopolitical / news sentiment
        logger.info("AGENT 1a: Fetching geopolitical & news sentiment...")
        sentiment_report = await self.sentiment_tracker.get_report()
        self._print_sentiment(sentiment_report)

        # Build asset list: core + gaming tokens if enabled
        all_assets = list(self.config.assets)
        if self.config.enable_gaming_sector:
            # Add gaming tokens when sector sentiment is positive
            if sentiment_report.gaming_score > 0 or sentiment_report.gaming_catalysts:
                all_assets.extend(
                    t for t in self.config.gaming_tokens if t not in all_assets
                )
                logger.info(
                    f"Gaming sector active (score={sentiment_report.gaming_score:+.1f}), "
                    f"added {len(self.config.gaming_tokens)} gaming tokens"
                )

        await self.telegram.notify_cycle_start(timestamp, all_assets)

        # CME gap tracking — record BTC price at Friday close / Sunday open
        if self.cme_gap.is_cme_close_window():
            try:
                btc_data = await self.data_provider.fetch_multi_timeframe(
                    "BTC/USDT", ["1h"],
                )
                if btc_data and "1h" in btc_data:
                    price = btc_data["1h"]["close"].iloc[-1]
                    self.cme_gap._friday_close = price
                    logger.info(f"CME Friday close recorded: ${price:,.0f}")
            except Exception as e:
                logger.debug(f"CME gap record failed: {e}")
        elif self.cme_gap.is_cme_open_window():
            friday = getattr(self.cme_gap, "_friday_close", None)
            if friday:
                try:
                    btc_data = await self.data_provider.fetch_multi_timeframe(
                        "BTC/USDT", ["1h"],
                    )
                    if btc_data and "1h" in btc_data:
                        sunday = btc_data["1h"]["close"].iloc[-1]
                        self.cme_gap.check_and_record_gap(friday, sunday)
                        self.cme_gap._friday_close = None
                except Exception as e:
                    logger.debug(f"CME gap check failed: {e}")

        # Reset correlation guard for new cycle
        self.risk.clear_cycle_trades()

        for symbol in all_assets:
            result = await self._process_asset(symbol, sentiment_report, timestamp)
            if result:
                cycle_results.append(result)

        self._print_portfolio_summary()
        await self.telegram.flush_rejections()
        await self.telegram.notify_portfolio({
            "total_value": self.portfolio.total_value,
            "cash": self.portfolio.cash,
            "exposure": self.portfolio.total_exposure_pct,
            "daily_pnl": self.portfolio.daily_pnl,
        })
        return cycle_results

    async def _process_asset(self, symbol: str, sentiment_report, timestamp: str) -> dict | None:
        console.rule(f"[bold yellow]{symbol}")

        try:
            # 1. MARKET ANALYST: Gather data and analyze
            logger.info(f"AGENT 1: Market Analyst scanning {symbol}...")
            ohlcv_data = await self.data_provider.fetch_multi_timeframe(
                symbol, self.config.timeframes
            )
            if not ohlcv_data:
                logger.warning(f"No data for {symbol}, skipping")
                return None

            order_book = await self.data_provider.fetch_order_book(symbol)
            funding_rate = await self.data_provider.fetch_funding_rate(symbol)

            analysis = self.analyst.analyze(
                ohlcv_data, order_book, funding_rate, symbol=symbol,
            )

            # CME gap bias for BTC — gaps fill 65-98% of the time
            if "BTC" in symbol:
                btc_price = ohlcv_data.get("1h", ohlcv_data.get(
                    "5m", next(iter(ohlcv_data.values()))
                ))["close"].iloc[-1]
                cme_bias = self.cme_gap.get_bias(btc_price)
                if abs(cme_bias) > 0:
                    analysis.sentiment_score = max(
                        -10, min(10, analysis.sentiment_score + cme_bias)
                    )
                    analysis.details["cme_gap_bias"] = round(cme_bias, 2)

            # Blend geopolitical sentiment into analysis (meaningful weight)
            geo_adjustment = sentiment_report.overall_score * 0.7
            analysis.sentiment_score = max(
                -10, min(10, analysis.sentiment_score + geo_adjustment)
            )
            if sentiment_report.risk_flags:
                analysis.details["geo_risk_flags"] = sentiment_report.risk_flags

            self._print_analysis(symbol, analysis)

            # 2. STRATEGY ENGINE: Generate signals
            logger.info(f"AGENT 2: Strategy Engine generating signals for {symbol}...")
            signals = self.strategy.generate_signals(symbol, analysis, ohlcv_data, order_book)

            if not signals:
                logger.info(f"No trade signals for {symbol}")
                return None

            self._print_signals(signals)

            # 3. RISK MANAGER: Evaluate each signal
            logger.info(f"AGENT 3: Risk Manager evaluating {len(signals)} signals...")
            approved_trades = []
            for sig in signals:
                risk_assessment = self.risk.evaluate(
                    sig, self.portfolio,
                    volatility_level=analysis.volatility_level,
                    funding_rate=funding_rate,
                )
                if risk_assessment.approved:
                    approved_trades.append((sig, risk_assessment))
                    logger.info(
                        f"  APPROVED: {sig.direction.value} {sig.asset} "
                        f"(confidence={sig.confidence_score}, R:R={sig.risk_reward})"
                    )
                    await self.telegram.notify_trade_approved(sig.to_dict())
                else:
                    logger.info(f"  REJECTED: {sig.strategy} — {risk_assessment.reason}")
                    await self.telegram.notify_trade_rejected(sig.strategy, risk_assessment.reason)

            if not approved_trades:
                logger.info(f"No trades approved for {symbol}")
                return None

            # 4. EXECUTION ENGINE: Execute approved trades
            logger.info(f"AGENT 4: Executing {len(approved_trades)} trades...")
            executed = []
            for sig, risk_assessment in approved_trades:
                plan = self.execution.create_execution_plan(sig, risk_assessment, self.portfolio)
                order = await self.execution.execute(plan, self.data_provider.exchange)
                executed.append({
                    "signal": sig.to_dict(),
                    "risk": risk_assessment.to_dict(),
                    "execution": plan.to_dict(),
                    "order": order.to_dict(),
                })
                # Log order status prominently
                logger.info(
                    f"  ORDER STATUS: {order.status} | {plan.side.upper()} "
                    f"{plan.position_size} {plan.symbol} @ {order.entry}"
                )
                # Only update exposure if order was actually placed
                # Track margin as % of portfolio: notional / leverage / portfolio_value
                if order.status in ("placed", "simulated"):
                    notional = plan.position_size * order.entry
                    asset_overrides = self.config.asset_overrides.get(
                        sig.asset, {}
                    )
                    max_lev = asset_overrides.get(
                        "max_leverage", self.config.max_leverage
                    )
                    margin_usd = notional / max_lev
                    margin_pct = margin_usd / self.portfolio.total_value
                    self.portfolio.total_exposure_pct += margin_pct
                    self.portfolio.cash -= margin_usd
                    logger.info(
                        f"  Margin: ${margin_usd:.2f} ({margin_pct:.1%}) | "
                        f"Total exposure: {self.portfolio.total_exposure_pct:.1%} | "
                        f"Cash: ${self.portfolio.cash:.2f}"
                    )
                    # Track position for monitoring
                    self.position_tracker.add_position(
                        symbol=sig.asset, side=plan.side,
                        entry_price=order.entry, quantity=plan.position_size,
                        margin_usd=margin_usd, stop_loss=sig.stop_loss,
                        take_profits=[sig.take_profit_1, sig.take_profit_2],
                    )

                # Log to trade journal
                self.journal.log_trade(
                    symbol=sig.asset, direction=sig.direction.value,
                    strategy=sig.strategy, entry=order.entry,
                    stop_loss=sig.stop_loss, take_profit=sig.take_profit_1,
                    position_size=plan.position_size,
                    confidence=sig.confidence_score,
                    risk_reward=sig.risk_reward, status=order.status,
                )

            return {
                "symbol": symbol,
                "timestamp": timestamp,
                "analysis": analysis.to_dict(),
                "sentiment": sentiment_report.to_dict(),
                "trades_executed": executed,
            }

        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            await self.telegram.notify_error(symbol, str(e))
            return None

    def _print_sentiment(self, report):
        table = Table(title="Geopolitical & News Sentiment")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Overall Score", f"{report.overall_score:+.2f}")
        table.add_row("Geo Score", f"{report.geo_score:+.2f}")
        table.add_row("News Score", f"{report.news_score:+.2f}")
        table.add_row("Gaming Score", f"{report.gaming_score:+.2f}")
        table.add_row("Fear & Greed", str(report.fear_greed_index or "N/A"))
        if report.risk_flags:
            table.add_row("Risk Flags", ", ".join(report.risk_flags[:3]))
        if report.opportunities:
            table.add_row("Opportunities", report.opportunities[0][:60])
        if report.gaming_catalysts:
            table.add_row("Gaming Catalysts", ", ".join(report.gaming_catalysts[:3]))
        console.print(table)

    def _print_analysis(self, symbol: str, analysis):
        table = Table(title=f"Market Analysis: {symbol}")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Regime", analysis.market_regime.value)
        table.add_row("Sentiment", f"{analysis.sentiment_score:+.2f}")
        table.add_row("Volatility", f"{analysis.volatility_level:.3f}")
        console.print(table)

    def _print_signals(self, signals):
        table = Table(title="Trade Signals")
        table.add_column("Strategy", style="cyan")
        table.add_column("Direction", style="green")
        table.add_column("Entry", style="white")
        table.add_column("SL", style="red")
        table.add_column("TP1", style="green")
        table.add_column("R:R", style="yellow")
        table.add_column("Conf", style="magenta")
        for s in signals:
            table.add_row(
                s.strategy, s.direction.value,
                f"{s.entry:.2f}", f"{s.stop_loss:.2f}", f"{s.take_profit_1:.2f}",
                f"{s.risk_reward:.1f}", f"{s.confidence_score:.1f}",
            )
        console.print(table)

    def _print_portfolio_summary(self):
        table = Table(title="Portfolio Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Total Value", f"${self.portfolio.total_value:,.2f}")
        table.add_row("Cash", f"${self.portfolio.cash:,.2f}")
        table.add_row("Exposure", f"{self.portfolio.total_exposure_pct:.2%}")
        table.add_row("Daily P&L", f"{self.portfolio.daily_pnl:+.2f}")

        # Position tracker stats
        pos_summary = self.position_tracker.get_summary()
        table.add_row("Open Positions", str(pos_summary["open_positions"]))
        table.add_row("Open Margin", f"${pos_summary['open_margin']:.2f}")

        # Trade journal stats
        perf = self.journal.get_performance()
        table.add_row("Total Trades", str(perf["total_trades"]))
        if perf["total_trades"] > 0:
            table.add_row("Win Rate", f"{perf['win_rate']}%")
            table.add_row("Total PnL", f"${perf['total_pnl']:+.2f}")
            table.add_row("Max Drawdown", f"{perf['max_drawdown']}%")
            table.add_row("Best Strategy", perf["best_strategy"])
        console.print(table)

    async def run_loop(self, interval_seconds: int = 180):
        """Run continuous trading loop with crash resilience.

        Individual cycle failures are caught and logged — the bot stays alive.
        After repeated consecutive failures it backs off to avoid spam, then
        resets on the next successful cycle.
        """
        self._running = True
        consecutive_failures = 0
        max_backoff = 300  # 5 min cap on failure backoff

        console.print("[bold green]Yanagiba Trading Bot started[/]")
        console.print(f"  Exchange: {self.config.exchange} ({'sandbox' if self.config.sandbox else 'LIVE'})")
        console.print(f"  Assets: {', '.join(self.config.assets)}")
        console.print(f"  Interval: {interval_seconds}s")
        console.print(f"  Telegram: {'enabled' if self.telegram.enabled else 'disabled'}")
        console.print()
        await self.telegram.notify_startup(
            self.config.exchange, self.config.sandbox,
            list(self.config.assets), interval_seconds,
        )

        try:
            while self._running:
                try:
                    results = await self.run_cycle()
                    consecutive_failures = 0  # reset on success
                    if results:
                        for r in results:
                            logger.info(f"Cycle result: {json.dumps(r, indent=2, default=str)}")
                except asyncio.CancelledError:
                    raise  # let cancellation propagate
                except Exception as e:
                    consecutive_failures += 1
                    backoff = min(interval_seconds * consecutive_failures, max_backoff)
                    logger.error(
                        f"Cycle failed ({consecutive_failures} in a row): {e} — "
                        f"retrying in {backoff}s"
                    )
                    await self.telegram.notify_error("CYCLE", str(e))
                    await asyncio.sleep(backoff)
                    continue  # skip the normal sleep, we already waited

                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self):
        self._running = False
        await self.telegram.notify_shutdown()
        await self.telegram.close()
        await self.data_provider.close()
        await self.sentiment_tracker.close()
        console.print("[bold red]Yanagiba shut down[/]")


def main():
    import os
    from pathlib import Path

    from dotenv import load_dotenv

    # Load .env from multiple locations (project dir, home dir, script dir)
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")
    load_dotenv(Path.home() / "Yanagiba" / ".env")
    load_dotenv()  # Also check CWD

    config = TradingConfig()

    # API keys: check CLI args first, then env vars
    config.api_key = os.environ.get("BINANCE_API_KEY", "")
    config.api_secret = os.environ.get("BINANCE_API_SECRET", "") or os.environ.get("BINANCE_SECRET", "")

    # Allow passing keys via CLI: --api-key KEY --api-secret SECRET
    if "--api-key" in sys.argv:
        idx = sys.argv.index("--api-key")
        if idx + 1 < len(sys.argv):
            config.api_key = sys.argv[idx + 1]
    if "--api-secret" in sys.argv:
        idx = sys.argv.index("--api-secret")
        if idx + 1 < len(sys.argv):
            config.api_secret = sys.argv[idx + 1]

    if config.api_key:
        console.print(f"[green]API key loaded: {config.api_key[:8]}...{config.api_key[-4:]}[/]")
    else:
        console.print("[yellow]WARNING: No API key found.[/]")

    # Parse CLI args
    if "--live" in sys.argv:
        config.sandbox = False
        if not config.api_key or not config.api_secret:
            console.print("[bold red]ERROR: --live requires API keys. Use one of:[/]")
            console.print("  1. Create ~/Yanagiba/.env with:")
            console.print("     BINANCE_API_KEY=your-key")
            console.print("     BINANCE_API_SECRET=your-secret")
            console.print("  2. Pass via CLI:")
            console.print("     --api-key YOUR_KEY --api-secret YOUR_SECRET")
            sys.exit(1)
        console.print("[bold red]WARNING: LIVE TRADING MODE[/]")

    if "--exchange" in sys.argv:
        idx = sys.argv.index("--exchange")
        if idx + 1 < len(sys.argv):
            config.exchange = sys.argv[idx + 1]

    if "--market" in sys.argv:
        idx = sys.argv.index("--market")
        if idx + 1 < len(sys.argv):
            config.market_type = sys.argv[idx + 1]  # "spot" or "future"

    interval = 180
    if "--interval" in sys.argv:
        idx = sys.argv.index("--interval")
        if idx + 1 < len(sys.argv):
            interval = int(sys.argv[idx + 1])

    telegram = TelegramNotifier(
        bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    if telegram.enabled:
        console.print("[bold green]Telegram notifications enabled[/]")
    else:
        console.print("[dim]Telegram notifications disabled (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env)[/]")

    bot = TradingBot(config, telegram=telegram)

    loop = asyncio.new_event_loop()

    def handle_signal(*_):
        bot._running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        loop.run_until_complete(bot.run_loop(interval))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
