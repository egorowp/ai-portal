from __future__ import annotations

import json
import os
import re
import hashlib
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from agent.ai_provider_manager import AIProviderManager, AIProviderError

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/sources.json").read_text(encoding="utf-8"))
INBOX = ROOT / "data/inbox"
NEWS = ROOT / "data/news"
USER_AGENT = "AI-Portal-Agent/0.1 (+https://github.com/)"

def env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, "").strip() or default))
    except (TypeError, ValueError):
        print(f"[WARN] invalid {name}; using default {default}")
        return default


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, "").strip() or default))
    except (TypeError, ValueError):
        print(f"[WARN] invalid {name}; using default {default}")
        return default


DELAY_BETWEEN = env_float("GENERATION_DELAY", 4.0)


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


def strip_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def validate_generated_content(raw: str):
    """Parse and validate the common AI Portal content contract."""
    generated = json.loads(strip_fences(raw))
    if not isinstance(generated, dict):
        raise ValueError("AI response must be a JSON object")

    required = ("summary", "kids", "general", "pro", "tags")
    missing = [key for key in required if key not in generated]
    if missing:
        raise ValueError("AI response is missing fields: " + ", ".join(missing))

    for key in ("summary", "kids", "general", "pro"):
        if not isinstance(generated[key], str) or not generated[key].strip():
            raise ValueError(f"AI response field '{key}' must be a non-empty string")

    if not isinstance(generated["tags"], list) or not all(
        isinstance(tag, str) and tag.strip() for tag in generated["tags"]
    ):
        raise ValueError("AI response field 'tags' must be a list of non-empty strings")
    return generated


