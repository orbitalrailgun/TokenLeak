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
- **Сравнение нескольких моделей** — сканирование одного репозитория разными моделями в единой БД; результаты изолированы по полю `ai_model`
- **Устойчивость к переполнению контекста** — при превышении контекстного окна агент корректно останавливается, сохраняя все накопленные алерты
- **Множество провайдеров** — GitHub, GitLab (self-hosted), Gitea/Forgejo, произвольные git URL
- **OpenAI или Ollama** — выбор AI-бэкенда с поддержкой пользовательского URL
- **SQLite или PostgreSQL** — из коробки без настройки или enterprise-конфигурация
- **Уведомления в Mattermost** — алерты в реальном времени + CSV-файл с полным отчётом после завершения скана репозитория
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
python -m tokenleak alerts_export        # выгрузка алертов в CSV
                            [--output FILE]   [--repo URL]
                            [--scan-id ID]    [--ai-model MODEL]
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
| `TOKENLEAK_SCAN_ALL_BRANCHES` | `true` | Полный скан каждого удалённого бранча; отключить для экономии токенов |
| `TOKENLEAK_MAX_REPO_SIZE_MB` | `2048` | Пропускать репозитории больше указанного размера |
| `TOKENLEAK_REPOS_LIST_PATH` | `repos.txt` | Список целей для сканирования |
| `TOKENLEAK_MATTERMOST_URL` | — | URL Mattermost-сервера |
| `TOKENLEAK_MATTERMOST_TOKEN` | — | Personal access token Mattermost |
| `TOKENLEAK_MATTERMOST_CHANNEL` | `tokenleak-alerts` | Имя канала для текстовых уведомлений |
| `TOKENLEAK_MATTERMOST_CHANNEL_ID` | — | ID канала для загрузки CSV-файлов (обязателен для прикреплений) |

Полный список — в `.env.example`.

### Уведомления Mattermost

После каждого скана коммита отправляется текстовый summary в канал.
После завершения сканирования **всего репозитория** загружается CSV-файл со всеми алертами
(требует `TOKENLEAK_MATTERMOST_CHANNEL_ID`).

Channel ID можно узнать через Mattermost: **Информация о канале → ID канала**
или через API: `GET /api/v4/channels/name/{team_name}/{channel_name}`.

Подробнее — в [docs/mattermost_setup.md](docs/mattermost_setup.md).

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

## Команда alerts_export

```bash
# Все алерты — вывод в stdout
python -m tokenleak alerts_export

# Сохранить в файл
python -m tokenleak alerts_export --output alerts.csv

# Алерты конкретного репозитория
python -m tokenleak alerts_export --repo https://github.com/user/repo.git --output repo.csv

# Алерты конкретного скана
python -m tokenleak alerts_export --scan-id 42 --output scan42.csv

# Алерты репозитория, отфильтрованные по модели
python -m tokenleak alerts_export --repo https://github.com/user/repo.git --ai-model gpt-4o
```

CSV-файл включает все поля, необходимые для анализа:
`alert_id`, `repo_url`, `repo_provider`, `repo_name`, `commit_sha`, `commit_date`,
`commit_message`, `commit_author`, `branch`, `scan_mode`, `scan_status`, `ai_model`,
`input_tokens`, `output_tokens`, `tokens_used`, `scan_error`, `file_path`,
`line_start`, `line_end`, `alert_type`, `severity`, `description`, `code_snippet`,
`how_used`, `confirmation`, `is_false_positive`, `triggered_by`, `alert_created_at`.

Файл кодируется в UTF-8 с BOM — открывается в Excel без дополнительных настроек.
Синтетические номера строк для бинарных файлов отображаются как пустые ячейки.

## Запуск тестов

```bash
pip install ".[dev]"
pytest tests/ -v
```

## Документация

| Файл | Содержание |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Архитектура компонентов и поток данных |
| [docs/flow.md](docs/flow.md) | Полный поток выполнения: диспетчер команд, стратегия, префильтр, взаимодействие агента и MCP |
| [docs/workflow.md](docs/workflow.md) | Команды, форматы целей, флаги |
| [docs/model_comparison.md](docs/model_comparison.md) | Сравнение нескольких моделей в единой БД |
| [docs/postgresql_setup.md](docs/postgresql_setup.md) | Настройка PostgreSQL с hardening |
| [docs/prefilter.md](docs/prefilter.md) | Префильтр: исключения, regex-паттерны, энтропия, подавление плейсхолдеров |
| [docs/mattermost_setup.md](docs/mattermost_setup.md) | Интеграция с Mattermost: бот, channel ID, примеры уведомлений |
| [docs/deployment.md](docs/deployment.md) | Пошаговое развёртывание в production |
| [docs/cron_setup.md](docs/cron_setup.md) | Конфигурация cron и systemd timer |
| [agent.md](agent.md) | Инструкции ИИ-агента (можно настраивать) |

## Лицензия

MIT
