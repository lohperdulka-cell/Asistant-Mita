#!/data/data/com.termux/files/usr/bin/bash
# Публикует локальный Mini App через cloudflared и подсказывает, что вписать в .env.
# Telegram требует https — без туннеля кнопка Web App не появится.
set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$PROJECT_DIR/.env"
  set +a
fi
PORT="${WEBAPP_PORT:-8080}"

if ! command -v cloudflared >/dev/null 2>&1; then
  cat >&2 <<'EOF'
cloudflared не установлен. Варианты:
  pkg install cloudflared            # если пакет доступен
  # или скачайте бинарник под aarch64:
  curl -L -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
  chmod +x cloudflared && mv cloudflared "$PREFIX/bin/"
EOF
  exit 1
fi

echo "Туннель на http://127.0.0.1:$PORT"
echo "Скопируйте выданный https://…trycloudflare.com в .env как WEBAPP_PUBLIC_URL"
echo
exec cloudflared tunnel --url "http://127.0.0.1:$PORT"