def generate_content(manager: AIProviderManager, source_item):
    prompt = f"""
Ты редактор русскоязычного образовательного портала об ИИ.
Подготовь три самостоятельные версии материала по предоставленным метаданным.
Не добавляй неподтверждённые факты. Если данных недостаточно, прямо обозначь ограничения.
Верни только JSON с ключами summary, kids, general, pro, tags.
kids: понятно школьнику 8–14 лет, с аналогией и без опасных инструкций.
general: для широкой аудитории, объясни практическую значимость.
pro: техническая выжимка для разработчиков, отделяй факты от предположений.
Сохрани ссылку на первоисточник отдельно, не выдумывай цитаты.
Источник: {json.dumps(source_item, ensure_ascii=False)}
"""
    result = manager.generate(
        messages=[
            {"role": "system", "content": "You are a careful Russian-language AI Portal editor. Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=int(os.getenv("AI_MAX_TOKENS", "3500")),
        validator=validate_generated_content,
    )
    return validate_generated_content(result.text), result


def load_inbox_items(existing_urls: set[str]) -> tuple[list[dict], set[str]]:
    """Return retryable inbox entries and URLs of every valid queued entry.

    The second value deliberately includes entries whose retry time is in the
    future or whose retry budget is exhausted. They must not be re-added as new
    items by source collectors.
    """
    pending = []
    queued_urls: set[str] = set()
    now = datetime.now(timezone.utc)
    max_attempts = env_int("INBOX_MAX_ATTEMPTS", 8)
    if not INBOX.exists():
        return pending, queued_urls

    for path in sorted(INBOX.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
                continue
            queued_urls.add(entry["url"])
            if entry["url"] in existing_urls:
                continue
            attempts = env_int_value(entry.get("generation_attempts", 0), 0)
            if attempts >= max_attempts:
                continue
            retry_after = entry.get("retry_after")
            if retry_after:
                try:
                    retry_at = datetime.fromisoformat(retry_after.replace("Z", "+00:00"))
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    if retry_at > now:
                        continue
                except (ValueError, TypeError, AttributeError):
                    pass
            pending.append(entry)
        except (OSError, ValueError, TypeError) as exc:
            print(f"[WARN] inbox item {path.name}: cannot read: {exc}")
    return pending, queued_urls


def env_int_value(value, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def retry_delay_seconds(attempts: int) -> int:
    base = env_int("INBOX_RETRY_BASE_SECONDS", 3600)
    maximum = env_int("INBOX_RETRY_MAX_SECONDS", 86400)
    return min(base * (2 ** max(0, attempts - 1)), maximum)


def save_json(folder: Path, name: str, data):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    limit = env_int("MAX_ITEMS_PER_SOURCE", CONFIG.get("max_items_per_source", 10), minimum=1)
    collectors = [
        ("hackernews", collect_hn),
        ("arxiv", collect_arxiv),
        ("github", collect_github),
        ("huggingface", collect_huggingface),
    ]
    existing_urls: set[str] = set()
    if NEWS.exists():
        for path in NEWS.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("url"):
                    existing_urls.add(record["url"])
            except (OSError, ValueError, AttributeError):
                print(f"[WARN] cannot read published candidate: {path.name}")

    collected = []
    for source_id, collector in collectors:
        source_cfg = next((source for source in CONFIG["sources"] if source["id"] == source_id), {})
        if not source_cfg.get("enabled", False):
            continue
        try:
            results = collector(limit)
            print(f"[OK] {source_id}: {len(results)} items")
            collected.extend(results)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
            print(f"[WARN] {source_id}: {exc}")
        except Exception as exc:
            print(f"[WARN] {source_id}: unexpected error: {type(exc).__name__}: {exc}")

    inbox_items, queued_urls = load_inbox_items(existing_urls)
    new_items = []
    seen_urls = set(existing_urls) | set(queued_urls)
    for entry in collected:
        if entry["url"] in seen_urls:
            continue
        new_items.append(entry)
        seen_urls.add(entry["url"])

    # Persist every newly collected item before applying generation limits. This
    # guarantees that unprocessed items survive the end of this workflow run.
    for source_item in new_items:
        save_json(INBOX, source_item["id"], source_item)

    work_items = inbox_items + new_items
    manager = AIProviderManager()
    print(
        f"Collected {len(collected)}; retryable inbox {len(inbox_items)}; "
        f"new {len(new_items)}; generation limit {manager.max_generations}"
    )

    generated_count = 0
    try:
        if not manager.providers:
            print("[INFO] no AI providers configured; collected items were saved to data/inbox")
        for idx, source_item in enumerate(work_items if manager.providers else []):
            if generated_count >= manager.max_generations:
                print(f"[SKIP] generation limit reached; {len(work_items) - idx} items remain queued")
                break

            try:
                generated, result = generate_content(manager, source_item)
                source_item["summary"] = generated["summary"]
                source_item["content"] = {
                    "kids": generated["kids"],
                    "general": generated["general"],
                    "pro": generated["pro"],
                }
                source_item["tags"] = generated["tags"] or source_item.get("tags", [])
                source_item["status"] = "review"
                source_item["ai_provider"] = result.provider
                source_item["ai_model"] = result.model
                source_item["generated_at"] = datetime.now(timezone.utc).isoformat()
                source_item.pop("generation_error", None)
                source_item.pop("retry_after", None)
                slug = source_item.get("slug") or source_item["id"]
                target = NEWS / f"{slug}.json"
                if target.exists():
                    current = json.loads(target.read_text(encoding="utf-8"))
                    if current.get("url") != source_item.get("url"):
                        slug = f"{slug}-{source_item['id'][:8]}"
                save_json(NEWS, slug, source_item)
                inbox_path = INBOX / f"{source_item['id']}.json"
                if inbox_path.exists():
                    inbox_path.unlink()
                generated_count += 1
                print(
                    f"[OK] generation {source_item['id']}: provider={result.provider} "
                    f"model={result.model} saved to news ({generated_count}/{MAX_PER_RUN})"
                )
            except Exception as exc:
                attempts = env_int_value(source_item.get("generation_attempts", 0), 0) + 1
                source_item["generation_attempts"] = attempts
                source_item["generation_error"] = f"{type(exc).__name__}: {exc}"[:500]
                source_item["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
                delay = retry_delay_seconds(attempts)
                retry_at = datetime.now(timezone.utc).timestamp() + delay
                source_item["retry_after"] = datetime.fromtimestamp(retry_at, timezone.utc).isoformat()
                save_json(INBOX, source_item["id"], source_item)
                print(f"[WARN] generation {source_item['id']}: {type(exc).__name__}; retry in {delay}s")

            if idx < len(work_items) - 1:
                time.sleep(DELAY_BETWEEN)
    finally:
        manager.close()

    print(
        f"Agent run complete. Generated {generated_count} items this run. "
        "Items remain in review until editorial approval."
    )


if __name__ == "__main__":
    main()
