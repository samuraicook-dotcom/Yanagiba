"""
utils/logger.py — Structured Logging Setup

Configures logging to both console and file with a consistent format.
Format: [2026-04-15 10:45:00] [INFO] [module] message
"""

import logging
import sys
from pathlib import Path


class KaizenFormatter(logging.Formatter):
    """Custom formatter that includes the logger name as a module tag."""

    FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

    def __init__(self) -> None:
        super().__init__(fmt=self.FORMAT, datefmt=self.DATE_FORMAT)


def setup_logging(level: str = "INFO", log_file: str = "data/kaizen.log") -> None:
    """
    Configure root logger for the application.

    Sets up:
      - StreamHandler → stdout
      - FileHandler → log_file (appends)

    Args:
        level: Log level string ('DEBUG', 'INFO', 'WARNING', 'ERROR')
        log_file: Path to log file
    """
    # Ensure log directory exists
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    formatter = KaizenFormatter()

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove any existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for noisy in ("urllib3", "feedparser", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("kaizen").info(
        "Logging initialised — level=%s, file=%s", level, log_file
    )
