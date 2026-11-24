# AI Portal

Бесплатный русскоязычный портал об искусственном интеллекте. Архитектура первой версии: static-first, Git-as-database, Python agent, Astro и GitHub Actions.

## Что уже работает

- Сбор материалов из Hacker News, arXiv, GitHub и Hugging Face.
- Нормализация и дедупликация по URL.
- Сохранение карточек источников в `data/inbox/`.
- Генерация трёх уровней материала через Gemini API (опционально).
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

## 3. Настроить Gemini (необязательно для первого запуска)

1. Получите API-ключ в Google AI Studio: https://aistudio.google.com/apikey
2. В GitHub откройте `Settings → Secrets and variables → Actions → New repository secret`.
3. Имя секрета: `GEMINI_API_KEY`.
4. Вставьте ключ в Secret value.

Модель задаётся переменной `GEMINI_MODEL` в workflow. Бесплатные квоты и доступность моделей меняются; проверьте актуальную модель в документации Gemini API.

Важно: передавайте модели только общедоступные материалы, которые допустимо отправлять стороннему API. Не отправляйте приватные данные.

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

Без `GEMINI_API_KEY` агент соберёт источники в `data/inbox/` со статусом `review`.
С ключом попробует сформировать три редакции и сохранит результат в `data/news/` со статусом `review` — автоматическая публикация в MVP выключена.

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

В `Settings → Secrets and variables → Actions` можно добавить `GEMINI_API_KEY`.
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
