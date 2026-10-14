"""Gemeinsame Bausteine der Collector-Module."""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

from ..config import Config, SourceConfig
from ..models import Article
from ..process.clean import Boilerplate, clean_content, clean_teaser, clean_title
from ..process.dedup import strip_tracking
from ..process.text import detect_language, word_count

log = logging.getLogger(__name__)


class CollectError(Exception):
    """Quelle lieferte keine verwertbaren Daten."""


@dataclass
class CollectContext:
    config: Config
    http: "object"  # HttpClient
    browser: "Optional[object]" = None  # BrowserRenderer
    boilerplate: Optional[Boilerplate] = None

    def __post_init__(self):
        if self.boilerplate is None:
            self.boilerplate = Boilerplate(self.config.processing.boilerplate_patterns)


def item_limit(source: SourceConfig, config: Config) -> int:
    return source.max_items or config.collection.max_items_per_source


def make_article(source: SourceConfig, ctx: CollectContext, *, url: str, title: str,
                 published: Optional[_dt.datetime] = None, teaser: str = "", content: str = "",
                 tags=(), rank: int = 0, size: int = 1, author: str = "", guid: str = "",
                 kicker: str = "", breaking: bool = False, base_url: str = "") -> Optional[Article]:
    """Erzeugt einen bereinigten :class:`Article` oder ``None`` bei unbrauchbaren Einträgen."""
    if base_url and url:
        url = urljoin(base_url, url)
    url = strip_tracking(url or "")
    title = clean_title(title or "", source.name)
    if not url or not title or not url.startswith(("http://", "https://")):
        return None
    if word_count(title) < ctx.config.processing.min_title_words:
        log.debug("%s: Titel zu kurz, übersprungen: %r", source.id, title)
        return None
    teaser = clean_teaser(teaser or "", title, ctx.boilerplate)
    content = clean_content(content, ctx.boilerplate) if content else ""
    min_chars = ctx.config.collection.fulltext.min_chars
    if not content and len(teaser) >= 3 * min_chars:
        # manche Feeds liefern den kompletten Text als „summary“
        content, teaser = clean_content(teaser, ctx.boilerplate), ""
    language = source.language or detect_language(f"{title}. {teaser} {content[:1500]}")
    clean_tags = []
    for tag in tags or ():
        tag = " ".join(str(tag).split())
        if 2 <= len(tag) <= 40 and tag not in clean_tags:
            clean_tags.append(tag)
    has_fulltext = len(content) >= min_chars
    return Article(
        source_id=source.id, source_name=source.name, url=url, title=title, published=published,
        teaser=teaser, content=content, language=language, categories=tuple(source.category),
        category=source.category[0] if len(source.category) == 1 else "",
        quality=source.quality, tags=clean_tags, feed_rank=rank, feed_size=max(size, 1), author=author or "",
        guid=guid or "", kicker=clean_title(kicker or ""), breaking=breaking,
        fetch_fulltext=source.fulltext and not has_fulltext,
        fulltext_status="ok" if has_fulltext else "",
    )
