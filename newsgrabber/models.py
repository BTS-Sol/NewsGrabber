"""Datenmodelle, die zwischen den Verarbeitungsstufen weitergereicht werden."""

from __future__ import annotations

import datetime as _dt
import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Article:
    """Eine einzelne Meldung einer Quelle.

    ``content`` enthält den bereinigten Volltext und existiert nur während des
    Laufs im Speicher – er wird weder in der Datenbank noch im Bericht gespeichert.
    """

    source_id: str
    source_name: str
    url: str
    title: str
    published: Optional[_dt.datetime] = None  # zeitzonenbewusst
    teaser: str = ""
    content: str = ""
    language: str = "de"
    categories: tuple = ()  # mögliche Kategorien laut Quelle
    category: str = ""  # zugewiesene Kategorie
    quality: float = 0.7
    tags: list = field(default_factory=list)
    feed_rank: int = 0
    feed_size: int = 1
    author: str = ""
    guid: str = ""
    kicker: str = ""  # Dachzeile
    breaking: bool = False
    date_estimated: bool = False
    fetch_fulltext: bool = True
    fetch_priority: bool = False  # Volltext unabhängig von der Vorauswahl holen (z. B. HTML-Quellen ohne Datum)
    fulltext_status: str = ""  # ok | kurz | robots | fehler | aus | ""

    @property
    def text(self) -> str:
        """Bester verfügbarer Text: Volltext, sonst Anrisstext."""
        return self.content or self.teaser

    def key(self) -> str:
        return self.url or f"{self.source_id}:{self.guid or self.title}"


@dataclass
class Story:
    """Eine Nachricht, ggf. aus mehreren Quellen zusammengeführt (Duplikat-Cluster)."""

    articles: list
    category: str = ""
    topic: str = ""
    score: float = 0.0
    score_parts: dict = field(default_factory=dict)
    keywords: list = field(default_factory=list)
    history: str = ""  # "" | "wiederholung" | "fortsetzung"

    @property
    def lead(self) -> Article:
        """Leitartikel: höchste Quellenqualität, bevorzugt mit Volltext."""
        return max(self.articles, key=lambda a: (a.quality, bool(a.content), len(a.text), -a.feed_rank))

    @property
    def published(self) -> Optional[_dt.datetime]:
        dates = [a.published for a in self.articles if a.published]
        return max(dates) if dates else None

    @property
    def first_published(self) -> Optional[_dt.datetime]:
        dates = [a.published for a in self.articles if a.published]
        return min(dates) if dates else None

    @property
    def source_names(self) -> list:
        names = []
        for article in sorted(self.articles, key=lambda a: -a.quality):
            if article.source_name not in names:
                names.append(article.source_name)
        return names

    @property
    def urls(self) -> list:
        return sorted({a.url for a in self.articles if a.url})

    def fingerprint(self) -> str:
        return hashlib.sha1("\n".join(self.urls).encode("utf-8")).hexdigest()[:16]


@dataclass
class StorySummary:
    headline: str
    summary: str
    facts: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    method: str = "extraktiv"
    language: str = "de"


@dataclass
class SourceLink:
    name: str
    url: str
    published: Optional[_dt.datetime] = None


@dataclass
class BriefingItem:
    rank: int
    headline: str
    summary: str
    facts: list
    keywords: list
    sources: list  # list[SourceLink]
    published: Optional[_dt.datetime]
    category: str
    topic: str = ""
    score: float = 0.0
    score_parts: dict = field(default_factory=dict)
    history: str = ""
    method: str = "extraktiv"
    language: str = "de"
    fingerprint: str = ""
    anchor: str = ""


@dataclass
class CategorySection:
    key: str
    title: str
    items: list = field(default_factory=list)
    note: str = ""


@dataclass
class SourceResult:
    source_id: str
    name: str
    ok: bool
    items: int = 0
    error: str = ""
    duration: float = 0.0
    used_fallback: bool = False
    consecutive_failures: int = 0


@dataclass
class Briefing:
    date: _dt.date
    title: str
    generated_at: _dt.datetime
    window_start: _dt.datetime
    window_end: _dt.datetime
    sections: list = field(default_factory=list)
    top_items: list = field(default_factory=list)
    source_results: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    word_count: int = 0

    @property
    def failed_sources(self) -> list:
        return [r for r in self.source_results if not r.ok]

    def all_items(self) -> list:
        return [item for section in self.sections for item in section.items]
