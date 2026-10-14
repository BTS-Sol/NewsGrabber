"""JSON-APIs: tagesschau-API, Hacker News (Algolia/Firebase) und ein generischer JSON-Adapter."""

from __future__ import annotations

import logging
import os
from typing import Any

from ..process.clean import html_to_text
from ..timeutil import parse_datetime
from .base import CollectContext, CollectError, item_limit, make_article

log = logging.getLogger(__name__)
ACCEPT_JSON = "application/json"


# ------------------------------------------------------------------ tagesschau
def collect_tagesschau(source, ctx: CollectContext) -> list:
    """tagesschau-API (``/api2u/news/`` bzw. ``/api2u/homepage/``).

    Die API liefert Text-Bausteine des Artikels mit; ein separater Seitenabruf ist
    daher meist nicht nötig.
    """
    data = ctx.http.get(source.url, accept=ACCEPT_JSON).json()
    if not isinstance(data, dict) or not isinstance(data.get("news"), list):
        raise CollectError("Unerwartetes Antwortformat der tagesschau-API (Schlüssel 'news' fehlt)")
    items = [i for i in data["news"] if isinstance(i, dict) and i.get("type", "story") in ("story", "webview")]
    items = items[:item_limit(source, ctx.config)]
    articles = []
    for rank, item in enumerate(items):
        blocks = item.get("content") or []
        paragraphs = [html_to_text(b.get("value", "")) for b in blocks
                      if isinstance(b, dict) and b.get("type") == "text" and b.get("value")]
        tags = [t.get("tag") for t in item.get("tags") or [] if isinstance(t, dict)]
        article = make_article(
            source, ctx, url=item.get("shareURL") or item.get("detailsweb") or "", title=item.get("title", ""),
            published=parse_datetime(item.get("date")), teaser=item.get("firstSentence", ""),
            content="\n".join(p for p in paragraphs if p), tags=tags, rank=rank, size=len(items),
            guid=item.get("sophoraId") or item.get("externalId") or "", kicker=item.get("topline", ""),
            breaking=bool(item.get("breakingNews")),
        )
        if article:
            articles.append(article)
    return articles


# ------------------------------------------------------------------ Hacker News
def collect_hackernews(source, ctx: CollectContext) -> list:
    """Hacker News über die Algolia-Such-API (eine Anfrage) oder die Firebase-API.

    Optionen: ``min_points`` (Standard 100), ``discussion_links`` (Standard false).
    """
    url = source.url or "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50"
    min_points = int(source.options.get("min_points", 100))
    limit = item_limit(source, ctx.config)
    if "firebaseio.com" in url:
        stories = _hackernews_firebase(url, ctx, limit * 2)
    else:
        data = ctx.http.get(url, accept=ACCEPT_JSON).json()
        if not isinstance(data, dict) or "hits" not in data:
            raise CollectError("Unerwartetes Antwortformat der Hacker-News-API (Schlüssel 'hits' fehlt)")
        stories = [{
            "id": hit.get("objectID"), "title": hit.get("title") or hit.get("story_title"),
            "url": hit.get("url") or hit.get("story_url"), "points": hit.get("points") or 0,
            "time": hit.get("created_at_i") or hit.get("created_at"), "text": hit.get("story_text") or "",
            "author": hit.get("author") or "",
        } for hit in data["hits"] if isinstance(hit, dict)]
    stories = [s for s in stories if s.get("title") and (s.get("points") or 0) >= min_points]
    stories.sort(key=lambda s: -(s.get("points") or 0))
    stories = stories[:limit]
    articles = []
    for rank, story in enumerate(stories):
        discussion = f"https://news.ycombinator.com/item?id={story['id']}"
        article = make_article(
            source, ctx, url=story.get("url") or discussion, title=story["title"],
            published=parse_datetime(story.get("time")), teaser=story.get("text", ""), rank=rank,
            size=len(stories), author=story.get("author", ""), guid=str(story.get("id", "")),
        )
        if article:
            article.tags = []
            articles.append(article)
    return articles


def _hackernews_firebase(url: str, ctx: CollectContext, scan: int) -> list:
    base = url.split("/v0/")[0] + "/v0/"
    ids = ctx.http.get(url, accept=ACCEPT_JSON).json()
    if not isinstance(ids, list):
        raise CollectError("Unerwartetes Antwortformat der Hacker-News-API (Liste erwartet)")
    stories = []
    for item_id in ids[:scan]:
        item = ctx.http.get(f"{base}item/{item_id}.json", accept=ACCEPT_JSON).json() or {}
        if item.get("type") == "story" and not item.get("dead") and not item.get("deleted"):
            stories.append({"id": item_id, "title": item.get("title"), "url": item.get("url"),
                            "points": item.get("score") or 0, "time": item.get("time"),
                            "text": item.get("text") or "", "author": item.get("by") or ""})
    return stories


# ------------------------------------------------------------------ generisch
def collect_json(source, ctx: CollectContext) -> list:
    """Generischer JSON-Adapter mit konfigurierbarer Feldzuordnung.

    Optionen::

        items_path: articles            # Pfad zur Liste der Einträge (Punkt-Notation)
        fields: {title: title, url: url, published: publishedAt, teaser: description,
                 content: content, tags: keywords, author: author}
        params: {language: de}          # zusätzliche Query-Parameter
        api_key_env: NEWSAPI_KEY        # Umgebungsvariable mit dem API-Schlüssel
        api_key_param: apiKey           # … als Query-Parameter
        api_key_header: X-Api-Key       # … oder als HTTP-Header
    """
    options = source.options
    params = dict(options.get("params") or {})
    headers = {}
    key_env = options.get("api_key_env")
    if key_env:
        key = os.environ.get(key_env)
        if not key:
            raise CollectError(f"API-Schlüssel fehlt: Umgebungsvariable {key_env} ist nicht gesetzt")
        if options.get("api_key_header"):
            headers[options["api_key_header"]] = key
        else:
            params[options.get("api_key_param", "apiKey")] = key
    data = ctx.http.get(source.url, accept=ACCEPT_JSON, params=params or None, headers=headers or None).json()
    items = lookup(data, options.get("items_path", ""))
    if not isinstance(items, list):
        raise CollectError(f"items_path '{options.get('items_path', '')}' verweist auf keine Liste")
    fields = {"title": "title", "url": "url", "published": "published", "teaser": "description",
              "content": "", "tags": "", "author": "author"}
    fields.update(options.get("fields") or {})
    items = items[:item_limit(source, ctx.config)]
    articles = []
    for rank, item in enumerate(items):
        def get(name):
            path = fields.get(name)
            return lookup(item, path) if path else None
        tags = get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        tags = [t.get("name") or t.get("tag") or t.get("label") if isinstance(t, dict) else t for t in tags]
        content = get("content") or ""
        article = make_article(
            source, ctx, url=str(get("url") or ""), title=str(get("title") or ""),
            published=parse_datetime(get("published")), teaser=str(get("teaser") or ""),
            content=html_to_text(str(content)) if content else "", tags=[t for t in tags if t],
            rank=rank, size=len(items), author=_text(get("author")), base_url=source.url,
        )
        if article:
            articles.append(article)
    return articles


def lookup(data: Any, path: str) -> Any:
    """Punkt-Notation, Listenindizes als Zahl: ``response.results.0.title``."""
    if not path:
        return data
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            current = current[index] if -len(current) <= index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value if v)
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value)
