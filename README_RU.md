# TokenLeak

Сканер git-репозиториев на основе ИИ-агента. Обнаруживает утечки секретов,
API-токенов, паролей, персональных данных и корпоративной информации во всей истории
коммитов.

---

## Возможности

- **Умная стратегия сканирования** — полный скан HEAD при первом запуске, инкрементальный diff при повторных
- **ИИ-агент с двумя проходами** — сначала строит карту рисков, затем детальный анализ файлов
- **Diff-сканирование** — быстрый анализ только изменённых строк, экономия токенов
- **OCR-анализ изображений** — опциональная vision-модель для скриншотов и Jupyter-нотбуков
- **Префильтр** — энтропийный анализ и 25+ regex-паттернов для снижения расхода токенов
- **Множество провайдеров** — GitHub, GitLab (self-hosted), Gitea/Forgejo, произвольные git URL
- **OpenAI или Ollama** — выбор AI-бэкенда с поддержкой пользовательского URL
- **SQLite или PostgreSQL** — из коробки без настройки или enterprise-конфигурация
- **Уведомления в Mattermost** — опциональные алерты в реальном времени
- **Блокировка процесса** — безопасен для cron, конкурентные запуски предотвращаются автоматически
- **Защита от больших репозиториев** — настраиваемый лимит размера с логированием и уведомлениями
- **Защита от исчерпания баланса API** — немедленная остановка при ошибке оплаты с понятным сообщением
- **Безопасное клонирование** — хуки отключены, права на исполнение сняты, временные данные удаляются
- **Кросс-платформенность** — Linux, macOS, Windows (Python 3.11+)

## Быстрый старт

```bash
# 1. Клонирование
git clone https://github.com/your-org/TokenLeak.git
cd TokenLeak

# 2. Установка только зависимостей (сборка пакета не нужна)
pip install -r requirements.txt

# 3. Настройка
cp .env.example .env
# Установить TOKENLEAK_AI_API_KEY, TOKENLEAK_AI_MODEL и т.д.

# 4. Проверка статуса
python tokenleak.py status

# 5. Сканирование репозитория
python tokenleak.py scan https://github.com/user/repo.git

# 6. Сканирование списка репозиториев
echo "https://github.com/user/repo1.git" > repos.txt
echo "github:my-org-name" >> repos.txt
python tokenleak.py scan
```

## Установка

**Рекомендуемый способ — только зависимости, без сборки пакета:**
```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python tokenleak.py --version
```

**С поддержкой PostgreSQL:**
```bash
pip install -r requirements.txt psycopg2-binary
```

## Использование

```
python -m tokenleak scan    [TARGET ...] [--sha SHA] [--report [FILE]]
                                         [--no-prefilter] [--noanimation]
python -m tokenleak rescan  [TARGET ...] [--sha SHA] [--report [FILE]]
                                         [--no-prefilter] [--noanimation]
python -m tokenleak status
python -m tokenleak mcp                  # запуск MCP-сервера через stdio
```

### Стратегия сканирования

| Команда | Поведение |
|---------|-----------|
| `scan` (первый запуск для репозитория) | Полный скан HEAD, затем diff-скан всей истории |
| `scan` (повторные запуски) | Diff-скан только новых коммитов |
| `rescan` | Всегда как первый запуск — полный HEAD + вся история |
| `scan --sha X` | Diff-скан конкретного коммита |
| `rescan --sha X` | Полный скан на указанном коммите |

### Форматы целей

| Спецификатор | Описание |
|---|---|
| `https://github.com/user/repo.git` | Один репозиторий |
| `github:username` | Все репозитории GitHub-пользователя |
| `gitlab:username` | Все репозитории на настроенном GitLab |
| `gitlab:https://host:username` | Все репозитории на конкретном GitLab |
| `gitea:username` | Все репозитории на настроенном Gitea |
| `server:https://gitlab.host` | Все репозитории на GitLab-сервере |

### Примеры

```bash
# Diff-скан конкретного коммита
python -m tokenleak scan https://github.com/user/repo.git --sha abc123

# Полный скан на конкретном коммите
python -m tokenleak rescan https://github.com/user/repo.git --sha abc123

# Принудительное пересканирование + сохранение отчёта в markdown
python -m tokenleak rescan github:my-org --report report.md

# Отключение префильтра (ИИ видит всё содержимое)
python -m tokenleak scan https://github.com/user/repo.git --no-prefilter

# Без анимации (для cron/CI)
python -m tokenleak scan --noanimation
```

## Конфигурация

Все настройки через переменные окружения или файл `.env`. Скопируйте `.env.example` в `.env`.

### Основные параметры

