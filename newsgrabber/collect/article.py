"""Volltext eines Artikels abrufen und den Haupttext extrahieren (trafilatura).

Der extrahierte Text dient nur als Grundlage für die eigene Zusammenfassung und
wird nicht dauerhaft gespeichert.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import trafilatura
from bs4 import BeautifulSoup

from ..models import Article
from ..process.clean import clean_content, clean_teaser
from ..timeutil import parse_datetime
from .base import CollectContext
from .http import FetchError, RobotsDisallowed

log = logging.getLogger(__name__)

_DATE_META = ("article:published_time", "og:published_time", "datePublished", "publishdate", "pubdate",
              "date", "dc.date", "dc.date.issued", "sailthru.date", "parsely-pub-date")


def fetch_fulltext(article: Article, ctx: CollectContext) -> None:
    """Ergänzt ``article.content`` (und ggf. Datum/Anrisstext). Fehler werden nur protokolliert."""
    if not article.fetch_fulltext:
        article.fulltext_status = article.fulltext_status or "aus"
        return
    min_chars = ctx.config.collection.fulltext.min_chars
    markup: Optional[str] = None
    final_url = article.url
    try:
        response = ctx.http.get(article.url, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5")
        final_url = response.url
        if "html" in response.content_type or not response.content_type:
            markup = _decode(response)
        else:
            article.fulltext_status = "kein-html"
    except RobotsDisallowed:
        article.fulltext_status = "robots"
        log.debug("Volltext von %s laut robots.txt nicht erlaubt", article.url)
        return
    except FetchError as exc:
        article.fulltext_status = "fehler"
        log.info("Volltext nicht abrufbar (%s): %s", article.source_id, exc)

    text, meta = extract(markup, final_url) if markup else ("", {})
    browser = ctx.browser
    if (len(text) < min_chars and browser is not None and browser.usable
            and ctx.config.collection.playwright.article_fallback and article.fulltext_status != "kein-html"):
        try:
            rendered = browser.render(article.url)
            text2, meta2 = extract(rendered, article.url)
            if len(text2) > len(text):
                text, meta = text2, meta2 or meta
                log.debug("Volltext von %s per Playwright ergänzt", article.url)
        except FetchError as exc:
            log.debug("Playwright-Fallback für %s fehlgeschlagen: %s", article.url, exc)

    if text:
        article.content = clean_content(text, ctx.boilerplate)
    if meta.get("published") and (article.published is None or article.date_estimated):
        article.published = meta["published"]
        article.date_estimated = False
    if not article.teaser and meta.get("description"):
        article.teaser = clean_teaser(meta["description"], article.title, ctx.boilerplate)
    if len(article.content) >= min_chars:
        article.fulltext_status = "ok"
    elif article.fulltext_status not in ("fehler", "kein-html"):
        article.fulltext_status = "kurz"  # z. B. Bezahlschranke


def _decode(response) -> str:
    if response.encoding:
        return response.text
    head = response.content[:4096].decode("ascii", errors="ignore")
    match = re.search(r"charset=[\"']?([\w-]+)", head, re.IGNORECASE)
    try:
        return response.content.decode(match.group(1) if match else "utf-8", errors="replace")
    except LookupError:
        return response.content.decode("utf-8", errors="replace")


def extract(markup: str, url: str) -> "tuple[str, dict]":
    """Haupttext und Metadaten (Veröffentlichungszeit, Beschreibung) aus HTML."""
    try:
        document = trafilatura.bare_extraction(markup, url=url, include_comments=False, include_tables=False,
                                               favor_precision=True, with_metadata=True)
        result = (document.as_dict() if hasattr(document, "as_dict") else document) or {}
    except Exception as exc:  # noqa: BLE001 – trafilatura kann bei kaputtem HTML beliebige Fehler werfen
        log.debug("trafilatura-Fehler bei %s: %s", url, exc)
        result = {}
    text = result.get("text") or ""
    meta = {"description": result.get("description") or ""}
    published = published_from_html(markup)
    if published is None and result.get("date"):
        published = parse_datetime(result["date"])  # nur Tagesgenauigkeit
    meta["published"] = published
    return text, meta


def published_from_html(markup: str):
    """Veröffentlichungszeit aus Meta-Tags, JSON-LD oder ``<time datetime>``."""
    try:
        soup = BeautifulSoup(markup[:400_000], "lxml")
    except Exception:  # noqa: BLE001
        return None
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or tag.get("itemprop") or "").strip()
        if key in _DATE_META or key.lower() in _DATE_META:
            value = parse_datetime(tag.get("content"))
            if value:
                return value
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        for node in _walk_jsonld(data):
            value = parse_datetime(node.get("datePublished") or node.get("dateCreated"))
            if value:
                return value
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        return parse_datetime(time_tag["datetime"])
    return None


def _walk_jsonld(data):
    if isinstance(data, list):
        for item in data:
            yield from _walk_jsonld(item)
    elif isinstance(data, dict):
        if "datePublished" in data or "dateCreated" in data:
            yield data
        for key in ("@graph", "mainEntity", "mainEntityOfPage"):
            if key in data:
                yield from _walk_jsonld(data[key])
