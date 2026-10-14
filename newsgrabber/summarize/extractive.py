"""Extraktive Zusammenfassung ohne externe Dienste.

Aus allen Artikeln einer Nachricht werden die aussagekräftigsten Sätze gewählt
(Nähe zum Themenkern, Überschneidung mit der Überschrift, Position im Text,
Quellenqualität) und per *Maximal Marginal Relevance* auf Redundanz geprüft – auch
über Quellen hinweg, die dasselbe mit anderen Worten berichten. Sätze mit Zahlen,
Daten und Namen, die nicht in der Zusammenfassung stehen, werden zu kurzen Fakten
verdichtet.

Urheberrecht: Pro Quelle werden höchstens ``max_words_per_source`` Wörter übernommen
und die Quellen im Bericht immer genannt. Für Zusammenfassungen in eigenen Worten
ist das Claude-Verfahren vorgesehen.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..models import Article, Story, StorySummary
from ..process.clean import Boilerplate
from ..process.dedup import char_ngrams
from ..process.keywords import DocumentFrequencies, extract_keywords
from ..process.text import has_number, split_sentences, terms, truncate_words, word_count
from .base import preferred_articles

_QUOTE_RE = re.compile(r"[„“\"«»]")
_URL_RE = re.compile(r"https?://|www\.\w")
_PROPER_RE = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-ZÄÖÜ][a-zäöüß]+")
_SENTENCE_END_RE = re.compile(r"[.!?…][\"“”»«’')\]]*$")

REDUNDANCY_LIMIT = 0.38  # ab dieser Ähnlichkeit gilt ein Satz als Wiederholung
FACT_REDUNDANCY_LIMIT = 0.32
MIN_NOVELTY = 0.5  # Anteil neuer Begriffe, den ein weiterer Satz mitbringen muss


@dataclass
class _Sentence:
    text: str
    article: Article
    article_index: int
    position: int
    from_teaser: bool
    vec: dict = field(default_factory=dict)  # Relevanz (Korpus-IDF)
    rvec: dict = field(default_factory=dict)  # Redundanz (satzbezogene IDF, inkl. N-Gramme)
    stems: frozenset = frozenset()
    grams: frozenset = frozenset()
    words: int = 0
    score: float = 0.0


class ExtractiveSummarizer:
    name = "extraktiv"

    def __init__(self, language: str = "de", max_words_per_source: int = 70, max_sentences: int = 40,
                 df: DocumentFrequencies = None, boilerplate: Boilerplate = None):
        self.language = language
        self.max_words_per_source = max_words_per_source
        self.max_sentences = max_sentences
        self.df = df or DocumentFrequencies()
        self.boilerplate = boilerplate or Boilerplate()

    def summarize_all(self, jobs: list) -> list:
        return [self.summarize(story, target) for story, target in jobs]

    # ------------------------------------------------------------------
    def summarize(self, story: Story, target_words: int) -> StorySummary:
        articles = preferred_articles(story, self.language)
        lead = articles[0]
        headline = lead.title
        keywords = extract_keywords(story, self.df, language=self.language)
        candidates = self._candidates(articles)
        if not candidates:
            text = lead.teaser or " ".join(split_sentences(lead.content)[:3])
            return StorySummary(headline=headline, summary=truncate_words(text, target_words), facts=[],
                                keywords=keywords, method=self.name, language=lead.language)

        self._score(candidates, lead)
        selected = self._select(candidates, target_words)
        # zentralster Satz zuerst, danach in der Reihenfolge der Quellen und Texte
        first = max(selected, key=lambda c: c.score)
        selected = [first] + sorted((c for c in selected if c is not first),
                                    key=lambda c: (c.article is not first.article, c.article_index, c.position))
        summary = " ".join(c.text for c in selected)
        facts = self._facts([c for c in candidates if c not in selected], selected, target_words)
        return StorySummary(headline=headline, summary=summary, facts=facts, keywords=keywords,
                            method=self.name, language=lead.language)

    def _candidates(self, articles: list) -> list:
        titles = {_plain(a.title) for a in articles}
        result = []
        for index, article in enumerate(articles[:6]):
            position = 0
            for text, from_teaser in ((article.teaser, True), (article.content, False)):
                for sentence in split_sentences(text)[: self.max_sentences]:
                    sentence = sentence.strip()
                    words = word_count(sentence)
                    if not 6 <= words <= 55 or _URL_RE.search(sentence) or self.boilerplate.matches(sentence):
                        continue
                    # Überschriften/Zwischentitel haben kein Satzzeichen am Ende
                    if not _SENTENCE_END_RE.search(sentence) or sentence[:1].islower() or _plain(sentence) in titles:
                        continue
                    counts = Counter(terms(sentence, article.language))
                    if not counts:
                        continue
                    vec = _normalize({t: n * self.df.idf(t) for t, n in counts.items()})
                    result.append(_Sentence(sentence, article, index, position, from_teaser, vec,
                                            stems=frozenset(counts), grams=frozenset(char_ngrams(sentence)),
                                            words=words))
                    position += 1
        _redundancy_vectors(result)
        # wörtliche Wiederholungen (Teaser-Satz steht oft auch am Textanfang) entfernen
        unique = []
        for candidate in result:
            if not any(_cosine(candidate.rvec, other.rvec) > 0.85 for other in unique):
                unique.append(candidate)
        return unique

    def _score(self, candidates: list, lead: Article) -> None:
        centroid = _normalize(sum((Counter(c.vec) for c in candidates), Counter()))
        title_terms = set(terms(f"{lead.kicker} {lead.title}", lead.language))
        for c in candidates:
            sim = _cosine(c.vec, centroid)
            overlap = len(title_terms & set(c.vec)) / max(1, len(title_terms))
            position = 1.0 / (1.0 + 0.25 * c.position)
            score = 0.45 * sim + 0.30 * overlap + 0.25 * position
            if c.from_teaser:
                score += 0.1
            score *= 0.6 + 0.4 * c.article.quality
            if _QUOTE_RE.search(c.text):
                score *= 0.75  # wörtliche Zitate sparsam einsetzen
            if c.text.rstrip().endswith("?"):
                score *= 0.6
            c.score = score

    def _select(self, candidates: list, target_words: int) -> list:
        selected, words, per_source = [], 0, Counter()
        known_stems, known_grams = set(), set()
        pool = sorted(candidates, key=lambda c: -c.score)
        while pool and words < target_words * 0.9:
            best, best_value = None, -1.0
            for c in pool:
                redundancy = max((_cosine(c.rvec, s.rvec) for s in selected), default=0.0)
                if redundancy > REDUNDANCY_LIMIT or _novelty(c, known_stems, known_grams) < MIN_NOVELTY:
                    continue
                if per_source[c.article.source_name] + c.words > self.max_words_per_source and selected:
                    continue
                if words + c.words > target_words * 1.25 and selected:
                    continue
                value = 0.7 * c.score - 0.3 * redundancy
                if value > best_value:
                    best, best_value = c, value
            if best is None:
                break
            selected.append(best)
            pool.remove(best)
            words += best.words
            per_source[best.article.source_name] += best.words
            known_stems |= best.stems
            known_grams |= best.grams
        if not selected and pool:
            selected.append(pool[0])
        return selected

    def _facts(self, rest: list, selected: list, target_words: int) -> list:
        wanted = 2 if target_words < 70 else 3 if target_words < 120 else 4
        scored = []
        for c in rest:
            bonus = 0.35 if has_number(c.text) else 0.0
            bonus += 0.1 if _PROPER_RE.search(c.text) else 0.0
            if _QUOTE_RE.search(c.text):
                bonus -= 0.15
            scored.append((c.score + bonus, c))
        scored.sort(key=lambda x: -x[0])
        facts, chosen = [], [s.rvec for s in selected]
        known_stems = set().union(*(s.stems for s in selected)) if selected else set()
        known_grams = set().union(*(s.grams for s in selected)) if selected else set()
        for _score, c in scored:
            if len(facts) >= wanted:
                break
            if any(_cosine(c.rvec, v) > FACT_REDUNDANCY_LIMIT for v in chosen) or \
                    _novelty(c, known_stems, known_grams) < MIN_NOVELTY:
                continue
            fact = shorten_fact(c.text)
            if fact:
                facts.append(fact)
                chosen.append(c.rvec)
                known_stems |= c.stems
                known_grams |= c.grams
        return facts


def _redundancy_vectors(sentences: list) -> None:
    """Gewichtung nach Seltenheit *innerhalb der Nachricht*: Themenwörter, die in fast jedem Satz
    vorkommen, zählen wenig; konkrete Angaben (Zahlen, Namen) zählen viel. Zeichen-N-Gramme
    erkennen dieselbe Aussage auch bei unterschiedlichen Komposita."""
    features = []
    for s in sentences:
        counts = Counter(terms(s.text, s.article.language))
        for gram in char_ngrams(s.text):
            counts[gram] += 0.35
        features.append(counts)
    df = Counter(f for counts in features for f in counts)
    n = len(sentences)
    for s, counts in zip(sentences, features):
        s.rvec = _normalize({f: w * math.log(1 + n / df[f]) for f, w in counts.items()})


def _novelty(sentence: _Sentence, known_stems: set, known_grams: set) -> float:
    """Anteil neuer Begriffe (Mittel aus Wortstämmen und Zeichen-N-Grammen)."""
    if not known_stems:
        return 1.0
    stem_new = len(sentence.stems - known_stems) / max(1, len(sentence.stems))
    gram_new = len(sentence.grams - known_grams) / max(1, len(sentence.grams)) if sentence.grams else stem_new
    return (stem_new + gram_new) / 2


def _plain(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def shorten_fact(sentence: str, limit: int = 28) -> str:
    """Kürzt einen Satz auf seinen Kern (Teilsatz mit Zahl bzw. erster Teilsatz)."""
    sentence = sentence.strip()
    if word_count(sentence) <= limit:
        return sentence
    clauses = [c.strip() for c in re.split(r";\s+|\s+[–—]\s+|,\s+(?=(?:aber|doch|während|weil|da|wobei|"
                                             r"nachdem|sodass|but|while|which|after|because)\b)", sentence) if c.strip()]
    with_number = [c for c in clauses if has_number(c) and 6 <= word_count(c) <= limit]
    pick = with_number[0] if with_number else next((c for c in clauses if 6 <= word_count(c) <= limit), "")
    if not pick:
        return ""
    pick = pick.rstrip(",;:–— ")
    if not pick.endswith((".", "!", "?")):
        pick += "."
    return pick[0].upper() + pick[1:]


def _normalize(vec) -> dict:
    norm = math.sqrt(sum(v * v for v in vec.values()))
    return {k: v / norm for k, v in vec.items()} if norm else {}


def _cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())
