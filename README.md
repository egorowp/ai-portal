# AI Portal

Бесплатный русскоязычный портал об искусственном интеллекте. Архитектура первой версии: static-first, Git-as-database, Python agent, Astro и GitHub Actions.

## Что уже работает

- Сбор материалов из Hacker News, arXiv, GitHub и Hugging Face.
- Нормализация и дедупликация по URL.
- Сохранение карточек источников в `data/inbox/`.
- Генерация трёх уровней материала через единый AI Provider Manager: Gemini, Groq, OpenRouter и Cloudflare Workers AI.
- Без API-ключа материалы остаются в `review`, а не публикуются автоматически.
- Astro отображает материалы из `data/news/` и страницы разделов.
- GitHub Actions запускает сборщик по расписанию и собирает сайт.

## 1. Создать GitHub-репозиторий

1. Откройте https://github.com/new
2. Repository name: `ai-portal`
3. Выберите **Public** (GitHub Actions для публичного репозитория удобен для бесплатного MVP).
4. Не добавляйте README, `.gitignore` и License — они уже есть в проекте.
5. Создайте репозиторий.

## 2. Загрузить проект

Распакуйте архив, откройте терминал в папке `ai-portal` и выполните:

```bash
git init
git add .
git commit -m "Initial AI Portal scaffold"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/ai-portal.git
git push -u origin main
```

Замените `YOUR_GITHUB_USERNAME` на свой GitHub username.

## 3. Настроить AI-провайдеры

Агент использует OpenAI-compatible Chat Completions API. Провайдеры вызываются в порядке `AI_PROVIDER_ORDER`; при временной ошибке, недоступности или некорректном JSON ответе агент пробует следующий провайдер.

Добавьте ключи в `Settings → Secrets and variables → Actions → Secrets`:

- `GEMINI_API_KEY` — Google AI Studio.
- `GROQ_API_KEY` — Groq.
- `OPENROUTER_API_KEY` — OpenRouter.
- `CLOUDFLARE_API_TOKEN` — Cloudflare API token с доступом Workers AI.
- `CLOUDFLARE_ACCOUNT_ID` — Cloudflare Account ID (Secret или Actions Variable).

Необязательные настройки в `Settings → Secrets and variables → Actions → Variables`:

| Variable | Default |
|---|---|
| `AI_PROVIDER_ORDER` | `gemini,groq,openrouter,cloudflare` |
| `GEMINI_MODEL` | `gemini-2.5-flash` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `OPENROUTER_MODEL` | `openrouter/free` |
| `CLOUDFLARE_MODEL` | `@cf/meta/llama-3.1-8b-instruct` |
| `AI_MAX_GENERATIONS_PER_RUN` | `6` |
| `AI_MAX_RETRIES` | `1` |
| `AI_TIMEOUT_SECONDS` | `90` |
| `AI_GEMINI_MAX_REQUESTS` | `12` |
| `AI_GROQ_MAX_REQUESTS` | `12` |
| `AI_OPENROUTER_MAX_REQUESTS` | `12` |
| `AI_CLOUDFLARE_MAX_REQUESTS` | `12` |

Если Variable не создана, код использует значение по умолчанию. Достаточно настроить только один API-ключ; остальные провайдеры без ключей автоматически пропускаются.

**Важно о бесплатности:** бесплатные тарифы, лимиты и доступность моделей меняются. OpenRouter `openrouter/free` маршрутизирует запросы только на бесплатные модели; для остальных провайдеров проверьте стоимость выбранной модели и условия аккаунта. Наличие API-ключа само по себе не гарантирует нулевую стоимость. Установите бюджет/ограничения у провайдера, если они доступны.

Агент повторно обрабатывает элементы `data/inbox/` после наступления `retry_after`. Число попыток ограничено `INBOX_MAX_ATTEMPTS` (по умолчанию 8). Неуспешные материалы не публикуются автоматически и остаются на проверке.

Важно: передавайте моделям только общедоступные материалы, которые допустимо отправлять стороннему API. Не отправляйте приватные данные.

## 4. Локальный запуск агента

Требуется Python 3.11+.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

python -m agent.main
```

Без настроенных AI API-ключей агент соберёт источники в `data/inbox/`.
Если настроен хотя бы один ключ, агент попробует сформировать три редакции и сохранит результат в `data/news/` со статусом `review` — автоматическая публикация выключена.

## 5. Локальный запуск сайта

Требуется Node.js 20+.

```bash
cd site
npm install
npm run dev
```

Откройте адрес, который покажет Astro (обычно http://localhost:4321).

Сборка:

```bash
npm run build
```

## 6. Включить GitHub Actions

После push откройте вкладку `Actions` репозитория и разрешите запуск workflows, если GitHub попросит подтверждение.

- `agent.yml` — ежедневно собирает новые материалы и коммитит результат.
- `pages.yml` — собирает Astro и публикует сайт в GitHub Pages.

В настройках репозитория: `Settings → Pages → Build and deployment → Source: GitHub Actions`.

В `Settings → Secrets and variables → Actions` добавьте ключи нужных AI-провайдеров (см. раздел 3).
Расписание workflow задано в UTC. Ручной запуск доступен через `Actions → Run workflow`.

## 7. Что входит в модель данных

Каждый материал — JSON-файл с полями:
- `id`, `slug`, `title`, `url`, `source`, `published_at`
- `summary`, `content.kids`, `content.general`, `content.pro`
- `tags`, `status`, `created_at`

Статусы первой версии: `review` и `published`. Агент не переводит материал в `published` автоматически.

## 8. Текущие ограничения MVP

- Сборщики используют публичные API/ленты и могут столкнуться с rate limits.
- Git — хранилище контента, а не полноценная база данных. Не сохраняйте полные копии чужих статей.
- Проверяйте права на использование текстов, изображений и логотипов источников.
- Контент, созданный ИИ, требует редакционной проверки, особенно факты, цифры и выводы.
- Для продакшена добавьте мониторинг, тесты качества, политику исправлений и процедуру удаления материалов.

## Структура

```text
agent/                 Python-агент
config/                настройки источников
data/inbox/            собранные исходные карточки
data/news/             подготовленные публикации
data/library/          база знаний
site/                  Astro frontend
.github/workflows/     автоматизация GitHub Actions
```
