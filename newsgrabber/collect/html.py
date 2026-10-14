"""Übersichtsseiten ohne Feed: Artikel-Links per CSS-Selektor, bei Bedarf mit Playwright gerendert.

Optionen der Quelle::

    link_selector: "article h2 a"     # CSS-Selektor für Artikel-Links
    include_pattern: "/artikel/"      # Regex, der Artikel-URLs enthalten müssen
    exclude_pattern: "/video/"        # Regex für auszuschließende URLs
    render: auto                      # static | browser | auto (Browser nur, wenn statisch nichts gefunden wird)

Datum und Text der Artikel werden in der Volltext-Stufe ergänzt.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from ..process.text import collapse
from .base import CollectContext, CollectError, item_limit, make_article

log = logging.getLogger(__name__)

DEFAULT_SELECTOR = "article a[href], h2 a[href], h3 a[href]"


def collect(source, ctx: CollectContext) -> list:
    options = source.options
    render = str(options.get("render", "auto")).lower()
    browser = ctx.browser if ctx.browser is not None and ctx.browser.usable else None
    if render == "browser" and browser is None:
        raise CollectError("Quelle benötigt Playwright (render: browser), Playwright ist aber nicht verfügbar")

    links = []
    page_url = source.url
    if render != "browser":
        response = ctx.http.get(source.url, accept="text/html,application/xhtml+xml")
        page_url = response.url
        links = extract_links(response.text, page_url, options)
    if not links and render in ("browser", "auto") and browser is not None:
        log.info("%s: keine Links im statischen HTML – Seite wird mit Playwright gerendert", source.id)
        links = extract_links(browser.render(source.url), page_url, options)
    if not links:
        raise CollectError("Keine Artikel-Links gefunden (link_selector prüfen)")

    links = links[:item_limit(source, ctx.config)]
    articles = []
    for rank, (url, title) in enumerate(links):
        article = make_article(source, ctx, url=url, title=title, rank=rank, size=len(links))
        if article:
            article.fetch_fulltext = source.fulltext
            article.fetch_priority = True  # Datum und Text stehen erst nach dem Artikelabruf fest
            articles.append(article)
    return articles


def extract_links(markup: str, base_url: str, options: dict) -> list:
    soup = BeautifulSoup(markup or "", "lxml")
    selector = options.get("link_selector") or DEFAULT_SELECTOR
    include = re.compile(options["include_pattern"]) if options.get("include_pattern") else None
    exclude = re.compile(options["exclude_pattern"]) if options.get("exclude_pattern") else None
    seen, links = set(), []
    for anchor in soup.select(selector):
        href = anchor.get("href")
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if url in seen or (include and not include.search(url)) or (exclude and exclude.search(url)):
            continue
        title = collapse(anchor.get("title") or anchor.get_text(" "))
        if not title:
            continue
        seen.add(url)
        links.append((url, title))
    return links
