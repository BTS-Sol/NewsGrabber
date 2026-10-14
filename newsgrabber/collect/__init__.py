"""Datensammlung: Quellen abrufen (parallel, fehlertolerant) und Volltexte ergänzen.

Neue Quellentypen werden in :data:`COLLECTORS` registriert. Ein Collector ist eine
Funktion ``(SourceConfig, CollectContext) -> list[Article]``.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import SourceConfig
from ..models import SourceResult
from ..process.dedup import canonical_url
from . import api, html, rss
from .article import fetch_fulltext
from .base import CollectContext, CollectError
from .browser import BrowserRenderer
from .http import FetchError, HttpClient

log = logging.getLogger(__name__)

COLLECTORS = {
    "rss": rss.collect,
    "tagesschau_api": api.collect_tagesschau,
    "hackernews": api.collect_hackernews,
    "json_api": api.collect_json,
    "html": html.collect,
}

__all__ = ["COLLECTORS", "CollectContext", "BrowserRenderer", "HttpClient", "collect_sources", "fetch_fulltexts",
           "run_source"]


def run_source(source: SourceConfig, ctx: CollectContext) -> "tuple[list, SourceResult]":
    """Ruft eine Quelle ab; bei Fehlern (oder leerem Ergebnis) wird ``fallback`` versucht."""
    started = time.monotonic()
    result = SourceResult(source_id=source.id, name=source.name, ok=False)
    attempts = [(source, False)]
    if source.fallback:
        fallback = dataclasses.replace(source, fallback=None, **{
            k: v for k, v in source.fallback.items() if k in {f.name for f in dataclasses.fields(SourceConfig)}})
        attempts.append((fallback, True))
    errors = []
    for candidate, is_fallback in attempts:
        try:
            articles = COLLECTORS[candidate.type](candidate, ctx)
        except (FetchError, CollectError) as exc:
            errors.append(str(exc))
            log.warning("Quelle %s%s fehlgeschlagen: %s", source.id, " (Ersatz)" if is_fallback else "", exc)
            continue
        except Exception as exc:  # noqa: BLE001 – eine kaputte Quelle darf den Lauf nicht abbrechen
            errors.append(f"{type(exc).__name__}: {exc}")
            log.exception("Unerwarteter Fehler in Quelle %s", source.id)
            continue
        if not articles and not is_fallback and len(attempts) > 1:
            errors.append("keine Einträge")
            continue
        result.ok, result.items, result.used_fallback = True, len(articles), is_fallback
        result.duration = time.monotonic() - started
        if is_fallback:
            log.info("Quelle %s: Ersatzquelle verwendet (%d Einträge)", source.id, len(articles))
        return articles, result
    result.error = "; ".join(errors)
    result.duration = time.monotonic() - started
    return [], result


def collect_sources(sources: list, ctx: CollectContext) -> "tuple[list, list]":
    """Alle Quellen parallel abrufen. Liefert (Artikel, Ergebnisse je Quelle)."""
    articles, results = [], []
    workers = max(1, min(ctx.config.collection.max_workers, len(sources) or 1))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="quelle") as pool:
        futures = {pool.submit(run_source, source, ctx): source for source in sources}
        for future in as_completed(futures):
            items, result = future.result()
            articles.extend(items)
            results.append(result)
            if result.ok:
                log.info("Quelle %-24s %3d Einträge (%.1f s)", result.source_id, result.items, result.duration)
    order = {s.id: i for i, s in enumerate(sources)}
    results.sort(key=lambda r: order.get(r.source_id, 0))
    return articles, results


def fetch_fulltexts(articles: list, ctx: CollectContext) -> dict:
    """Volltexte parallel abrufen (Rate Limit pro Host greift weiterhin)."""
    stats: dict = {}
    if not articles:
        return stats
    groups: dict = {}
    for article in articles:  # dieselbe URL aus mehreren Quellen nur einmal abrufen
        groups.setdefault(canonical_url(article.url), []).append(article)
    workers = max(1, ctx.config.collection.max_workers)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="volltext") as pool:
        futures = {}
        for group in groups.values():
            representative = next((a for a in group if a.fetch_fulltext), group[0])
            futures[pool.submit(fetch_fulltext, representative, ctx)] = (representative, group)
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:  # noqa: BLE001
                log.exception("Unerwarteter Fehler beim Volltextabruf")
            first, group = futures[future]
            for other in group:
                if other is first:
                    continue
                if len(first.content) > len(other.content):
                    other.content, other.fulltext_status = first.content, first.fulltext_status
                if other.published is None or other.date_estimated:
                    other.published, other.date_estimated = first.published, first.date_estimated
                other.teaser = other.teaser or first.teaser
    for article in articles:
        stats[article.fulltext_status or "unbekannt"] = stats.get(article.fulltext_status or "unbekannt", 0) + 1
    return stats
