#!/data/data/com.termux/files/usr/bin/bash
# Запуск Mini App (FastAPI/uvicorn) в Termux.
set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

VENV="$PROJECT_DIR/.venv"
PYTHON="$VENV/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python || command -v python3)"
fi

LOG_DIR="$PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$PROJECT_DIR/.env"
  set +a
fi

WAKE_LOCK=0
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock && WAKE_LOCK=1
fi

cleanup() {
  if [ "$WAKE_LOCK" = "1" ]; then
    termux-wake-unlock || true
  fi
}
trap cleanup EXIT INT TERM

echo "[$(date '+%F %T')] старт webapp" >> "$LOG_DIR/webapp.log"
exec "$PYTHON" -u -m webapp.run >> "$LOG_DIR/webapp.log" 2>&1
