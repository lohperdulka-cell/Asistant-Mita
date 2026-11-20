#!/data/data/com.termux/files/usr/bin/bash
# Регистрирует бота и Mini App как сервисы termux-services (автозапуск).
set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
SERVICE_DIR="$PREFIX/var/service"

if ! command -v sv >/dev/null 2>&1; then
  echo "termux-services не установлен. Выполните:" >&2
  echo "  pkg install termux-services" >&2
  echo "  # затем перезапустите Termux" >&2
  exit 1
fi

if [ ! -d "$SERVICE_DIR" ]; then
  echo "Каталог $SERVICE_DIR не найден. Запустите Termux заново после установки termux-services." >&2
  exit 1
fi

install_service() {
  local name="$1"
  local script="$2"
  local dir="$SERVICE_DIR/$name"

  mkdir -p "$dir"
  cat > "$dir/run" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
exec $PROJECT_DIR/scripts/$script
EOF
  chmod +x "$dir/run"
  echo "сервис $name -> $dir/run"
}

install_service "mita-bot" "run_bot.sh"
install_service "mita-webapp" "run_webapp.sh"

echo
echo "Сервисы созданы. Управление:"
echo "  sv-enable mita-bot      # автозапуск при старте Termux"
echo "  sv-enable mita-webapp"
echo "  sv up mita-bot          # запустить сейчас"
echo "  sv status mita-bot      # состояние"
echo "  sv down mita-bot        # остановить"
echo "  tail -f ~/.termux/service/mita-bot/current  # логи sv"
