#!/data/data/com.termux/files/usr/bin/bash
# Установка «Миты» в Termux. Идемпотентна: можно запускать повторно.
set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

echo "== Мита: установка в Termux =="

if ! command -v pkg >/dev/null 2>&1; then
  echo "Этот скрипт рассчитан на Termux (нет команды pkg)." >&2
  exit 1
fi

echo "[1/5] Системные пакеты"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python termux-api >/dev/null || {
  echo "Не удалось установить python/termux-api. Установите вручную:" >&2
  echo "  pkg install python termux-api" >&2
  exit 1
}

echo "[2/5] Виртуальное окружение"
if [ ! -d .venv ]; then
  python -m venv .venv || {
    echo "python -m venv не сработал. Поставьте: pkg install python-venv" >&2
    exit 1
  }
fi
# shellcheck disable=SC1091
. .venv/bin/activate

echo "[3/5] Базовые зависимости"
python -m pip install --upgrade pip >/dev/null 2>&1 || true
python -m pip install -r requirements.txt || exit 1

echo "[4/5] RAG-зависимости (необязательные, с fallback)"
if python -m pip install -r requirements-rag.txt; then
  echo "RAG-пакеты установлены."
else
  echo "Часть RAG-пакетов не встала — бот переключится на лёгкий поиск." >&2
fi

echo "[5/5] Каталоги и .env"
mkdir -p data/uploads data/logs
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Создан .env — впишите BOT_TOKEN и LLM_API_KEY:"
  echo "  nano .env"
fi
chmod +x scripts/*.sh

echo
echo "Готово. Дальше:"
echo "  1) nano .env        — BOT_TOKEN, LLM_API_KEY"
echo "  2) ./scripts/run_bot.sh"
