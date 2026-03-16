#!/usr/bin/env bash
#
# yanagiba.sh — Start/stop/restart Yanagiba trading bot on macOS
#
# Usage:
#   ./yanagiba.sh start [--live] [--exchange binance|bybit] [--interval 60]
#   ./yanagiba.sh stop
#   ./yanagiba.sh restart [args...]
#   ./yanagiba.sh status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$HOME/.yanagiba"
PID_FILE="$PID_DIR/yanagiba.pid"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/yanagiba.log"
ENV_FILE="$SCRIPT_DIR/.env"
STOP_TIMEOUT=30

mkdir -p "$PID_DIR" "$LOG_DIR"

is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        # Stale PID file
        rm -f "$PID_FILE"
    fi
    return 1
}

do_start() {
    if is_running; then
        echo "Yanagiba is already running (PID $(cat "$PID_FILE"))"
        exit 1
    fi

    # Source environment variables (API keys, etc.)
    if [ -f "$ENV_FILE" ]; then
        set -a
        # shellcheck disable=SC1090
        source "$ENV_FILE"
        set +a
    fi

    echo "Starting Yanagiba..."
    cd "$SCRIPT_DIR"
    nohup python3 -m yanagiba.main "$@" >> "$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    echo "Yanagiba started (PID $pid)"
    echo "Logs: $LOG_FILE"
}

do_stop() {
    if ! is_running; then
        echo "Yanagiba is not running"
        return 0
    fi

    local pid
    pid=$(cat "$PID_FILE")
    echo "Stopping Yanagiba (PID $pid)..."
    kill -TERM "$pid"

    # Wait for graceful shutdown
    local elapsed=0
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$elapsed" -ge "$STOP_TIMEOUT" ]; then
            echo "Graceful shutdown timed out, force-killing..."
            kill -9 "$pid" 2>/dev/null || true
            break
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    rm -f "$PID_FILE"
    echo "Yanagiba stopped"
}

do_restart() {
    do_stop
    sleep 1
    do_start "$@"
}

do_status() {
    if is_running; then
        echo "Yanagiba is running (PID $(cat "$PID_FILE"))"
    else
        echo "Yanagiba is not running"
    fi
}

case "${1:-}" in
    start)
        shift
        do_start "$@"
        ;;
    stop)
        do_stop
        ;;
    restart)
        shift
        do_restart "$@"
        ;;
    status)
        do_status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status} [--live] [--exchange binance|bybit] [--interval N]"
        exit 1
        ;;
esac
