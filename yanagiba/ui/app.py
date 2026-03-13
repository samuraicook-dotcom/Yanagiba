"""Yanagiba Trading Bot — Web Dashboard & Configuration UI.

Run with:  python -m yanagiba.ui.app
"""

from __future__ import annotations

import json
import os
import time
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
    valid_fields = {f.name for f in fields(TradingConfig)}
    filtered = {}
    for k, v in data.items():
        if k not in valid_fields:
            continue
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
            all_lines = LOG_FILE.read_text().strip().split("\n")
            lines = all_lines[-lines_count:]
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


# --- Connect Four Multiplayer Game ---

# In-memory game state (shared between all connected players)
c4_game = {
    "board": [[0] * 7 for _ in range(6)],  # 6 rows x 7 cols, 0=empty 1=red 2=yellow
    "current_player": 1,
    "winner": 0,  # 0=none, 1=red wins, 2=yellow wins, 3=draw
    "last_move": None,
    "move_count": 0,
    "players": {},  # player_id -> player number (1 or 2)
    "updated_at": time.time(),
}


def _c4_check_winner(board, row, col, player):
    """Check if the last move at (row, col) created a 4-in-a-row."""
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for dr, dc in directions:
        count = 1
        for sign in [1, -1]:
            r, c = row + dr * sign, col + dc * sign
            while 0 <= r < 6 and 0 <= c < 7 and board[r][c] == player:
                count += 1
                r += dr * sign
                c += dc * sign
        if count >= 4:
            return True
    return False


@app.route("/api/game/state")
def c4_state():
    """Return current game state for polling."""
    return jsonify({
        "board": c4_game["board"],
        "current_player": c4_game["current_player"],
        "winner": c4_game["winner"],
        "last_move": c4_game["last_move"],
        "move_count": c4_game["move_count"],
        "players": len(c4_game["players"]),
        "updated_at": c4_game["updated_at"],
    })


@app.route("/api/game/join", methods=["POST"])
def c4_join():
    """Join the game. Assigns player 1 (red) or player 2 (yellow)."""
    data = request.get_json(force=True)
    player_id = data.get("player_id", "")

    if player_id in c4_game["players"]:
        return jsonify({"player": c4_game["players"][player_id]})

    assigned = list(c4_game["players"].values())
    if 1 not in assigned:
        c4_game["players"][player_id] = 1
    elif 2 not in assigned:
        c4_game["players"][player_id] = 2
    else:
        return jsonify({"player": 0, "error": "Game is full"})

    return jsonify({"player": c4_game["players"][player_id]})


@app.route("/api/game/move", methods=["POST"])
def c4_move():
    """Drop a piece in a column."""
    data = request.get_json(force=True)
    player_id = data.get("player_id", "")
    col = data.get("col", -1)

    if player_id not in c4_game["players"]:
        return jsonify({"error": "Not in game"}), 400

    player = c4_game["players"][player_id]
    if player != c4_game["current_player"]:
        return jsonify({"error": "Not your turn"}), 400

    if c4_game["winner"] != 0:
        return jsonify({"error": "Game is over"}), 400

    if not (0 <= col < 7):
        return jsonify({"error": "Invalid column"}), 400

    # Find lowest empty row
    board = c4_game["board"]
    row = -1
    for r in range(5, -1, -1):
        if board[r][col] == 0:
            row = r
            break

    if row == -1:
        return jsonify({"error": "Column is full"}), 400

    board[row][col] = player
    c4_game["move_count"] += 1
    c4_game["last_move"] = {"row": row, "col": col, "player": player}
    c4_game["updated_at"] = time.time()

    if _c4_check_winner(board, row, col, player):
        c4_game["winner"] = player
    elif c4_game["move_count"] >= 42:
        c4_game["winner"] = 3  # draw
    else:
        c4_game["current_player"] = 2 if player == 1 else 1

    return jsonify({"ok": True})


@app.route("/api/game/reset", methods=["POST"])
def c4_reset():
    """Reset the game board."""
    c4_game["board"] = [[0] * 7 for _ in range(6)]
    c4_game["current_player"] = 1
    c4_game["winner"] = 0
    c4_game["last_move"] = None
    c4_game["move_count"] = 0
    c4_game["players"] = {}
    c4_game["updated_at"] = time.time()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("YANAGIBA_UI_PORT", 5000))
    print("\n  Yanagiba Trading Bot Dashboard")
    print(f"  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=True)
