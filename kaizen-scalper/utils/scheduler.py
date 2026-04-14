"""
utils/scheduler.py — APScheduler Job Configuration

Sets up all scheduled jobs:
  - Trading loop: every 30 min at :15 and :45
  - Sentiment update: every 30 min at :00 and :30
  - Daily 09:10 IST: reset counters, load instruments
  - Daily 15:10 IST: square off all positions
  - Daily 15:35 IST: generate daily summary
"""

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("kaizen.scheduler")

# IST is UTC+5:30
IST_TIMEZONE = "Asia/Kolkata"


class KaizenScheduler:
    """
    Wraps APScheduler to configure and manage all bot job schedules.
    """

    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler(timezone=IST_TIMEZONE)

    def add_trading_loop(self, fn: Callable, assets: list[str]) -> None:
        """
        Schedule the trading cycle to run at :15 and :45 every 30 min
        (aligned to NSE 30-min candle close).

        Args:
            fn: Function to call — receives `symbol` as argument
            assets: List of symbols to run the loop for
        """
        for symbol in assets:
            # :15 past every hour during market hours (09:15 to 14:45)
            self._scheduler.add_job(
                func=fn,
                trigger=CronTrigger(
                    hour="9-14",
                    minute="15,45",
                    timezone=IST_TIMEZONE,
                ),
                args=[symbol],
                id=f"trading_{symbol}_quarter",
                name=f"Trading loop {symbol} (:15/:45)",
                replace_existing=True,
            )
        logger.info("Trading loop scheduled for assets: %s", assets)

    def add_sentiment_update(self, fn: Callable) -> None:
        """
        Schedule sentiment update at :00 and :30 every hour
        (offset from trading to avoid overlap).

        Args:
            fn: Sentiment update function (no arguments)
        """
        self._scheduler.add_job(
            func=fn,
            trigger=CronTrigger(
                hour="8-15",
                minute="0,30",
                timezone=IST_TIMEZONE,
            ),
            id="sentiment_update",
            name="Sentiment RSS update",
            replace_existing=True,
        )
        logger.info("Sentiment update scheduled at :00 and :30")

    def add_daily_open(self, fn: Callable) -> None:
        """
        Schedule the daily market-open task at 09:10 IST.
        Resets daily counters and pre-loads instrument tokens.

        Args:
            fn: Function to call at market open
        """
        self._scheduler.add_job(
            func=fn,
            trigger=CronTrigger(
                hour=9,
                minute=10,
                day_of_week="mon-fri",
                timezone=IST_TIMEZONE,
            ),
            id="daily_open",
            name="Daily open — reset counters + load instruments",
            replace_existing=True,
        )
        logger.info("Daily open task scheduled at 09:10 IST")

    def add_square_off(self, fn: Callable) -> None:
        """
        Schedule auto square-off at 15:10 IST.

        Args:
            fn: Square-off function (no arguments)
        """
        self._scheduler.add_job(
            func=fn,
            trigger=CronTrigger(
                hour=15,
                minute=10,
                day_of_week="mon-fri",
                timezone=IST_TIMEZONE,
            ),
            id="auto_square_off",
            name="Auto square-off all positions",
            replace_existing=True,
        )
        logger.info("Auto square-off scheduled at 15:10 IST")

    def add_daily_summary(self, fn: Callable) -> None:
        """
        Schedule daily summary generation at 15:35 IST.

        Args:
            fn: Summary generation function (no arguments)
        """
        self._scheduler.add_job(
            func=fn,
            trigger=CronTrigger(
                hour=15,
                minute=35,
                day_of_week="mon-fri",
                timezone=IST_TIMEZONE,
            ),
            id="daily_summary",
            name="Daily performance summary",
            replace_existing=True,
        )
        logger.info("Daily summary scheduled at 15:35 IST")

    def start(self) -> None:
        """Start the scheduler."""
        self._scheduler.start()
        logger.info("Scheduler started — %d jobs active", len(self._scheduler.get_jobs()))

    def shutdown(self, wait: bool = True) -> None:
        """Gracefully shut down the scheduler."""
        self._scheduler.shutdown(wait=wait)
        logger.info("Scheduler shut down")

    def list_jobs(self) -> list[str]:
        """Return list of scheduled job names."""
        return [job.name for job in self._scheduler.get_jobs()]
