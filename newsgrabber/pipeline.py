"""Ablauf eines Laufs: Sammeln → Verarbeiten → Bewerten → Zusammenfassen → Bericht.

Auch als Python-API nutzbar::

    from newsgrabber.config import load_config
    from newsgrabber.pipeline import RunOptions, run

    result = run(load_config("newsgrabber.yaml"), RunOptions(date=datetime.date.today()))
    print(result.files)
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .collect import BrowserRenderer, CollectContext, HttpClient, collect_sources, fetch_fulltexts
from .config import Config
from .models import Briefing, Story
from .process.dedup import cluster_articles, merge_identical
from .rank import Classifier, score_stories
from .report import (Renderer, build_sections, count_markdown_words, fit_to_budget, output_paths, plan_lengths,
                     select_stories, write_atomic)
from .storage import open_storage
from .summarize import build_summarizer
from .timeutil import briefing_window, now as tz_now

log = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    """Lauf konnte keinen Bericht erzeugen."""


@dataclass
class RunOptions:
    date: _dt.date
    hours: Optional[float] = None
    output_dir: Optional[Path] = None
    formats: Optional[list] = None
    use_database: bool = True
    fulltext: Optional[bool] = None
    backend: Optional[str] = None
    max_words: Optional[int] = None
    min_words: Optional[int] = None
    source_ids: Optional[list] = None
    preview: bool = False  # nichts schreiben, Datenbank nicht verändern
    current_time: Optional[_dt.datetime] = None  # für Tests


@dataclass
class RunResult:
    briefing: Briefing
    files: list = field(default_factory=list)
    preview: dict = field(default_factory=dict)  # Format -> Text (nur bei preview)

    @property
    def has_warnings(self) -> bool:
        return bool(self.briefing.failed_sources) or bool(self.briefing.notes)


def apply_overrides(config: Config, options: RunOptions) -> Config:
    config = dataclasses.replace(config)
    if options.hours:
        config.window = dataclasses.replace(config.window, hours=options.hours)
    report = config.report
    if options.formats or options.max_words or options.min_words:
        report = dataclasses.replace(
            report, formats=options.formats or report.formats,
            max_words=options.max_words or report.max_words,
            min_words=min(options.min_words or report.min_words, options.max_words or report.max_words))
        config.report = report
    if options.fulltext is not None:
        fulltext = dataclasses.replace(config.collection.fulltext, enabled=options.fulltext)
        config.collection = dataclasses.replace(config.collection, fulltext=fulltext)
    return config


def run(config: Config, options: RunOptions) -> RunResult:
    started = time.monotonic()
    config = apply_overrides(config, options)
    tz = config.tz
    current = (options.current_time or tz_now(tz)).astimezone(tz)
    window_start, window_end = briefing_window(options.date, tz, config.window.hours, config.end_time, current)
    log.info("Briefing für %s – Zeitfenster %s bis %s", options.date.isoformat(),
             window_start.strftime("%d.%m. %H:%M"), window_end.strftime("%d.%m. %H:%M"))

    sources = config.enabled_sources()
    if options.source_ids:
        wanted = set(options.source_ids)
        unknown = wanted - {s.id for s in config.sources}
        if unknown:
            raise PipelineError(f"Unbekannte Quelle(n): {', '.join(sorted(unknown))}")
        sources = [s for s in config.sources if s.id in wanted]
    if not sources:
        raise PipelineError("Keine aktive Quelle konfiguriert")

    use_db = options.use_database and config.database.enabled
    storage = open_storage(use_db, config.resolve(config.database.path), readonly=options.preview)
    http = HttpClient(config.collection)
    browser = BrowserRenderer(config.collection.playwright, http, config.collection.user_agent) \
        if config.collection.playwright.enabled else None
    ctx = CollectContext(config=config, http=http, browser=browser)
    try:
        # ---------------------------------------------------- 1. Sammeln
        articles, results = collect_sources(sources, ctx)
        storage.update_source_status(results, current)
        ok = [r for r in results if r.ok]
        log.info("%d von %d Quellen erreichbar, %d Einträge", len(ok), len(results), len(articles))
        if not articles:
            raise PipelineError("Keine Quelle hat Meldungen geliefert – Netzwerk und Quellen prüfen "
                                "(python -m newsgrabber quellen --testen)")

        # ---------------------------------------------------- 2. Verarbeiten
        articles = merge_identical(articles)
        storage.remember_articles(articles, current)
        articles = filter_window(articles, window_start, window_end, current)
        log.info("%d Meldungen im Zeitfenster", len(articles))
        if not articles:
            raise PipelineError("Keine Meldungen im Zeitfenster – ggf. --stunden erhöhen")
        classifier = Classifier(config)
        for article in articles:
            classifier.assign_category(article)
        history = storage.history(options.date, config.scoring.history_days)
        stories, vectorizer = _cluster_and_score(articles, config, window_end, classifier, history)

        # ---------------------------------------------------- 3. Volltexte für die Kandidaten
        fulltext_stats = {}
        if config.collection.fulltext.enabled:
            candidates = fulltext_candidates(stories, articles, config)
            if candidates:
                log.info("Volltext für %d Artikel abrufen", len(candidates))
                fulltext_stats = fetch_fulltexts(candidates, ctx)
                storage.update_article_details(candidates)
                articles = filter_window(articles, window_start, window_end, current)
                for article in candidates:
                    article.category = ""  # mit Volltext neu zuordnen
                    classifier.assign_category(article)
                stories, vectorizer = _cluster_and_score(articles, config, window_end, classifier, history)

        # ---------------------------------------------------- 4. Auswahl und Zusammenfassung
        selection = select_stories(stories, config)
        plan = plan_lengths(selection, config)
        jobs = [(story, plan[id(story)]) for chosen in selection.values() for story in chosen]
        summarizer = build_summarizer(config, vectorizer.df, storage, current, ctx.boilerplate, options.backend)
        summaries = dict(zip([id(story) for story, _ in jobs], summarizer.summarize_all(jobs)))

        # ---------------------------------------------------- 5. Bericht
        briefing = Briefing(date=options.date, title=config.report.title, generated_at=current,
                            window_start=window_start, window_end=window_end, source_results=results)
        briefing.sections = build_sections(selection, summaries, config)
        renderer = Renderer(config)
        fit_to_budget(briefing, config, lambda b: count_markdown_words(renderer.markdown(b)))
        selected = briefing.all_items()
        briefing.stats = {
            "sources_total": len(results),
            "sources_ok": len(ok),
            "sources_failed": len(results) - len(ok),
            "sources_used": len({s.name for item in selected for s in item.sources}),
            "articles_in_window": len(articles),
            "stories_total": len(stories),
            "stories_selected": len(selected),
            "fulltext": fulltext_stats,
            "summarizer": getattr(summarizer, "name", "?"),
            "http_requests": http.request_count,
            "rendered_pages": browser.pages_rendered if browser else 0,
            "duration_seconds": 0,
        }
        _add_notes(briefing, config)
        briefing.stats["duration_seconds"] = round(time.monotonic() - started, 1)
        briefing.word_count = count_markdown_words(renderer.markdown(briefing))

        result = RunResult(briefing=briefing)
        if options.preview:
            result.preview = {fmt: renderer.render(briefing, fmt) for fmt in config.report.formats}
            return result
        paths = output_paths(config, options.date, options.output_dir)
        for fmt, path in paths.items():
            write_atomic(path, renderer.render(briefing, fmt))
            result.files.append(path)
        storage.save_report(briefing, result.files)
        if config.database.retention_days > 0:
            storage.purge(current, config.database.retention_days)
        log.info("Bericht mit %d Meldungen und ca. %d Wörtern erstellt (%.1f s)",
                 len(selected), briefing.word_count, time.monotonic() - started)
        return result
    finally:
        if browser is not None:
            browser.close()
        storage.close()


def _cluster_and_score(articles: list, config: Config, reference: _dt.datetime, classifier: Classifier, history):
    stories, vectorizer = cluster_articles(articles, config.processing.dedup_threshold)
    score_stories(stories, config, reference, classifier, history)
    stories.sort(key=lambda s: -s.score)
    merged = sum(1 for s in stories if len(s.articles) > 1)
    log.info("%d Nachrichten (%d aus mehreren Quellen zusammengeführt)", len(stories), merged)
    return stories, vectorizer


def filter_window(articles: list, start: _dt.datetime, end: _dt.datetime, current: _dt.datetime) -> list:
    """Meldungen im Zeitfenster; offensichtlich falsche Zukunftsdaten werden auf „jetzt“ gesetzt."""
    kept = []
    tolerance = _dt.timedelta(minutes=15)
    for article in articles:
        published = article.published
        if published is not None and published > current + _dt.timedelta(hours=2):
            log.debug("Datum in der Zukunft (%s) bei %s – als unbekannt behandelt", published, article.url)
            article.published, article.date_estimated = current, True
            published = current
        if published is None or start <= published <= end + tolerance:
            kept.append(article)
    return kept


def fulltext_candidates(stories: list, articles: list, config: Config) -> list:
    """Artikel, deren Volltext abgerufen wird: die besten Nachrichten je Kategorie (max. 2 Artikel je
    Nachricht) sowie Einträge aus HTML-Quellen, deren Datum erst im Artikel steht."""
    fc = config.collection.fulltext
    chosen, seen = [], set()

    def add(article):
        if id(article) not in seen and article.fetch_fulltext and not article.fulltext_status:
            seen.add(id(article))
            chosen.append(article)

    for article in articles:
        if article.fetch_priority:
            add(article)
    per_category = {c.key: [s for s in stories if s.category == c.key][:fc.candidates_per_category]
                    for c in config.categories}
    ranked = sorted((s for group in per_category.values() for s in group), key=lambda s: -s.score)
    for story in ranked:
        for article in sorted(story.articles, key=lambda a: -a.quality)[:2]:
            add(article)
    return chosen[:fc.max_articles]


def _add_notes(briefing: Briefing, config: Config) -> None:
    failing = [r for r in briefing.source_results if not r.ok and r.consecutive_failures >= 3]
    for result in failing:
        briefing.notes.append(f"{result.name} ist seit {result.consecutive_failures} Läufen nicht erreichbar "
                              f"– URL prüfen ({result.error[:120]})")
    if briefing.stats["sources_failed"] and briefing.stats["sources_failed"] >= briefing.stats["sources_total"] / 2:
        briefing.notes.append("Mehr als die Hälfte der Quellen war nicht erreichbar; das Briefing ist unvollständig.")
    if briefing.all_items() and briefing.word_count < config.report.min_words:
        briefing.notes.append("Weniger Meldungen als üblich – das Briefing ist kürzer als der Zielumfang.")


def story_label(story: Story) -> str:
    return f"[{story.score:.2f}] {story.lead.title} ({', '.join(story.source_names)})"
