"""Zusammenfassung: extraktiv (Standard, offline) oder per Claude-API (optional).

``summarizer.backend``:

* ``extractive`` – immer extraktiv
* ``claude``     – Claude-API (Fehler einzelner Nachrichten -> extraktiv)
* ``auto``       – Claude, wenn das Paket ``anthropic`` installiert und der API-Schlüssel gesetzt ist
"""

from __future__ import annotations

import logging

from ..config import Config
from .base import Summarizer, SummaryError
from .claude import ClaudeSummarizer, claude_available
from .extractive import ExtractiveSummarizer

log = logging.getLogger(__name__)

__all__ = ["build_summarizer", "ExtractiveSummarizer", "ClaudeSummarizer", "Summarizer", "SummaryError"]


def build_summarizer(config: Config, df, storage, now, boilerplate=None, backend: str = None):
    sc = config.summarizer
    backend = backend or sc.backend
    extractive = ExtractiveSummarizer(language=sc.language, max_words_per_source=sc.max_words_per_source,
                                      max_sentences=config.processing.max_sentences, df=df, boilerplate=boilerplate)
    use_claude = backend == "claude" or (backend == "auto" and claude_available(sc.claude))
    if backend == "auto" and not use_claude:
        log.info("Zusammenfassung: extraktiv (für Claude: pip install anthropic und %s setzen)", sc.claude.api_key_env)
    if not use_claude:
        return extractive
    try:
        summarizer = ClaudeSummarizer(sc.claude, extractive, storage, now, language=sc.language, tz=config.tz)
    except SummaryError as exc:
        log.warning("%s – verwende extraktive Zusammenfassung", exc)
        return extractive
    log.info("Zusammenfassung: Claude (%s, effort %s)", sc.claude.model, sc.claude.effort)
    return summarizer
