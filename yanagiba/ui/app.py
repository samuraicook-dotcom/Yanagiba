"""Yanagiba Trading Bot — Web Dashboard & Configuration UI.

Run with:  python -m yanagiba.ui.app
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from yanagiba.models.config import TradingConfig

app = Flask(__name__)

CONFIG_DIR = Path.home() / ".yanagiba"
CONFIG_FILE = CONFIG_DIR / "config.json"
JOURNAL_DIR = Path.home() / "Yanagiba" / "journal"
PORTFOLIO_FILE = JOURNAL_DIR / "portfolio_state.json"
POSITIONS_FILE = JOURNAL_DIR / "open_positions.json"
TRADES_FILE = JOURNAL_DIR / "trades.jsonl"
STATS_FILE = JOURNAL_DIR / "stats.json"
LOG_FILE = Path.home() / "Yanagiba" / "logs" / "yanagiba.log"


def _config_to_dict(config: TradingConfig) -> dict:
    """Convert TradingConfig to a JSON-safe dict."""
    return asdict(config)


def _dict_to_config(data: dict) -> TradingConfig:
    """Build TradingConfig from a dict, casting types appropriately."""
    field_types = {f.name: f.type for f in fields(TradingConfig)}
    filtered = {}
    for k, v in data.items():
        if k not in field_types:
            continue
        # Cast numeric types from JSON/form strings
        ft = field_types[k]
        try:
            if ft == "float" and not isinstance(v, float):
                v = float(v)
            elif ft == "int" and not isinstance(v, int):
                v = int(v)
            elif ft == "bool" and not isinstance(v, bool):
                v = str(v).lower() in ("true", "1", "yes")
        except (ValueError, TypeError):
            pass  # keep original value
        filtered[k] = v
    return TradingConfig(**filtered)


def load_config() -> TradingConfig:
    """Load config from disk, or return defaults."""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return _dict_to_config(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return TradingConfig()


def save_config(config: TradingConfig) -> None:
    """Persist config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(_config_to_dict(config), indent=2))


# --- Routes ---


@app.route("/")
def index():
    config = load_config()
    return render_template("dashboard.html", config=_config_to_dict(config))


@app.route("/api/config", methods=["GET"])
def get_config():
    config = load_config()
    return jsonify(_config_to_dict(config))


@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.get_json(force=True)
    # Merge with existing config
    current = _config_to_dict(load_config())
    current.update(data)
    config = _dict_to_config(current)
    save_config(config)
    return jsonify({"status": "ok", "config": _config_to_dict(config)})


@app.route("/api/config/reset", methods=["POST"])
def reset_config():
    config = TradingConfig()
    save_config(config)
    return jsonify({"status": "ok", "config": _config_to_dict(config)})


@app.route("/api/config/export", methods=["GET"])
def export_config():
    config = load_config()
    return jsonify(_config_to_dict(config))


@app.route("/api/config/import", methods=["POST"])
def import_config():
    data = request.get_json(force=True)
    config = _dict_to_config(data)
    save_config(config)
    return jsonify({"status": "ok", "config": _config_to_dict(config)})


# --- Live Data API ---


def _read_json(path: Path, default=None):
    """Read a JSON file, returning default on any error."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return default if default is not None else {}


@app.route("/api/portfolio")
def get_portfolio():
    """Current portfolio state (value, cash, exposure, PnL)."""
    data = _read_json(PORTFOLIO_FILE, {
        "total_value": 0, "cash": 0, "total_exposure_pct": 0, "daily_pnl": 0,
    })
    return jsonify(data)


@app.route("/api/positions")
def get_positions():
    """Open positions from the position tracker."""
    data = _read_json(POSITIONS_FILE, [])
    return jsonify(data)


@app.route("/api/performance")
def get_performance():
    """Trade journal stats (win rate, PnL, drawdown, strategy breakdown)."""
    stats = _read_json(STATS_FILE, {
        "total_trades": 0, "wins": 0, "losses": 0,
        "total_pnl": 0.0, "max_drawdown": 0.0, "strategy_stats": {},
    })
    total = stats.get("total_trades", 0)
    wins = stats.get("wins", 0)
    strat_stats = stats.get("strategy_stats", {})

    # Find best/worst strategy
    best = max(strat_stats, key=lambda s: strat_stats[s]["pnl"]) if strat_stats else "N/A"
    worst = min(strat_stats, key=lambda s: strat_stats[s]["pnl"]) if strat_stats else "N/A"

    return jsonify({
        "total_trades": total,
        "wins": wins,
        "losses": stats.get("losses", 0),
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        "total_pnl": round(stats.get("total_pnl", 0), 2),
        "max_drawdown": round(stats.get("max_drawdown", 0) * 100, 1),
        "best_strategy": best,
        "worst_strategy": worst,
        "strategy_stats": strat_stats,
    })


@app.route("/api/trades")
def get_trades():
    """Recent trades from the JSONL trade journal."""
    trades = []
    if TRADES_FILE.exists():
        try:
            lines = TRADES_FILE.read_text().strip().split("\n")
            # Return last 50 trades, newest first
            for line in reversed(lines[-50:]):
                if line.strip():
                    trades.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass
    return jsonify(trades)


@app.route("/api/logs")
def get_logs():
    """Last N lines from the bot log file."""
    lines_count = request.args.get("lines", 50, type=int)
    lines_count = min(lines_count, 200)
    lines = []
    if LOG_FILE.exists():
        try:
            from collections import deque
            with open(LOG_FILE) as f:
                lines = list(deque(f, maxlen=lines_count))
            lines = [line.rstrip("\n") for line in lines]
        except OSError:
            pass
    return jsonify(lines)


@app.route("/api/bot/status")
def get_bot_status():
    """Check if the bot process is running (systemd or PID)."""
    import subprocess
    running = False
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "yanagiba"],
            capture_output=True, text=True, timeout=5,
        )
        running = result.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return jsonify({"running": running})


if __name__ == "__main__":
    port = int(os.environ.get("YANAGIBA_UI_PORT", 5000))
    print("\n  Yanagiba Trading Bot Dashboard")
    print(f"  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
