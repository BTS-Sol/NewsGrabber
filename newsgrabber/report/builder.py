"""Auswahl der Nachrichten je Kategorie, Längenplanung und automatisches Kürzen auf das Wortbudget."""

from __future__ import annotations

import datetime as _dt
import logging
import re
from collections import Counter

from ..config import Config
from ..models import Briefing, BriefingItem, CategorySection, SourceLink, Story
from ..process.text import split_sentences, word_count

log = logging.getLogger(__name__)

# geschätzter Umfang je Meldung außerhalb der Zusammenfassung (Überschrift, Schlagwörter, Quellenzeile)
ITEM_OVERHEAD_WORDS = 30
FIXED_OVERHEAD_WORDS = 90


def select_stories(stories: list, config: Config) -> dict:
    """Priorisierte Auswahl je Kategorie (mit Themenvielfalt innerhalb einer Kategorie)."""
    min_score = config.scoring.min_score
    diversity = config.scoring.topic_diversity
    selection = {}
    for category in config.categories:
        # Meldungen ganz ohne Text (nur Überschrift) kommen nicht ins Briefing
        pool = sorted((s for s in stories if s.category == category.key and any(a.text for a in s.articles)),
                      key=lambda s: -s.score)
        chosen, topics = [], Counter()
        while pool and len(chosen) < category.max_items:
            best = max(pool, key=lambda s: s.score * (diversity ** topics[s.topic] if s.topic else 1.0))
            if best.score < min_score and len(chosen) >= category.min_items:
                break
            chosen.append(best)
            pool.remove(best)
            topics[best.topic] += 1
        selection[category.key] = chosen
    return selection


def plan_lengths(selection: dict, config: Config) -> dict:
    """Zielwortzahl der Zusammenfassung je Nachricht, abgeleitet aus dem Wortbudget."""
    report = config.report
    target_total = (report.min_words + report.max_words) / 2
    items = [(key, story) for key, stories in selection.items() for story in stories]
    if not items:
        return {}
    available = target_total - FIXED_OVERHEAD_WORDS - report.top_stories * 12 - len(items) * ITEM_OVERHEAD_WORDS
    weights = {c.key: c.weight for c in config.categories}
    total_weight = sum(weights[key] for key, _story in items) or 1.0
    plan = {}
    for key, story in items:
        # Zusammenfassung ~ 70 % des Anteils, Fakten ~ 30 %
        share = available * weights[key] / total_weight * 0.7
        plan[id(story)] = int(max(40, min(150, share)))
    return plan


def make_item(story: Story, summary, rank: int, category_title: str) -> BriefingItem:
    sources, seen = [], set()
    for article in sorted(story.articles, key=lambda a: (-a.quality, a.published or _dt.datetime.max.replace(
            tzinfo=_dt.timezone.utc))):
        if article.url in seen:
            continue
        seen.add(article.url)
        sources.append(SourceLink(name=article.source_name, url=article.url, published=article.published))
    keywords = list(dict.fromkeys(summary.keywords))[:6]
    return BriefingItem(
        rank=rank, headline=summary.headline, summary=summary.summary, facts=list(summary.facts),
        keywords=keywords, sources=sources[:5], published=story.published, category=story.category,
        topic=story.topic, score=story.score, score_parts=dict(story.score_parts), history=story.history,
        method=summary.method, language=summary.language, fingerprint=story.fingerprint(),
        anchor=f"{story.category}-{rank}",
    )


def build_sections(selection: dict, summaries: dict, config: Config) -> list:
    sections = []
    for category in config.categories:
        stories = selection.get(category.key, [])
        items = [make_item(story, summaries[id(story)], rank, category.title)
                 for rank, story in enumerate(stories, start=1)]
        note = "" if items else "Keine aktuellen Meldungen in dieser Kategorie."
        sections.append(CategorySection(key=category.key, title=category.title, items=items, note=note))
    return sections


def choose_top_items(sections: list, count: int) -> list:
    items = [item for section in sections for item in section.items if item.history != "wiederholung"]
    return sorted(items, key=lambda i: -i.score)[:count]


# ------------------------------------------------------------------ Wortbudget
def fit_to_budget(briefing: Briefing, config: Config, count_words) -> None:
    """Kürzt den Bericht, bis ``count_words(briefing)`` höchstens ``report.max_words`` ergibt.

    Reihenfolge: (1) Meldungen mit der niedrigsten Bewertung entfernen, solange das
    Minimum je Kategorie und ``min_words`` gewahrt bleiben, (2) Fakten und letzte
    Sätze der am niedrigsten bewerteten Meldungen kürzen, (3) notfalls weitere
    Meldungen entfernen (mindestens eine je Kategorie bleibt).
    """
    report = config.report
    minimum = {c.key: c.min_items for c in config.categories}

    def refresh() -> int:
        briefing.top_items = choose_top_items(briefing.sections, report.top_stories)
        briefing.word_count = count_words(briefing)
        return briefing.word_count

    words = refresh()
    if words <= report.max_words:
        return
    removed = trimmed = 0

    # (1) ganze Meldungen entfernen, solange min_words gehalten wird
    while words > report.max_words:
        candidate = _lowest(briefing, lambda s: len(s.items) > minimum.get(s.key, 0))
        if candidate is None:
            break
        section, item = candidate
        section.items.remove(item)
        after = refresh()
        if after < report.min_words:
            section.items.append(item)
            section.items.sort(key=lambda i: i.rank)
            words = refresh()
            break
        words, removed = after, removed + 1

    # (2) Fakten und Sätze kürzen
    while words > report.max_words and _trim_once(briefing):
        trimmed += 1
        words = refresh()

    # (3) letzte Möglichkeit: weitere Meldungen entfernen
    while words > report.max_words:
        candidate = _lowest(briefing, lambda s: len(s.items) > 1)
        if candidate is None:
            break
        candidate[0].items.remove(candidate[1])
        removed += 1
        words = refresh()

    for section in briefing.sections:
        for rank, item in enumerate(section.items, start=1):
            item.rank, item.anchor = rank, f"{section.key}-{rank}"
    refresh()
    log.info("Bericht auf %d Wörter gekürzt (%d Meldung(en) entfernt, %d Kürzung(en))",
             briefing.word_count, removed, trimmed)


def _lowest(briefing: Briefing, allowed):
    candidates = [(section, item) for section in briefing.sections if allowed(section) for item in section.items]
    if not candidates:
        return None
    return min(candidates, key=lambda c: c[1].score)


def _trim_once(briefing: Briefing) -> bool:
    items = sorted(briefing.all_items(), key=lambda i: i.score)
    for item in items:  # zuerst überzählige Fakten
        if len(item.facts) > 1:
            item.facts.pop()
            return True
    for item in items:  # dann den letzten Satz längerer Zusammenfassungen
        sentences = split_sentences(item.summary)
        if len(sentences) > 1:
            item.summary = " ".join(sentences[:-1])
            return True
    for item in items:
        if item.facts:
            item.facts.pop()
            return True
    return False


def count_markdown_words(markdown: str) -> int:
    """Wörter im Bericht ohne URLs und Markdown-Syntax."""
    text = re.sub(r"\]\([^)]*\)", "]", markdown)
    text = re.sub(r"https?://\S+", "", text)
    return word_count(text)
