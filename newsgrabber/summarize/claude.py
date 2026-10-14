"""Zusammenfassungen in eigenen Worten über die Claude-API (optional).

Pro Nachricht eine Anfrage mit strukturierter JSON-Ausgabe (Überschrift,
Zusammenfassung, Fakten, Schlagwörter). Englische Quellen werden dabei ins Deutsche
übertragen. Schlägt eine Anfrage fehl, wird für diese Nachricht das extraktive
Verfahren verwendet. Ergebnisse werden in der Datenbank zwischengespeichert, damit
ein erneuter Lauf am selben Tag keine Kosten verursacht.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from ..config import ClaudeConfig
from ..models import Story, StorySummary
from ..process.text import collapse, truncate_words, word_count
from ..storage import make_cache_key
from ..timeutil import format_datetime
from .base import SummaryError

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

LANGUAGE_NAMES = {"de": "Deutsch", "en": "Englisch"}

SYSTEM_PROMPT = """Du bist Nachrichtenredakteur und schreibst ein sachliches Morgenbriefing.

Du erhältst zu einer Nachricht Auszüge aus einem oder mehreren Quellenartikeln. Schreibe daraus eine eigene,
verständliche Zusammenfassung in {language}:

- Formuliere vollständig in eigenen Worten. Übernimm keine Sätze aus den Quellen; kurze wörtliche Zitate nur,
  wenn sie selbst die Nachricht sind, und dann mit Nennung der Person.
- Verwende ausschließlich Informationen aus den Quellen. Keine Vermutungen, keine Bewertungen, keine Ergänzungen
  aus eigenem Wissen. Widersprechen sich Quellen, nenne beide Angaben.
- Die Zusammenfassung beantwortet zuerst: Was ist passiert, wer ist beteiligt, wann und wo? Danach Hintergrund
  oder Folgen, soweit in den Quellen enthalten.
- Fakten sind kurze, eigenständige Aussagen mit konkreten Angaben (Zahlen, Daten, Namen, Orte), die nicht
  schon in der Zusammenfassung stehen.
- Überschrift: sachlich, höchstens zwölf Wörter, kein Clickbait.
- Schlagwörter: 3 bis 6 aussagekräftige Begriffe (Personen, Organisationen, Orte, Themen).
- Zeitangaben relativ zum Berichtsdatum sinnvoll formulieren (z. B. „am Dienstag“) und Zeitzone Europe/Berlin
  annehmen."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "Überschrift, höchstens zwölf Wörter"},
        "summary": {"type": "string", "description": "Zusammenfassung als Fließtext"},
        "facts": {"type": "array", "items": {"type": "string"}, "description": "2 bis 4 Kernfakten"},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "3 bis 6 Schlagwörter"},
    },
    "required": ["headline", "summary", "facts", "keywords"],
    "additionalProperties": False,
}


def claude_available(config: ClaudeConfig) -> bool:
    """Für ``backend: auto``: SDK installiert und API-Schlüssel gesetzt?"""
    import importlib.util

    return importlib.util.find_spec("anthropic") is not None and bool(os.environ.get(config.api_key_env))


