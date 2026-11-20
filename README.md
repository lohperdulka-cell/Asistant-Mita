# Мита — личный Telegram-ассистент

Telegram-бот с inline-режимом, онбордингом под цели, заметками, напоминаниями,
RAG-поиском по загруженным материалам, трекером привычек и Telegram Mini App
в тёмно-лаймовой теме. Рассчитан на запуск на Android-телефоне через Termux
(без root).

## Что умеет

| Модуль | Возможности |
|---|---|
| Онбординг | цель, функции, стиль общения, часовой пояс и время сводки |
| Inline-режим | `@bot_username запрос` в любом чате, с учётом профиля |
| Заметки | `/note`, `/notes`, `/done` |
| Напоминания | `/remind через 10 минут …`, APScheduler, ежедневная сводка |
| Материалы (RAG) | PDF/DOCX/TXT/MD, текст, ссылки; `/upload`, `/materials`, `/forget` |
| Привычки | `/habit add`, отметки в сводке, `/habits` |
| Mini App | заметки, напоминания, материалы, стиль — тёмно-лаймовая тема |

## Структура

```
assistant_bot/
├── bot.py              # точка входа, роутеры, меню команд
├── config.py           # настройки из переменных окружения
├── database.py         # SQLite (aiosqlite): схема и миграции
├── onboarding.py       # FSM-онбординг и /settings
├── inline_mode.py      # inline-режим
├── notes.py            # заметки, напоминания, сводка
├── timeparse.py        # парсинг «через 10 минут», «завтра в 9:00»
├── materials.py        # приём файлов/текста/ссылок
├── extractors.py       # PDF/DOCX/TXT/HTML → текст (с fallback)
├── rag.py              # чанки, поиск, fallback-движки
├── llm_client.py       # клиент LLM (OpenAI-совместимый)
├── scheduler.py        # APScheduler: напоминания и сводки
├── habits.py           # трекер привычек
├── webapp_menu.py      # главное меню и кнопка Mini App
├── webapp/
│   ├── app.py          # FastAPI: API + статика
│   ├── auth.py         # проверка initData (HMAC-SHA256)
│   ├── run.py          # отдельный запуск (uvicorn)
│   ├── static/style.css
│   ├── static/app.js
│   └── templates/index.html
├── scripts/            # запуск и автозапуск в Termux
├── requirements.txt
└── README.md
```

## Переменные окружения

Все секреты — только через `os.environ` / `.env`, в коде их нет.

| Переменная | Обязательна | Назначение |
|---|---|---|
| `BOT_TOKEN` | да | токен от @BotFather |
| `BOT_USERNAME` | нет | username бота для подсказок inline |
| `LLM_API_KEY` | для ИИ | ключ OpenAI-совместимого API |
| `LLM_BASE_URL` | нет | по умолчанию `https://api.openai.com/v1` |
| `LLM_MODEL` | нет | по умолчанию `gpt-4o-mini` |
| `DATA_DIR` | нет | каталог БД и загрузок (`./data`) |
| `WEBAPP_HOST` / `WEBAPP_PORT` | нет | `127.0.0.1` / `8080` |
| `WEBAPP_PUBLIC_URL` | для Mini App | https-адрес туннеля |
| `RAG_ENGINE` | нет | `auto` / `embeddings` / `tfidf` / `keywords` |
| `MAX_UPLOAD_MB` | нет | лимит файла, по умолчанию 20 |
| `LOG_LEVEL` | нет | `INFO` |

## Настройка бота в @BotFather

1. `/newbot` → получите `BOT_TOKEN`, впишите в `.env`.
2. `/setinline` → выберите бота → задайте плейсхолдер, например
   `Спросите у Миты…`. Без этого шага inline-режим не работает.
3. `/setcommands` (необязательно) — бот сам выставляет команды при старте.
4. `/setmenubutton` — для Mini App нужен https-адрес (см. «Mini App»).

> **TODO (ручной шаг):** `/setinline` в BotFather обязателен и делается
> только руками через интерфейс Telegram — API для этого нет.

