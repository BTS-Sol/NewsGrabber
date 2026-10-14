"""Optionale SQLite-Datenbank: verarbeitete Artikel, Berichtsverlauf, Quellenstatus, Zusammenfassungs-Cache.

Gespeichert werden nur Metadaten (URL, Titel, Zeitpunkte, Prüfsumme) und die eigenen
Zusammenfassungen – **keine Volltexte** der Quellen.

Ist die Datenbank deaktiviert (``database.enabled: false`` oder ``--ohne-db``), wird
:class:`NullStorage` verwendet, die alle Aufrufe ignoriert.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

from .models import Briefing, Story
from .process.text import terms

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS articles (
    url           TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    published_at  TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    language      TEXT,
    category      TEXT,
    content_hash  TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_seen ON articles(last_seen_at);
CREATE TABLE IF NOT EXISTS reports (
    report_date   TEXT PRIMARY KEY,
    generated_at  TEXT NOT NULL,
    word_count    INTEGER,
    story_count   INTEGER,
    files         TEXT,
    stats         TEXT
);
CREATE TABLE IF NOT EXISTS report_stories (
    report_date   TEXT NOT NULL,
    category      TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    headline      TEXT NOT NULL,
    fingerprint   TEXT,
    urls          TEXT NOT NULL,
    terms         TEXT NOT NULL,
    PRIMARY KEY (report_date, category, rank)
);
CREATE TABLE IF NOT EXISTS source_status (
    source_id            TEXT PRIMARY KEY,
    last_attempt         TEXT,
    last_success         TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT,
    last_items           INTEGER
);
CREATE TABLE IF NOT EXISTS summary_cache (
    cache_key   TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    payload     TEXT NOT NULL
);
"""


def _iso(value: Optional[_dt.datetime]) -> Optional[str]:
    return value.astimezone(_dt.timezone.utc).isoformat(timespec="seconds") if value else None


def _parse(value: Optional[str]) -> Optional[_dt.datetime]:
    return _dt.datetime.fromisoformat(value) if value else None


