"""Bewertung der Nachrichten nach Aktualität, Relevanz, Quellenqualität und Verbreitung.

Gesamtwert = gewichtetes Mittel der Teilwerte (je 0…1), multipliziert mit Abschlägen
für Werbung/Ratgeber (Negativ-Schlagwörter), fehlendes Datum und bereits berichtete
Nachrichten. Alle Gewichte stehen in der Konfiguration unter ``scoring``.
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
from collections import Counter

from ..config import Config
from ..models import Article, Story
from ..process.keywords import KeywordMatcher
from ..timeutil import age_hours

log = logging.getLogger(__name__)


def relevance_text(article: Article, words: int = 300) -> str:
    return f"{article.kicker}\n{article.title}\n{article.teaser}\n{' '.join(article.content.split()[:words])}"


class Classifier:
    """Ordnet Artikel einer Kategorie und einem Thema zu (über konfigurierte Schlagwörter)."""

    def __init__(self, config: Config):
        self.config = config
        self.categories = {c.key: KeywordMatcher(c.keywords) for c in config.categories}
        self.topics = {c.key: {name: KeywordMatcher(words) for name, words in c.topics.items()}
                       for c in config.categories}
        self.negative = KeywordMatcher(config.scoring.negative_keywords)

    def keyword_hits(self, article: Article, category: str) -> float:
        """Gewichtete Anzahl verschiedener Kategorie-/Themen-Schlagwörter (Titel zählt doppelt)."""
        title = f"{article.kicker} {article.title}"
        matchers = [self.categories.get(category)] + list(self.topics.get(category, {}).values())
        body_hits, title_hits = set(), set()
        for matcher in matchers:
            if matcher:
                body_hits |= set(matcher.matches(relevance_text(article)))
                title_hits |= set(matcher.matches(title))
        return len(body_hits) + len(title_hits)

    def assign_category(self, article: Article) -> str:
        if article.category:
            return article.category
        candidates = list(article.categories) or [c.key for c in self.config.categories]
        if len(candidates) == 1:
            article.category = candidates[0]
            return article.category
        scores = {key: self.keyword_hits(article, key) for key in candidates}
        best = max(candidates, key=lambda k: (scores[k], -candidates.index(k)))
        article.category = best
        return best

    def story_category(self, story: Story) -> str:
        votes: Counter = Counter()
        for article in story.articles:
            votes[self.assign_category(article)] += article.quality + 0.5 * self.keyword_hits(article, article.category)
        order = [c.key for c in self.config.categories]
        return max(votes, key=lambda k: (votes[k], -order.index(k) if k in order else -99))

    def topic(self, story: Story) -> str:
        topics = self.topics.get(story.category) or {}
        if not topics:
            return ""
        scores: Counter = Counter()
        for article in story.articles:
            title = f"{article.kicker} {article.title}"
            text = relevance_text(article)
            for name, matcher in topics.items():
                scores[name] += 2 * len(matcher.matches(title)) + len(matcher.matches(text))
        if not scores or scores.most_common(1)[0][1] == 0:
            return ""
        return scores.most_common(1)[0][0]

    def is_negative(self, story: Story) -> bool:
        if not self.negative:
            return False
        lead = story.lead
        return bool(self.negative.matches(f"{lead.kicker} {lead.title} {lead.teaser[:300]}"))


def score_stories(stories: list, config: Config, reference: _dt.datetime, classifier: Classifier,
                  history=None) -> None:
    """Setzt ``category``, ``topic``, ``score`` und ``score_parts`` jeder Story (in place)."""
    sc = config.scoring
    weights = {k: float(sc.weights.get(k, 0.0)) for k in ("recency", "relevance", "quality", "coverage")}
    total_weight = sum(weights.values()) or 1.0
    for story in stories:
        story.category = classifier.story_category(story)
        story.topic = classifier.topic(story)
        parts = {
            "recency": _recency(story, reference, sc.recency_half_life_hours),
            "relevance": _relevance(story, classifier, config.collection.fulltext.min_chars),
            "quality": max(a.quality for a in story.articles),
            "coverage": _coverage(story, sc.coverage_saturation),
        }
        score = sum(weights[k] * parts[k] for k in weights) / total_weight
        factors = {}
        if all(a.date_estimated or a.published is None for a in story.articles):
            factors["ohne_datum"] = sc.undated_penalty
        if not any(a.text for a in story.articles):
            factors["ohne_text"] = sc.no_text_penalty
        if classifier.is_negative(story):
            factors["negativ"] = sc.negative_penalty
        if history is not None:
            story.history = history.classify(story)
            if story.history == "wiederholung":
                factors["wiederholung"] = sc.repeat_penalty
            elif story.history == "fortsetzung":
                factors["fortsetzung"] = sc.update_penalty
        for value in factors.values():
            score *= value
        story.score = round(score, 4)
        story.score_parts = {k: round(v, 3) for k, v in parts.items()}
        story.score_parts.update({f"faktor_{k}": v for k, v in factors.items()})


def _recency(story: Story, reference: _dt.datetime, half_life: float) -> float:
    age = age_hours(story.published, reference)
    if age is None:
        return 0.3
    return 0.5 ** (age / max(half_life, 0.1))


def _relevance(story: Story, classifier: Classifier, min_chars: int) -> float:
    hits = max(classifier.keyword_hits(a, story.category) for a in story.articles)
    keyword = 1 - math.exp(-hits / 2.5)
    # redaktionelle Gewichtung: Position im Feed (Startseiten/Top-Listen sind sortiert)
    prominence = max(1 - a.feed_rank / max(a.feed_size, 1) for a in story.articles)
    density = 1.0 if any(len(a.content) >= min_chars for a in story.articles) else \
        0.6 if any(len(a.teaser) >= 120 for a in story.articles) else 0.3
    value = 0.5 * keyword + 0.3 * prominence + 0.2 * density
    if any(a.breaking for a in story.articles):
        value += 0.2
    return min(1.0, value)


def _coverage(story: Story, saturation: int) -> float:
    sources = len({a.source_name for a in story.articles})
    return min(1.0, math.log(1 + sources) / math.log(1 + max(saturation, 1)))