class ClaudeSummarizer:
    name = "claude"

    def __init__(self, config: ClaudeConfig, fallback, storage, now, language: str = "de", tz=None):
        try:
            import anthropic
        except ImportError as exc:
            raise SummaryError("Das Paket 'anthropic' ist nicht installiert (pip install anthropic)") from exc
        self._anthropic = anthropic
        self.config = config
        self.fallback = fallback
        self.storage = storage
        self.now = now
        self.language = language
        self.tz = tz
        key = os.environ.get(config.api_key_env) or None
        self.client = anthropic.Anthropic(api_key=key, timeout=config.timeout, max_retries=3)
        self._disabled_reason = ""
        self.calls = 0
        self.cache_hits = 0
        self.failures = 0

    # ------------------------------------------------------------------
    def summarize_all(self, jobs: list) -> list:
        results: list = [None] * len(jobs)
        pending = []
        for index, (story, target) in enumerate(jobs):
            cached = self.storage.cached_summary(self._cache_key(story, target))
            if cached:
                self.cache_hits += 1
                results[index] = StorySummary(**cached)
            else:
                pending.append(index)
        if pending:
            workers = max(1, self.config.max_workers)
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="claude") as pool:
                futures = {index: pool.submit(self._summarize_one, *jobs[index]) for index in pending}
                for index, future in futures.items():
                    story, target = jobs[index]
                    try:
                        summary = future.result()
                    except SummaryError as exc:
                        self.failures += 1
                        log.warning("Claude-Zusammenfassung fehlgeschlagen (%s) – extraktiv: %s", exc,
                                    story.lead.title[:80])
                        summary = self.fallback.summarize(story, target)
                    else:
                        self.storage.store_summary(self._cache_key(story, target), summary.__dict__, self.now)
                    results[index] = summary
        if self.calls or self.cache_hits:
            log.info("Claude: %d Anfrage(n), %d aus dem Cache, %d Fehler", self.calls, self.cache_hits, self.failures)
        return results

    def _cache_key(self, story: Story, target: int) -> str:
        return make_cache_key("claude", self.config.model, self.config.effort, self.language, target // 20,
                              story.fingerprint())

    def _summarize_one(self, story: Story, target: int) -> StorySummary:
        if self._disabled_reason:
            raise SummaryError(self._disabled_reason)
        prompt = self._prompt(story, target)
        anthropic = self._anthropic
        kwargs = dict(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=SYSTEM_PROMPT.format(language=LANGUAGE_NAMES.get(self.language, self.language)),
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.config.effort,
                           "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        try:
            self.calls += 1
            if self.config.fallbacks:
                response = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                response = self.client.messages.create(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            self._disabled_reason = f"Zugriff verweigert ({exc.status_code}) – API-Schlüssel prüfen"
            raise SummaryError(self._disabled_reason) from exc
        except anthropic.NotFoundError as exc:
            self._disabled_reason = f"Modell '{self.config.model}' nicht gefunden"
            raise SummaryError(self._disabled_reason) from exc
        except anthropic.BadRequestError as exc:
            raise SummaryError(f"ungültige Anfrage: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise SummaryError("Rate Limit erreicht") from exc
        except anthropic.APIStatusError as exc:
            raise SummaryError(f"API-Fehler {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise SummaryError(f"Verbindungsfehler: {exc}") from exc

        if response.stop_reason == "refusal":
            raise SummaryError("Anfrage abgelehnt (refusal)")
        if response.stop_reason == "max_tokens":
            raise SummaryError("Antwort abgeschnitten (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SummaryError("Antwort ist kein gültiges JSON") from exc
        return self._to_summary(data, target)

    def _to_summary(self, data: dict, target: int) -> StorySummary:
        headline = collapse(str(data.get("headline", "")))
        summary = collapse(str(data.get("summary", "")))
        if not headline or not summary:
            raise SummaryError("Überschrift oder Zusammenfassung fehlt")
        if word_count(summary) > target * 1.6:
            summary = truncate_words(summary, int(target * 1.4))
        facts = [collapse(str(f)) for f in data.get("facts", []) if str(f).strip()][:4]
        keywords = [collapse(str(k)) for k in data.get("keywords", []) if str(k).strip()][:6]
        return StorySummary(headline=headline, summary=summary, facts=facts, keywords=keywords,
                            method=self.name, language=self.language)

    def _prompt(self, story: Story, target: int) -> str:
        articles = sorted(story.articles, key=lambda a: (-a.quality, -len(a.text)))[:5]
        budget = self.config.max_input_words
        parts = []
        for number, article in enumerate(articles, start=1):
            share = budget // 2 if number == 1 else budget // (2 * max(1, len(articles) - 1))
            body = truncate_words(collapse(article.text), max(60, share), ellipsis=" […]")
            published = format_datetime(article.published, self.tz) if self.tz and article.published else "unbekannt"
            parts.append(
                f'<quelle nr="{number}" name="{article.source_name}" veroeffentlicht="{published}" '
                f'sprache="{article.language}">\n<titel>{article.title}</titel>\n'
                + (f"<dachzeile>{article.kicker}</dachzeile>\n" if article.kicker else "")
                + f"<text>{body}</text>\n</quelle>")
        facts = 2 if target < 70 else 3 if target < 120 else 4
        date = self.now.astimezone(self.tz).strftime("%d.%m.%Y") if self.tz else ""
        return (f"Berichtsdatum: {date}\n"
                f"Länge der Zusammenfassung: etwa {target} Wörter. Anzahl Fakten: {facts}.\n\n"
                "<quellen>\n" + "\n".join(parts) + "\n</quellen>")