## Быстрый старт (десктоп / проверка)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-rag.txt   # опционально: PDF/DOCX + TF-IDF
cp .env.example .env                  # впишите BOT_TOKEN и LLM_API_KEY
python bot.py
```

Mini App отдельно:

```bash
python -m webapp.run          # http://127.0.0.1:8080
```

## Запуск в Termux

### 0. Что нужно заранее

| Что | Где взять | Обязательно |
|---|---|---|
| Токен бота | @BotFather → `/newbot` или `/token` | да |
| Ключ LLM | OpenAI, OpenRouter, Groq, DeepSeek — любой OpenAI-совместимый | нет, но без него бот отвечает офлайн-заглушкой |
| https-адрес для Mini App | `scripts/run_tunnel.sh` (cloudflared) | нет, только для Mini App |

Минимальный набор, чтобы бот просто заработал в личке: **только `BOT_TOKEN`**.
Онбординг, заметки, напоминания, привычки и `/materials` работают без LLM.
LLM нужна для умных ответов, inline-режима и RAG-ответов.

### 0.1 Бесплатная LLM (OpenRouter + Groq)

Ключ OpenAI не обязателен. Бот умеет работать на бесплатных провайдерах
и сам переключается между ними, когда у одного кончился лимит.

**OpenRouter** — openrouter.ai → Sign in → Keys → Create Key.

Ключ **не привязан к модели**: при создании модель не выбирается, потому что
она передаётся в теле каждого запроса (поле `model`). Один ключ работает со
всеми 477 моделями каталога. Модель меняется переменной `OPENROUTER_MODEL`
и применяется после перезапуска бота.

Посмотреть бесплатные модели и выбрать:

```bash
python scripts/models.py                              # список :free моделей
python scripts/models.py use google/gemma-4-31b-it:free   # прописать в .env
python scripts/models.py use openrouter/free           # авто-роутер по free
```

`openrouter/free` — встроенный роутер: сам выбирает свободную бесплатную
модель под каждый запрос. Удобно, когда отдельная модель упирается в лимит.

Модели с суффиксом `:free` не тарифицируются:

```
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=google/gemma-4-31b-it:free
```

Список актуальных бесплатных моделей:

```bash
curl -s https://openrouter.ai/api/v1/models | python -c "
import json,sys
for m in json.load(sys.stdin)['data']:
    if m['id'].endswith(':free'): print(m['id'])
"
```

**Groq** — console.groq.com → API Keys → Create. Бесплатный лимит щедрый,
ответы очень быстрые:

```
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
```

Достаточно одного ключа. Порядок перебора задаёт `LLM_CHAIN`
(по умолчанию `primary,openrouter,groq`). Если провайдер вернул 429/401
или не ответил за `LLM_TIMEOUT`, запрос уходит к следующему, а упавший
получает паузу 60 секунд, чтобы не тратить время на каждый запрос.

Проверить, кто из них живой:

```bash
python scripts/doctor.py
```

### 1. Установка

#### Почему Termux только с F-Droid

В Google Play лежит Termux 0.101 (2020 год). Он собран под старую версию
Android и указывает на мёртвое зеркало пакетов, поэтому `pkg install`
падает с ошибкой `Unable to locate package`. Google запретил обновлять
Termux в Play из-за политики `targetSdkVersion`, так что новая версия
выходит только на F-Droid и GitHub.

Ссылки:

- F-Droid, страница Termux: https://f-droid.org/packages/com.termux/
- Прямая ссылка на APK: https://f-droid.org/repo/com.termux_1002.apk
- Termux:Boot (нужен для автозапуска): https://f-droid.org/packages/com.termux.boot/
- Termux:API (даёт `termux-wake-lock`): https://f-droid.org/packages/com.termux.api/

Порядок:

1. Поставьте **F-Droid** с https://f-droid.org/ (это магазин приложений,
   из него потом ставятся сами Termux-пакеты).
2. Откройте F-Droid, найдите **Termux**, установите.
3. Так же поставьте **Termux:Boot** и **Termux:API**.
4. Если Termux из Play Store уже стоял — **удалите его перед установкой**
   с F-Droid. Две версии конфликтуют за один каталог `/data/data/com.termux`
   и портят друг другу пакеты.

Важно: Termux, Termux:Boot и Termux:API должны быть подписаны одним ключом,
поэтому ставьте все три из F-Droid, а не вперемешку с GitHub-сборками.

```bash
pkg update && pkg upgrade
pkg install python termux-api git
git clone <repo> assistant_bot && cd assistant_bot
bash scripts/install_termux.sh
nano .env        # BOT_TOKEN, LLM_API_KEY
```

`install_termux.sh` создаёт `.venv`, ставит `requirements.txt`, затем
пытается поставить `requirements-rag.txt`. Если RAG-пакеты не встали
(мало места, нет колёс под aarch64), установка не падает — бот переключится
на лёгкий поиск по ключевым словам.

### 2. Ручной запуск

```bash
./scripts/run_bot.sh          # бот, лог в data/logs/bot.log
./scripts/run_webapp.sh       # Mini App, лог в data/logs/webapp.log
```

Оба скрипта берут `termux-wake-lock`, если установлен `termux-api`, и
снимают его при выходе. Без `termux-api` печатают предупреждение и работают
дальше.

### 3. Автозапуск через termux-services

```bash
pkg install termux-services
# перезапустите Termux, чтобы появился $PREFIX/var/service
bash scripts/install_services.sh