| Переменная | По умолчанию | Описание |
|---|---|---|
| `TOKENLEAK_AI_PROVIDER` | `openai` | `openai` или `ollama` |
| `TOKENLEAK_AI_API_KEY` | — | API-ключ (OpenAI) |
| `TOKENLEAK_AI_API_URL` | — | Пользовательский base URL |
| `TOKENLEAK_AI_MODEL` | `gpt-4o` | Название модели |
| `TOKENLEAK_OCR_MODEL` | — | Vision-модель для OCR изображений и нотбуков (опционально) |
| `TOKENLEAK_DB_TYPE` | `sqlite` | `sqlite` или `postgres` |
| `TOKENLEAK_PREFILTER_ENABLED` | `true` | Отключить через `false` или `--no-prefilter` |
| `TOKENLEAK_MAX_REPO_SIZE_MB` | `2048` | Пропускать репозитории больше указанного размера |
| `TOKENLEAK_REPOS_LIST_PATH` | `repos.txt` | Список целей для сканирования |
| `TOKENLEAK_MATTERMOST_URL` | — | URL Mattermost-сервера |
| `TOKENLEAK_MATTERMOST_TOKEN` | — | Personal access token Mattermost |

Полный список — в `.env.example`.

### Использование с Ollama

```bash
TOKENLEAK_AI_PROVIDER=ollama
TOKENLEAK_AI_API_URL=http://localhost:11434/v1
TOKENLEAK_AI_MODEL=llama3.1:70b
```

## OCR-анализ изображений

Если задана переменная `TOKENLEAK_OCR_MODEL`, TokenLeak использует отдельную
vision-capable модель для анализа изображений на наличие чувствительных данных:

```bash
TOKENLEAK_OCR_MODEL=gpt-4o           # или любая другая vision-модель
```

**Что анализируется:**
- Файлы изображений: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`
- Изображения, встроенные в выходные данные ячеек Jupyter-нотбуков (`.ipynb`)

**Что ищет модель:**
- API-ключи, токены, пароли на скриншотах терминалов и дашбордов
- Персональные данные (имена, email, номера документов)
- Внутренние URL, IP-адреса, строки подключения к БД на скриншотах

Если `TOKENLEAK_OCR_MODEL` не задан, изображения молча пропускаются.

## База данных

**SQLite** (по умолчанию) — не требует настройки, идеален для одного хоста.

**PostgreSQL** — см. [docs/postgresql_setup.md](docs/postgresql_setup.md): инструкция по
настройке с минимальными привилегиями и отключением exec-функций СУБД.

## Префильтр

Префильтр проверяет файлы локально перед отправкой ИИ:

- **Энтропийный анализ** — Shannon entropy > 4.5 для строк длиной ≥ 20 символов
- **Regex-паттерны** — AWS-ключи, токены GitHub/GitLab, JWT, приватные ключи, пароли,
  строки подключения к БД, Stripe, Twilio, Slack, Google API и другие
- **Подозрительные имена файлов** — `.env`, `id_rsa`, `*.pem`, `*.key`, `.htpasswd` и т.д.

**Отключение** префильтра (ИИ проверяет всё, дороже, но полнее):
```bash
TOKENLEAK_PREFILTER_ENABLED=false
# или для одного запуска:
python -m tokenleak scan --no-prefilter
```

## Развёртывание

Полная инструкция — [docs/deployment.md](docs/deployment.md): создание системного
пользователя `tokenleak`, настройка окружения, проверка установки.

Конфигурация cron и systemd timer — [docs/cron_setup.md](docs/cron_setup.md).

## Безопасность

- Клонированные репозитории считаются потенциально опасными (предполагается наличие ВПО)
- Git-хуки удаляются сразу после клонирования
- Права на исполнение снимаются с рабочего дерева
- Директория клона удаляется после каждого сканирования (даже при ошибке)
- Приложение работает под непривилегированным пользователем
- Агент получает инструкцию никогда не использовать и не проверять найденные учётные данные

## Команда status

```bash
python -m tokenleak status
```

Выводит сводную таблицу из БД:
- Количество репозиториев
- Статусы сканирований (выполнено / ошибка / пропущено)
- Общее число алертов
- Потраченные токены
- Время последнего завершённого сканирования

## Запуск тестов

```bash
pip install ".[dev]"
pytest tests/ -v
```

## Документация

| Файл | Содержание |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Архитектура компонентов и поток данных |
| [docs/workflow.md](docs/workflow.md) | Команды, форматы целей, флаги |
| [docs/postgresql_setup.md](docs/postgresql_setup.md) | Настройка PostgreSQL с hardening |
| [docs/deployment.md](docs/deployment.md) | Пошаговое развёртывание в production |
| [docs/cron_setup.md](docs/cron_setup.md) | Конфигурация cron и systemd timer |
| [agent.md](agent.md) | Инструкции ИИ-агента (можно настраивать) |

## Лицензия

MIT
