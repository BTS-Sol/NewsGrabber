"""RSS- und Atom-Feeds (über feedparser)."""

from __future__ import annotations

import feedparser

from ..process.clean import html_to_text
from ..timeutil import from_struct_time
from .base import CollectContext, CollectError, item_limit, make_article

ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"
_GENERIC_TAGS = {"news", "allgemein", "uncategorized", "nachrichten", "top", "home", "meldung", "artikel", "article"}


def collect(source, ctx: CollectContext) -> list:
    response = ctx.http.get(source.url, accept=ACCEPT)
    feed = feedparser.parse(response.content, response_headers={
        "content-location": response.url,
        "content-type": response.headers.get("Content-Type", "application/xml"),
    })
    if not feed.entries:
        if getattr(feed, "bozo", False):
            raise CollectError(f"Feed nicht lesbar: {feed.get('bozo_exception')}")
        return []
    entries = feed.entries[:item_limit(source, ctx.config)]
    articles = []
    for rank, entry in enumerate(entries):
        url = entry.get("feedburner_origlink") or entry.get("link") or ""
        if not url:
            url = next((l.get("href") for l in entry.get("links", []) if l.get("href")), "")
        published = from_struct_time(entry.get("published_parsed") or entry.get("updated_parsed")
                                     or entry.get("created_parsed"))
        content = ""
        if entry.get("content"):
            content = max((c.get("value", "") for c in entry["content"]), key=len)
        summary = entry.get("summary", "")
        if content and summary and summary.strip() == content.strip():
            summary = ""
        content_text = html_to_text(content) if content else ""
        tags = [t.get("term") or t.get("label") for t in entry.get("tags", [])]
        tags = [t for t in tags if t and t.strip().lower() not in _GENERIC_TAGS]
        article = make_article(
            source, ctx, url=url, title=entry.get("title", ""), published=published, teaser=summary,
            content=content_text, tags=tags, rank=rank, size=len(entries), author=entry.get("author", ""),
            guid=entry.get("id", ""), base_url=response.url,
        )
        if article:
            articles.append(article)
    return articles