class Storage:
    """SQLite-Zugriff. ``readonly`` (für ``--vorschau``) liest Verlauf und Cache, schreibt aber nichts."""

    enabled = True

    def __init__(self, path: Path, readonly: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        if readonly:
            self.conn = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        if not readonly:
            with self.conn:
                self.conn.executescript(SCHEMA)
                self.conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                                  (str(SCHEMA_VERSION),))

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ Artikel
    def remember_articles(self, articles: list, now: _dt.datetime) -> None:
        """Speichert gesehene Artikel und setzt bei Einträgen ohne Datum den ersten Fundzeitpunkt.

        So gelten Meldungen ohne Veröffentlichungsdatum, die schon an früheren Tagen im
        Feed standen, nicht fälschlich als neu.
        """
        stamp = _iso(now)
        with self.conn:
            for article in articles:
                row = self.conn.execute("SELECT first_seen_at FROM articles WHERE url = ?", (article.url,)).fetchone()
                if self.readonly:
                    first_seen = _parse(row["first_seen_at"]) if row else now
                elif row is None:
                    self.conn.execute(
                        "INSERT INTO articles(url, source_id, title, published_at, first_seen_at, last_seen_at,"
                        " language, category) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (article.url, article.source_id, article.title[:500], _iso(article.published), stamp, stamp,
                         article.language, article.category))
                    first_seen = now
                else:
                    self.conn.execute("UPDATE articles SET last_seen_at = ?, title = ? WHERE url = ?",
                                      (stamp, article.title[:500], article.url))
                    first_seen = _parse(row["first_seen_at"])
                if article.published is None and first_seen is not None:
                    article.published, article.date_estimated = first_seen, True

    def update_article_details(self, articles: list) -> None:
        """Nach dem Volltextabruf: Datum, Kategorie und eine Prüfsumme des Texts (nicht den Text)."""
        if self.readonly:
            return
        with self.conn:
            for article in articles:
                digest = hashlib.sha1(article.content.encode("utf-8")).hexdigest() if article.content else None
                self.conn.execute(
                    "UPDATE articles SET published_at = COALESCE(?, published_at), category = ?,"
                    " content_hash = COALESCE(?, content_hash) WHERE url = ?",
                    (None if article.date_estimated else _iso(article.published), article.category, digest,
                     article.url))

    # ------------------------------------------------------------ Verlauf
    def history(self, report_date: _dt.date, days: int) -> "History":
        start = (report_date - _dt.timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT report_date, headline, urls, terms FROM report_stories"
            " WHERE report_date >= ? AND report_date < ?", (start, report_date.isoformat())).fetchall()
        return History([(r["report_date"], r["headline"], set(json.loads(r["urls"])), set(json.loads(r["terms"])))
                        for r in rows])

    def save_report(self, briefing: Briefing, files: list) -> None:
        if self.readonly:
            return
        day = briefing.date.isoformat()
        with self.conn:
            self.conn.execute("DELETE FROM report_stories WHERE report_date = ?", (day,))
            for item in briefing.all_items():
                self.conn.execute(
                    "INSERT INTO report_stories(report_date, category, rank, headline, fingerprint, urls, terms)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (day, item.category, item.rank, item.headline, item.fingerprint,
                     json.dumps(sorted({s.url for s in item.sources})),
                     json.dumps(sorted(set(terms(item.headline, item.language))))))
            self.conn.execute(
                "INSERT OR REPLACE INTO reports(report_date, generated_at, word_count, story_count, files, stats)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (day, _iso(briefing.generated_at), briefing.word_count, len(briefing.all_items()),
                 json.dumps([str(f) for f in files]), json.dumps(briefing.stats, default=str)))

    # ------------------------------------------------------------ Quellen
    def update_source_status(self, results: list, now: _dt.datetime) -> None:
        if self.readonly:
            return
        stamp = _iso(now)
        with self.conn:
            for result in results:
                row = self.conn.execute("SELECT consecutive_failures FROM source_status WHERE source_id = ?",
                                        (result.source_id,)).fetchone()
                failures = 0 if result.ok else (row["consecutive_failures"] if row else 0) + 1
                result.consecutive_failures = failures
                self.conn.execute(
                    "INSERT INTO source_status(source_id, last_attempt, last_success, consecutive_failures,"
                    " last_error, last_items) VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(source_id) DO UPDATE SET last_attempt = excluded.last_attempt,"
                    " last_success = COALESCE(excluded.last_success, source_status.last_success),"
                    " consecutive_failures = excluded.consecutive_failures, last_error = excluded.last_error,"
                    " last_items = excluded.last_items",
                    (result.source_id, stamp, stamp if result.ok else None, failures,
                     None if result.ok else result.error[:500], result.items))

    def source_status(self) -> dict:
        rows = self.conn.execute("SELECT * FROM source_status").fetchall()
        return {r["source_id"]: dict(r) for r in rows}

    # ------------------------------------------------------------ Cache
    def cached_summary(self, key: str) -> Optional[dict]:
        row = self.conn.execute("SELECT payload FROM summary_cache WHERE cache_key = ?", (key,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def store_summary(self, key: str, payload: dict, now: _dt.datetime) -> None:
        if self.readonly:
            return
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO summary_cache(cache_key, created_at, payload) VALUES (?, ?, ?)",
                              (key, _iso(now), json.dumps(payload, ensure_ascii=False)))

    # ------------------------------------------------------------ Aufräumen
    def purge(self, now: _dt.datetime, retention_days: int) -> dict:
        if self.readonly:
            return {}
        cutoff = now - _dt.timedelta(days=retention_days)
        counts = {}
        with self.conn:
            counts["artikel"] = self.conn.execute("DELETE FROM articles WHERE last_seen_at < ?",
                                                  (_iso(cutoff),)).rowcount
            counts["berichte"] = self.conn.execute("DELETE FROM reports WHERE report_date < ?",
                                                   (cutoff.date().isoformat(),)).rowcount
            self.conn.execute("DELETE FROM report_stories WHERE report_date < ?", (cutoff.date().isoformat(),))
            counts["cache"] = self.conn.execute("DELETE FROM summary_cache WHERE created_at < ?",
                                                (_iso(cutoff),)).rowcount
        return counts

    def stats(self) -> dict:
        return {table: self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("articles", "reports", "report_stories", "source_status", "summary_cache")}


class NullStorage:
    """Platzhalter ohne Datenbank."""

    enabled = False
    path = None

    def close(self) -> None:
        pass

    def remember_articles(self, articles: list, now: _dt.datetime) -> None:
        for article in articles:
            if article.published is None:
                article.published, article.date_estimated = now, True

    def update_article_details(self, articles: list) -> None:
        pass

    def history(self, report_date: _dt.date, days: int) -> "History":
        return History([])

    def save_report(self, briefing: Briefing, files: list) -> None:
        pass

    def update_source_status(self, results: list, now: _dt.datetime) -> None:
        pass

    def source_status(self) -> dict:
        return {}

    def cached_summary(self, key: str) -> Optional[dict]:
        return None

    def store_summary(self, key: str, payload: dict, now: _dt.datetime) -> None:
        pass

    def purge(self, now: _dt.datetime, retention_days: int) -> dict:
        return {}

    def stats(self) -> dict:
        return {}


class History:
    """Bereits berichtete Nachrichten der Vortage (für Wiederholungs-/Fortsetzungserkennung)."""

    def __init__(self, entries: list):
        self.entries = entries  # (Datum, Überschrift, URLs, Begriffe)

    def __len__(self) -> int:
        return len(self.entries)

    def classify(self, story: Story) -> str:
        if not self.entries:
            return ""
        urls = set(story.urls)
        lead = story.lead
        story_terms = set(terms(f"{lead.title}", lead.language))
        result = ""
        for _day, _headline, old_urls, old_terms in self.entries:
            if urls and urls <= old_urls:
                return "wiederholung"
            if urls & old_urls:
                result = "fortsetzung"
                continue
            if story_terms and old_terms:
                overlap = len(story_terms & old_terms) / max(1, min(len(story_terms), len(old_terms)))
                if overlap >= 0.6 and len(story_terms & old_terms) >= 3:
                    result = "fortsetzung"
        return result


def open_storage(enabled: bool, path: Optional[Path], readonly: bool = False):
    if not enabled or path is None or (readonly and not Path(path).exists()):
        return NullStorage()
    try:
        return Storage(path, readonly=readonly)
    except sqlite3.Error as exc:
        log.warning("Datenbank %s nicht nutzbar (%s) – Lauf ohne Datenbank", path, exc)
        return NullStorage()


def make_cache_key(*parts) -> str:
    raw = "|".join(re.sub(r"\s+", " ", str(p)) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
