from __future__ import annotations

import json
import os
import re
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/sources.json").read_text(encoding="utf-8"))
INBOX = ROOT / "data/inbox"
NEWS = ROOT / "data/news"
USER_AGENT = "AI-Portal-Agent/0.1 (+https://github.com/)"


def get_json(url: str):
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def get_text(url: str):
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=25) as response:
        return response.read().decode("utf-8", errors="replace")


def clean_text(value: str, limit: int = 1200) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def stable_id(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()[:16]


def item(title, url, source, description="", published_at=None, tags=None):
    url = (url or "").strip()
    if not url:
        return None
    return {
        "id": stable_id(url),
        "slug": re.sub(r"[^a-z0-9-]+", "-", (title or "item").lower()).strip("-")[:70] or stable_id(url),
        "title": clean_text(title, 240) or "Без названия",
        "url": url,
        "source": source,
        "published_at": published_at,
        "description": clean_text(description),
        "tags": tags or ["искусственный интеллект"],
        "status": "review",
        "created_at": datetime.now(timezone.utc).isoformat()
    }


def collect_hn(limit):
    data = get_json(f"https://hn.algolia.com/api/v1/search_by_date?query=AI%20OR%20LLM&tags=story&hitsPerPage={limit}")
    out = []
    for hit in data.get("hits", []):
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        obj = item(hit.get("title"), url, "Hacker News", hit.get("story_text") or "",
                   hit.get("created_at"), ["AI", "Hacker News"])
        if obj: out.append(obj)
    return out


def collect_arxiv(limit):
    query = quote(CONFIG.get("sources", [{}])[1].get("query", "artificial intelligence"))
    xml = get_text(f"https://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results={limit}&sortBy=submittedDate&sortOrder=descending")
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml)
    out = []
    for entry in root.findall("a:entry", ns):
        title = entry.findtext("a:title", default="", namespaces=ns)
        summary = entry.findtext("a:summary", default="", namespaces=ns)
        link = entry.find("a:id", ns)
        published = entry.findtext("a:published", default=None, namespaces=ns)
        obj = item(title, link.text if link is not None else "", "arXiv", summary, published, ["research", "arXiv"])
        if obj: out.append(obj)
    return out


def collect_github(limit):
    query = quote("artificial intelligence")
    data = get_json(f"https://api.github.com/search/repositories?q={query}&sort=updated&order=desc&per_page={limit}")
    out = []
    for repo in data.get("items", []):
        obj = item(repo.get("full_name"), repo.get("html_url"), "GitHub",
                   repo.get("description") or "", repo.get("updated_at"),
                   ["open-source", "GitHub", "AI"])
        if obj: out.append(obj)
    return out


def collect_huggingface(limit):
    data = get_json(f"https://huggingface.co/api/models?search=llm&sort=lastModified&direction=-1&limit={limit}")
    out = []
    for model in data:
        model_id = model.get("modelId") or model.get("id")
        if not model_id: continue
        obj = item(model_id, f"https://huggingface.co/{model_id}", "Hugging Face",
                   "Модель из каталога Hugging Face.", model.get("lastModified"),
                   ["models", "Hugging Face"])
        if obj: out.append(obj)
    return out


def gemini_generate(source_item):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    prompt = f"""
Ты редактор русскоязычного образовательного портала об ИИ.
Подготовь три самостоятельные версии материала по предоставленным метаданным.
Не добавляй неподтверждённые факты. Если данных недостаточно, прямо обозначь ограничения.
Верни только JSON с ключами summary, kids, general, pro, tags.
kids: понятно школьнику 8-14 лет, с аналогией и без опасных инструкций.
general: для широкой аудитории, объясни практическую значимость.
pro: техническая выжимка для разработчиков, отделяй факты от предположений.
Сохрани ссылку на первоисточник отдельно, не выдумывай цитаты.
Источник: {json.dumps(source_item, ensure_ascii=False)}
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
    last_exc = None
    for attempt in range(4):
        try:
            req = Request(url, data=json.dumps(payload).encode(), headers={
                "Content-Type": "application/json", "User-Agent": USER_AGENT
            })
            with urlopen(req, timeout=120) as response:
                data = json.loads(response.read().decode())
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
            return json.loads(text)
        except HTTPError as exc:
            if exc.code not in (429, 503):
                raise
            if attempt == 3:
                raise
            retry_after = exc.headers.get("Retry-After") or ""
            wait = int(retry_after) if retry_after.isdigit() else 15 * (attempt + 1)
            print(f"[RETRY] {source_item['id']}: HTTP {exc.code}, повтор через {wait}s")
            time.sleep(wait)
        except TimeoutError:
            last_exc = exc
            if attempt == 3:
                raise
            print(f"[RETRY] {source_item['id']}: таймаут, повтор через 15s")
            time.sleep(15)
    raise last_exc if last_exc else RuntimeError("generation failed")


def save_json(folder: Path, name: str, data):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    limit = int(os.getenv("MAX_ITEMS_PER_SOURCE", CONFIG.get("max_items_per_source", 10)))
    max_generations = int(os.getenv("MAX_GENERATIONS", "15"))
    collectors = [
        ("hackernews", collect_hn),
        ("arxiv", collect_arxiv),
        ("github", collect_github),
        ("huggingface", collect_huggingface),
    ]
    existing = set()
    if NEWS.exists():
        for path in NEWS.glob("*.json"):
            try: existing.add(json.loads(path.read_text(encoding="utf-8")).get("url"))
            except Exception: pass

    collected = []
    for source_id, collector in collectors:
        source_cfg = next((s for s in CONFIG["sources"] if s["id"] == source_id), {})
        if not source_cfg.get("enabled", False):
            continue
        try:
            results = collector(limit)
            print(f"[OK] {source_id}: {len(results)} items")
            collected.extend(results)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
            print(f"[WARN] {source_id}: {exc}")
        except Exception as exc:
            print(f"[WARN] {source_id}: unexpected error: {exc}")

    new_items = [x for x in collected if x["url"] not in existing]
    print(f"Collected {len(collected)}; new {len(new_items)}")
    generated = 0
    for source_item in new_items:
        if generated >= max_generations:
            remaining = len(new_items) - generated
            print(f"[SKIP] достигнут лимит {max_generations} генераций на запуск; "
                  f"{remaining} записей попробуют снова в следующем запуске")
            break
        try:
            generated_raw = gemini_generate(source_item)
            if generated_raw:
                source_item["summary"] = generated_raw.get("summary", "")
                source_item["content"] = {
                    "kids": generated_raw.get("kids", ""),
                    "general": generated_raw.get("general", ""),
                    "pro": generated_raw.get("pro", "")
                }
                source_item["tags"] = generated_raw.get("tags", source_item["tags"])
                source_item["status"] = "review"
                save_json(NEWS, source_item["slug"], source_item)
                generated += 1
                print(f"[OK] generation {source_item['id']}: saved to news ({generated}/{max_generations})")
            else:
                save_json(INBOX, source_item["id"], source_item)
            time.sleep(5)
        except Exception as exc:
            source_item["generation_error"] = str(exc)[:500]
            save_json(INBOX, source_item["id"], source_item)
            print(f"[WARN] generation {source_item['id']}: {exc}")

    print(f"Agent run complete. Generated {generated} items this run. "
          "Items remain in review until editorial approval.")


if __name__ == "__main__":
    main()