sv-enable mita-bot
sv-enable mita-webapp
sv up mita-bot
sv status mita-bot
```

Логи: `data/logs/*.log` и `~/.termux/service/mita-bot/current`.

### 4. Автозапуск после перезагрузки телефона

1. Установите **Termux:Boot** из F-Droid (не из Play Store).
2. Откройте Termux:Boot один раз вручную — иначе он не получит разрешение
   на запуск скриптов.
3. Выполните `bash scripts/install_boot.sh` — создастся
   `~/.termux/boot/00-mita`.
4. Перезагрузите телефон и проверьте `sv status mita-bot`.

### 5. Отключите оптимизацию батареи

Android усыпляет Termux и рвёт long-polling. Сделайте:

- Настройки → Приложения → Termux → Батарея → **Без ограничений**
  (на MIUI/EMUI: «Автозапуск» + «Работа в фоне» + отключить «Экономия энергии»).
- Настройки → Приложения → Termux:Boot → то же самое.
- Держите `termux-wake-lock` включённым (скрипты делают это сами).

## Mini App

Telegram открывает Web App только по **https**. На телефоне это значит:
локальный uvicorn + туннель.

```bash
./scripts/run_webapp.sh      # терминал 1
./scripts/run_tunnel.sh      # терминал 2: cloudflared
```

`run_tunnel.sh` выведет адрес вида `https://xxx.trycloudflare.com`.
Впишите его в `.env`:

```
WEBAPP_PUBLIC_URL=https://xxx.trycloudflare.com
```

Перезапустите бота (`sv restart mita-bot`) — в `/menu` появится кнопка
«Открыть Mini App», а `/app` пришлёт её отдельно.

Проверка вручную: `curl http://127.0.0.1:8080/healthz` → `{"ok":true}`.

Безопасность: каждый API-запрос несёт заголовок `X-Init-Data`, подпись
проверяется HMAC-SHA256 с `BOT_TOKEN` и TTL 24 часа. `user_id` берётся
только из подписанных данных — подставить чужой id в теле запроса нельзя.

## RAG: как это работает и что делать, если тяжело

1. Текст режется на чанки по границам предложений (`CHUNK_SIZE`, `CHUNK_OVERLAP`).
2. Чанки ищутся одним из движков (`RAG_ENGINE`):

| Движок | Что нужно | Размер | Когда выбирать |
|---|---|---|---|
| `embeddings` | `sentence-transformers` + torch | ~500 МБ | нужен кросс-язычный поиск |
| `tfidf` | `scikit-learn` | ~30 МБ | обычный случай |
| `keywords` | ничего | 0 | слабый телефон, нет колёс |

`auto` пробует по порядку `embeddings → tfidf → keywords` и падает вниз,
если пакет не импортируется или индекс не построился.

Важное ограничение: `tfidf` и `keywords` **не кросс-язычные**. Вопрос
по-русски к английскому документу не найдёт совпадений — для этого нужен
движок `embeddings` с мультиязычной моделью.

Найденные фрагменты подставляются в промпт, модель не переобучается —
бот явно пишет об этом в ответе.

## Диагностика

| Симптом | Что проверить |
|---|---|
| `BOT_TOKEN не задан` | `.env` в корне проекта, `nano .env` |
| `Telegram отклонил BOT_TOKEN` | токен скопирован целиком, без пробелов |
| Inline не отвечает | `/setinline` в @BotFather выполнен? |
| PDF: «не установлен pypdf» | `pip install -r requirements-rag.txt` |
| PDF: «нет текстового слоя» | скан без OCR — нужен текстовый PDF |
| Ответы без учёта материалов | `/materials` пуст, или язык вопроса ≠ язык документа при `tfidf` |
| Кнопки Mini App нет | `WEBAPP_PUBLIC_URL` начинается с `https://`? |
| Mini App: 401 | открывайте из Telegram, не по прямой ссылке |
| Бот замолкает через час | оптимизация батареи, см. пункт 5 |
| После перезагрузки не поднялся | Termux:Boot открыт вручную? `sv status mita-bot` |

## Проверка настроек

Если что-то не запускается — сначала запустите диагностику:

```bash
python scripts/doctor.py
```

Скрипт ничего не меняет. Он проверяет: токен (через `getMe`), совпадение
`BOT_USERNAME`, включён ли inline-режим, нет ли конфликтующего webhook,
живой ли ключ LLM (пробный запрос), открывается ли БД, какой движок RAG
и какие парсеры установлены, корректен ли адрес Mini App.

Строки `[ FAIL ]` надо исправить, `[ WARN ]` — по желанию. В конце будет
`Критичных проблем нет` либо `НАДО ИСПРАВИТЬ: N`.

## Тесты

```bash
python tests/test_acceptance.py    # все 8 критериев готовности из ТЗ
python tests/test_notes.py         # заметки, напоминания, сводка
python tests/test_scheduler.py     # доставка и защита от дублей
python tests/test_habits.py        # серии и проценты привычек
python tests/test_webapp.py        # API Mini App и подпись initData
python tests/test_rag_flow.py      # загрузка PDF/DOCX/TXT и RAG-ответ
```

Тесты не требуют сети: LLM и Telegram подменяются на транспорте,
база — настоящий SQLite во временном каталоге.

## Безопасность

- Бот читает **только** то, что пользователь сам ему отправил.
- Файлы телефона не сканируются, доступа к хранилищу нет.
- Приватные заметки не отдаются в inline-режиме другим людям.
- `initData` Mini App проверяется по HMAC-SHA256 с `BOT_TOKEN`.
- Все секреты — в переменных окружения; `.env` в `.gitignore`.
