"""Ausgabe des Briefings als Markdown, HTML und JSON (Jinja2-Vorlagen, austauschbar)."""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined

from .. import __version__
from ..config import Config
from ..models import Briefing
from ..timeutil import format_datetime, format_long_date, format_relative

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
JSON_SCHEMA_VERSION = 1
EXTENSIONS = {"markdown": ".md", "html": ".html", "json": ".json"}

HISTORY_LABELS = {"fortsetzung": "Fortsetzung", "wiederholung": "bereits berichtet"}
LANGUAGE_LABELS = {"en": "Englisch", "de": "Deutsch", "fr": "Französisch"}

METHOD_NOTES = {
    "extraktiv": "Zusammenfassungen automatisch aus kurzen Auszügen der genannten Quellen erstellt.",
    "claude": "Zusammenfassungen in eigenen Worten mit Claude (Anthropic) erstellt.",
}


class Renderer:
    def __init__(self, config: Config):
        self.config = config
        self.tz = config.tz
        loaders = []
        custom = config.resolve(config.report.templates_dir)
        if custom:
            loaders.append(FileSystemLoader(str(custom)))
        loaders.append(FileSystemLoader(str(TEMPLATE_DIR)))
        self.md_env = self._env(ChoiceLoader(loaders), autoescape=False)
        self.html_env = self._env(ChoiceLoader(loaders), autoescape=True)

    def _env(self, loader, autoescape: bool) -> Environment:
        env = Environment(loader=loader, autoescape=autoescape, trim_blocks=True, lstrip_blocks=True,
                          keep_trailing_newline=True, undefined=StrictUndefined)
        env.filters["dt"] = lambda v: format_datetime(v, self.tz)
        env.filters["longdate"] = format_long_date
        env.filters["md"] = md_escape
        env.filters["thousands"] = lambda n: f"{int(n):,}".replace(",", ".")
        return env

    def _context(self, briefing: Briefing) -> dict:
        methods = sorted({item.method for item in briefing.all_items()})
        rel = lambda v: format_relative(v, self.tz, briefing.window_end)  # noqa: E731

        def md_meta(item) -> str:
            parts = [md_escape(item.topic)] if item.topic else []
            parts.append(rel(item.published))
            parts += [HISTORY_LABELS[item.history]] if item.history in HISTORY_LABELS else []
            if item.language != "de":
                parts.append(f"Quelle auf {LANGUAGE_LABELS.get(item.language, item.language)}")
            line = f"*{' · '.join(parts)}*"
            if item.keywords:
                line += " · **Schlagwörter:** " + ", ".join(md_escape(k) for k in item.keywords)
            return line

        def md_sources(item) -> str:
            return " · ".join(f"[{md_escape(s.name)}]({s.url})" + (f" ({rel(s.published)})" if s.published else "")
                              for s in item.sources)

        failed = ", ".join(r.name + (f" (seit {r.consecutive_failures} Läufen)" if r.consecutive_failures > 1 else "")
                           for r in briefing.failed_sources)
        return {
            "b": briefing,
            "labels": {c.key: c.title for c in self.config.categories},
            "rel": rel,
            "md_meta": md_meta,
            "md_sources": md_sources,
            "failed_sources_text": failed,
            "history_labels": HISTORY_LABELS,
            "language_labels": LANGUAGE_LABELS,
            "show_scores": self.config.report.show_scores,
            "show_source_status": self.config.report.show_source_status,
            "method_notes": [METHOD_NOTES.get(m, m) for m in methods],
            "version": __version__,
            "generated": format_datetime(briefing.generated_at, self.tz),
        }

    def markdown(self, briefing: Briefing) -> str:
        text = self.md_env.get_template("briefing.md.j2").render(**self._context(briefing))
        return re.sub(r"\n{3,}", "\n\n", text)

    def html(self, briefing: Briefing) -> str:
        return self.html_env.get_template("briefing.html.j2").render(**self._context(briefing))

    def json(self, briefing: Briefing) -> str:
        return json.dumps(briefing_to_dict(briefing, self.config), ensure_ascii=False, indent=2) + "\n"

    def render(self, briefing: Briefing, fmt: str) -> str:
        return {"markdown": self.markdown, "html": self.html, "json": self.json}[fmt](briefing)


def md_escape(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"([\\`*_\[\]<>|])", r"\\\1", text)
    return re.sub(r"^([#+-]|\d+\.)(\s)", r"\\\1\2", text)


def _iso(value, tz):
    return value.astimezone(tz).isoformat(timespec="seconds") if isinstance(value, _dt.datetime) else value


def briefing_to_dict(briefing: Briefing, config: Config) -> dict:
    tz = config.tz
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "generator": f"NewsGrabber {__version__}",
        "title": briefing.title,
        "date": briefing.date.isoformat(),
        "timezone": config.timezone,
        "generated_at": _iso(briefing.generated_at, tz),
        "window": {"start": _iso(briefing.window_start, tz), "end": _iso(briefing.window_end, tz)},
        "word_count": briefing.word_count,
        "stats": briefing.stats,
        "notes": briefing.notes,
        "top_stories": [item.anchor for item in briefing.top_items],
        "categories": [{
            "key": section.key,
            "title": section.title,
            "note": section.note,
            "items": [{
                "id": item.anchor,
                "rank": item.rank,
                "headline": item.headline,
                "summary": item.summary,
                "facts": item.facts,
                "keywords": item.keywords,
                "topic": item.topic,
                "published": _iso(item.published, tz),
                "history": item.history,
                "summary_method": item.method,
                "language": item.language,
                "score": item.score,
                "score_parts": item.score_parts,
                "fingerprint": item.fingerprint,
                "sources": [{"name": s.name, "url": s.url, "published": _iso(s.published, tz)}
                            for s in item.sources],
            } for item in section.items],
        } for section in briefing.sections],
        "sources": [{
            "id": r.source_id, "name": r.name, "ok": r.ok, "items": r.items, "error": r.error,
            "used_fallback": r.used_fallback, "consecutive_failures": r.consecutive_failures,
            "duration_seconds": round(r.duration, 2),
        } for r in briefing.source_results],
    }


def output_paths(config: Config, report_date: _dt.date, output_dir=None) -> dict:
    root = Path(output_dir) if output_dir else config.resolve(config.report.output_dir)
    folder = root / config.report.folder.format(date=report_date)
    stem = config.report.filename.format(date=report_date)
    return {fmt: folder / f"{stem}{EXTENSIONS[fmt]}" for fmt in config.report.formats}


def write_atomic(path: Path, text: str) -> None:
    """Schreibt erst in eine temporäre Datei und ersetzt dann – keine halbfertigen Berichte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
