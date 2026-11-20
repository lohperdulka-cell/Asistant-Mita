#!/data/data/com.termux/files/usr/bin/bash
# Запуск бота «Мита» в Termux. Держит wake-lock, чтобы Android не усыпил процесс.
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

if [ -z "${BOT_TOKEN:-}" ]; then
  echo "BOT_TOKEN не задан. Впишите его в $PROJECT_DIR/.env" >&2
  exit 1
fi

WAKE_LOCK=0
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock && WAKE_LOCK=1
else
  echo "termux-wake-lock не найден (pkg install termux-api). Продолжаю без него." >&2
fi

cleanup() {
  if [ "$WAKE_LOCK" = "1" ]; then
    termux-wake-unlock || true
  fi
}
trap cleanup EXIT INT TERM

echo "[$(date '+%F %T')] старт бота" >> "$LOG_DIR/bot.log"
exec "$PYTHON" -u bot.py >> "$LOG_DIR/bot.log" 2>&1
