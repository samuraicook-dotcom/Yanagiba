# CLAUDE.md — AI Assistant Guide for Yanagiba

## Project Overview

Yanagiba is an institutional-grade quantitative crypto trading bot with a multi-agent architecture.

## Getting Started

- **Language:** Python 3.11+
- **Install:** `pip install -e .` or `pip install -e ".[dev]"`
- **Run (paper trading):** `python -m yanagiba.main`
- **Run (live):** `python -m yanagiba.main --live`
- **Options:** `--exchange binance|bybit` `--interval 60`
- **Lint:** `ruff check yanagiba/`

## Architecture

4-agent pipeline — every trade passes through all agents sequentially:

1. **Market Analyst** — regime detection (trending/ranging/volatile), sentiment scoring, geopolitical risk
2. **Strategy Engine** — signal generation (momentum, mean reversion, scalping, liquidity sweeps)
3. **Risk Manager** — approval gate (R:R, exposure, confidence thresholds)
4. **Execution Engine** — order placement (limit entries, stop-market SL, partial TPs)

## Project Structure

```
yanagiba/
├── main.py                    # Orchestrator + CLI entry point
├── models/
│   ├── types.py               # Core data types (signals, regimes, orders)
│   └── config.py              # Trading configuration
├── data/
│   ├── market_data.py         # ccxt exchange data provider
│   └── sentiment.py           # Geopolitical & news sentiment tracker
├── indicators/
│   └── technical.py           # RSI, MACD, VWAP, BB, EMA, ATR, volume delta
├── agents/
│   ├── market_analyst.py      # Agent 1: Market regime + sentiment
│   ├── strategy_engine.py     # Agent 2: Signal generation
│   ├── risk_manager.py        # Agent 3: Risk gate
│   └── execution_engine.py    # Agent 4: Order execution
```

## Risk Rules

- Max 1% risk per trade
- Max 10% portfolio exposure
- Max 3x leverage
- 3% daily loss limit
- Scalp: 0.5% SL, 1-2% TP

## Code Conventions

- Python 3.11+, type hints, dataclasses
- Async-first (ccxt async, aiohttp)
- All outputs structured as JSON-serializable dicts
- Ruff for linting

## Key Decisions

- **ccxt** for exchange abstraction (supports 100+ exchanges, spot + futures)
- **Sandbox mode by default** — must explicitly opt into live trading
- **Geopolitical tracking** via free APIs (Fear & Greed, CoinGecko trending, CryptoPanic)
- **All trading pairs available** — configurable via TradingConfig.assets
- **Futures supported** — ccxt handles both spot and futures markets
