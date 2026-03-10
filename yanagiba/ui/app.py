"""Yanagiba Trading Bot — Web Configuration UI.

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


if __name__ == "__main__":
    port = int(os.environ.get("YANAGIBA_UI_PORT", 5000))
    print("\n  Yanagiba Trading Bot UI")
    print(f"  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=True)
