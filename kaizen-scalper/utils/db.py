"""
utils/db.py — SQLite Trade Storage

Creates and manages the trades, daily_summary, and sentiment_log tables.
"""

import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger("kaizen.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    quantity INTEGER NOT NULL,
    sl_price REAL,
    tp_price REAL,
    gross_pnl REAL,
    net_pnl REAL,
    charges REAL,
    status TEXT DEFAULT 'OPEN',
    exit_reason TEXT,
    tech_signal TEXT,
    sentiment_bias TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS daily_summary (
    date TEXT PRIMARY KEY,
    total_trades INTEGER,
    wins INTEGER,
    losses INTEGER,
    gross_pnl REAL,
    net_pnl REAL,
    total_charges REAL,
    max_drawdown REAL,
    capital_eod REAL
);

CREATE TABLE IF NOT EXISTS sentiment_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    bias TEXT NOT NULL,
    score REAL,
    headline_count INTEGER,
    top_headlines TEXT
);
"""


class TradeDB:
    """
    SQLite-backed persistent storage for trades, summaries, and sentiment logs.
    """

    def __init__(self, db_path: str = "data/trades.db") -> None:
        """
        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """Create all tables if they don't exist."""
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        logger.info("Database initialised at %s", self.db_path)

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def insert_trade(self, trade: dict) -> int:
        """
        Insert a new trade record (status=OPEN).

        Args:
            trade: Trade dict from executor

        Returns:
            Row ID of the inserted record
        """
        sql = """
        INSERT INTO trades
            (timestamp, symbol, direction, entry_price, quantity,
             sl_price, tp_price, status, tech_signal, sentiment_bias)
        VALUES
            (:timestamp, :symbol, :direction, :entry_price, :quantity,
             :sl_price, :tp_price, 'OPEN', :tech_signal, :sentiment_bias)
        """
        params = {
            "timestamp": trade.get("entry_time", ""),
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "entry_price": trade["entry_price"],
            "quantity": trade["quantity"],
            "sl_price": trade.get("sl_price"),
            "tp_price": trade.get("tp_price"),
            "tech_signal": trade.get("tech_signal"),
            "sentiment_bias": trade.get("sentiment_bias"),
        }
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            row_id = cursor.lastrowid
        logger.debug("Trade inserted: id=%d %s %s", row_id, trade["symbol"], trade["direction"])
        return row_id

    def update_trade_close(self, trade: dict) -> None:
        """
        Update a trade record with exit information.

        Args:
            trade: Trade dict with exit_price, net_pnl, charges, etc.
        """
        sql = """
        UPDATE trades
        SET exit_price = :exit_price,
            gross_pnl  = :gross_pnl,
            net_pnl    = :net_pnl,
            charges    = :charges,
            status     = 'CLOSED',
            exit_reason = :exit_reason
        WHERE symbol = :symbol
          AND direction = :direction
          AND status = 'OPEN'
        ORDER BY id DESC
        LIMIT 1
        """
        # SQLite doesn't support ORDER BY in UPDATE directly — use subquery
        sql = """
        UPDATE trades SET
            exit_price  = :exit_price,
            gross_pnl   = :gross_pnl,
            net_pnl     = :net_pnl,
            charges     = :charges,
            status      = 'CLOSED',
            exit_reason = :exit_reason
        WHERE id = (
            SELECT id FROM trades
            WHERE symbol = :symbol AND direction = :direction AND status = 'OPEN'
            ORDER BY id DESC LIMIT 1
        )
        """
        with self._connect() as conn:
            conn.execute(sql, {
                "symbol": trade["symbol"],
                "direction": trade["direction"],
                "exit_price": trade.get("exit_price"),
                "gross_pnl": trade.get("gross_pnl"),
                "net_pnl": trade.get("net_pnl"),
                "charges": trade.get("charges"),
                "exit_reason": trade.get("exit_reason"),
            })

    def get_trades_today(self, trade_date: str | None = None) -> list[dict]:
        """
        Fetch all trades for a given date (defaults to today).

        Args:
            trade_date: 'YYYY-MM-DD' string (optional)

        Returns:
            List of trade dicts
        """
        if trade_date is None:
            trade_date = date.today().isoformat()

        sql = "SELECT * FROM trades WHERE timestamp LIKE :date_prefix ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, {"date_prefix": f"{trade_date}%"}).fetchall()
        return [dict(row) for row in rows]

    def get_open_trades(self) -> list[dict]:
        """Return all trades with status=OPEN."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'OPEN'"
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def upsert_daily_summary(self, summary: dict) -> None:
        """
        Insert or update the daily summary record.

        Args:
            summary: Dict with keys matching the daily_summary table columns
        """
        sql = """
        INSERT OR REPLACE INTO daily_summary
            (date, total_trades, wins, losses, gross_pnl, net_pnl,
             total_charges, max_drawdown, capital_eod)
        VALUES
            (:date, :total_trades, :wins, :losses, :gross_pnl, :net_pnl,
             :total_charges, :max_drawdown, :capital_eod)
        """
        with self._connect() as conn:
            conn.execute(sql, summary)

    def get_daily_summary(self, summary_date: str | None = None) -> dict | None:
        """Fetch the daily summary for a date (defaults to today)."""
        if summary_date is None:
            summary_date = date.today().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM daily_summary WHERE date = ?", (summary_date,)
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Sentiment log
    # ------------------------------------------------------------------

    def log_sentiment(self, sentiment: dict) -> None:
        """
        Log a sentiment update.

        Args:
            sentiment: Dict with timestamp, bias, score, headline_count, top_headlines
        """
        sql = """
        INSERT INTO sentiment_log (timestamp, bias, score, headline_count, top_headlines)
        VALUES (:timestamp, :bias, :score, :headline_count, :top_headlines)
        """
        with self._connect() as conn:
            conn.execute(sql, {
                "timestamp": sentiment.get("timestamp"),
                "bias": sentiment.get("bias"),
                "score": sentiment.get("score"),
                "headline_count": sentiment.get("headline_count"),
                "top_headlines": json.dumps(sentiment.get("top_headlines", [])),
            })
