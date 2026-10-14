"""Schnittstelle der Zusammenfassungs-Verfahren."""

from __future__ import annotations

from typing import Protocol

from ..models import Story, StorySummary


class Summarizer(Protocol):
    name: str

    def summarize_all(self, jobs: list) -> list:
        """``jobs``: Liste von (Story, Zielwortzahl). Ergebnis: Liste von StorySummary in gleicher Reihenfolge."""
        ...


class SummaryError(Exception):
    """Zusammenfassung für eine Nachricht nicht möglich."""


def preferred_articles(story: Story, language: str) -> list:
    """Artikel in der Berichtssprache mit Text – sonst alle Artikel mit Text, sortiert nach Qualität."""
    with_text = [a for a in story.articles if a.text]
    preferred = [a for a in with_text if a.language == language]
    chosen = preferred or with_text or list(story.articles)
    return sorted(chosen, key=lambda a: (-a.quality, -len(a.content), a.feed_rank))


def fallback_summary(story: Story, method: str = "extraktiv") -> StorySummary:
    lead = story.lead
    return StorySummary(headline=lead.title, summary=lead.teaser or "", method=method, language=lead.language)
