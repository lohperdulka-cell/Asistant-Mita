#!/data/data/com.termux/files/usr/bin/bash
# Ставится в ~/.termux/boot/ — Termux:Boot запускает это при загрузке телефона.
# Требуется приложение Termux:Boot из F-Droid и один ручной запуск после установки.
set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BOOT_DIR="$HOME/.termux/boot"

mkdir -p "$BOOT_DIR"

cat > "$BOOT_DIR/00-mita" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd $PROJECT_DIR || exit 1
sv up mita-webapp
sv up mita-bot
EOF
chmod +x "$BOOT_DIR/00-mita"

echo "Создан $BOOT_DIR/00-mita"
echo
echo "Проверка:"
echo "  ls -l $BOOT_DIR"
echo
echo "Важно: Termux:Boot запускает скрипты только если приложение Termux:Boot"
echo "хотя бы раз открыто вручную после установки, а Termux исключён из"
echo "оптимизации батареи (см. README)."
