"""Schlagwörter: konfigurierte Begriffe erkennen und Schlagwörter für den Bericht bestimmen."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Iterable

from .text import GENERIC_NEWS_WORDS, STOPWORDS_DE, STOPWORDS_EN, split_sentences, surface_terms, words


class KeywordMatcher:
    """Erkennt konfigurierte Schlagwörter in Texten.

    Regeln (für deutsche Komposita und Abkürzungen praktikabel):

    * Begriffe mit höchstens drei Zeichen oder in GROSSBUCHSTABEN (``KI``, ``EU``, ``CVE``)
      werden exakt als ganzes Wort mit Groß-/Kleinschreibung gesucht.
    * Alle anderen Begriffe werden unabhängig von der Schreibweise als Wortanfang gesucht:
      ``Sicherheitslücke`` findet auch „Sicherheitslücken“.
    * Ein abschließendes ``$`` erzwingt die exakte Suche als ganzes Wort (``Rust$``).
    """

    def __init__(self, keywords: Iterable[str]):
        self.keywords = [k.strip() for k in keywords if k and k.strip()]
        exact, prefix = [], []
        self._lookup = {}
        for keyword in self.keywords:
            whole_word = keyword.endswith("$")
            term = keyword.rstrip("$").strip()
            if not term:
                continue
            key = _norm(term)
            self._lookup[(whole_word or _is_exact(term), key if not (whole_word or _is_exact(term)) else term)] = term
            (exact if (whole_word or _is_exact(term)) else prefix).append(term)
        self._exact = _compile(exact, case_sensitive=True, whole_word=True)
        self._prefix = _compile(prefix, case_sensitive=False, whole_word=False)

    def __bool__(self) -> bool:
        return bool(self.keywords)

    def matches(self, text: str) -> Counter:
        """Gefundene Schlagwörter mit Häufigkeit."""
        found: Counter = Counter()
        if not text:
            return found
        if self._exact:
            for match in self._exact.finditer(text):
                term = self._lookup.get((True, re.sub(r"[\s-]+", " ", match.group(1))))
                if term:
                    found[term] += 1
        if self._prefix:
            for match in self._prefix.finditer(text):
                term = self._lookup.get((False, _norm(match.group(1))))
                if term:
                    found[term] += 1
        return found


def _is_exact(term: str) -> bool:
    letters = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", term)
    return len(term) <= 3 or (letters.isupper() and len(letters) > 1)


def _norm(term: str) -> str:
    return re.sub(r"[\s-]+", " ", term.lower()).strip()


def _compile(terms: list, case_sensitive: bool, whole_word: bool):
    if not terms:
        return None
    parts = []
    for term in sorted(set(terms), key=len, reverse=True):
        parts.append(r"[\s-]+".join(re.escape(p) for p in re.split(r"[\s-]+", term)))
    tail = r"(?![\w])" if whole_word else ""
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<![\w])(" + "|".join(parts) + ")" + tail, flags)


# ---------------------------------------------------------------- Schlagwörter
class DocumentFrequencies:
    """Dokumenthäufigkeit der Wortstämme über alle Artikel des Laufs (für TF-IDF)."""

    def __init__(self):
        self.df: Counter = Counter()
        self.docs = 0

    def add(self, stems: Iterable[str]) -> None:
        self.df.update(set(stems))
        self.docs += 1

    def idf(self, stem: str) -> float:
        return math.log((1 + self.docs) / (1 + self.df.get(stem, 0))) + 1.0


def extract_keywords(story, df: DocumentFrequencies, limit: int = 5, language: str = "de") -> list:
    """Schlagwörter einer Nachricht: Tags der Quellen, Eigennamen und gewichtete Substantive.

    Berücksichtigt werden Wörter, die außerhalb des Satzanfangs großgeschrieben vorkommen
    (deutsche Substantive, Eigennamen) – Verben und Adjektive fallen so heraus. Artikel in
    der Berichtssprache haben Vorrang.
    """
    scores: Counter = Counter()
    surface = defaultdict(Counter)
    trusted = set()  # Stämme, die mindestens einmal mitten im Satz großgeschrieben stehen
    phrase_counts: Counter = Counter()
    stem_language: dict = {}
    has_preferred = any(a.language == language and a.text for a in story.articles)

    for article in story.articles:
        factor = 1.0 if not has_preferred or article.language == language else 0.15
        for tag in article.tags:  # redaktionell vergebene Schlagwörter haben Vorrang
            if tag.lower() not in GENERIC_NEWS_WORDS and len(tag) <= 30:
                scores[("tag", tag)] += 6.0 * factor
        parts = [(re.sub(r":\s+", ". ", article.title), 3.0), (article.teaser, 1.5),
                 (" ".join(article.content.split()[:400]), 1.0)]
        for text, weight in parts:
            for sentence in split_sentences(text):
                for position, (stem_, token) in enumerate(surface_terms(sentence, article.language)):
                    lower = token.lower()
                    if token.isdigit() or lower in GENERIC_NEWS_WORDS or len(token) < 3:
                        continue
                    surface[stem_][token] += 1
                    stem_language.setdefault(stem_, article.language)
                    if position > 0 or not sentence.startswith(token):
                        if token[:1].isupper():
                            trusted.add(stem_)
                    scores[("term", stem_)] += weight * df.idf(stem_) / 4 * factor
        if factor == 1.0:
            phrase_counts.update(proper_phrases(article.teaser + "\n" + " ".join(article.content.split()[:400])))

    for phrase, count in phrase_counts.items():
        if count >= 2:  # echte Eigennamen wiederholen sich
            scores[("phrase", phrase)] += 1.5 + count * 0.5

    result, covered = [], []

    def take(label: str) -> None:
        normalized = label.lower()
        if len(result) < limit and not any(normalized in c or c in normalized for c in covered):
            covered.append(normalized)  # „Inflation“ neben „Inflationsrate“ ist überflüssig
            result.append(label)

    ranked = scores.most_common()
    for (kind, key), _score in ranked:
        if kind != "term":
            take(key)
            continue
        forms = surface[key]
        capitalized = sum(n for f, n in forms.items() if f[:1].isupper())
        # nur Substantive/Eigennamen: mitten im Satz großgeschrieben, überwiegend groß
        if key in trusted and capitalized * 2 >= sum(forms.values()):
            take(max((f for f in forms if f[:1].isupper()), key=lambda f: forms[f]))
    if len(result) < 3:
        # englische Texte: Substantive sind kleingeschrieben – längere Begriffe ergänzen
        for (kind, key), _score in ranked:
            if kind == "term" and stem_language.get(key) == "en" and key not in trusted:
                label = max(surface[key], key=surface[key].get)
                if len(label) >= 5:
                    take(label)
    return result


def proper_phrases(text: str) -> list:
    """Folgen von 2–3 großgeschriebenen Wörtern (Eigennamen wie „Europäische Union“, „Donald Trump“).

    Das erste Wort eines Satzes zählt nicht mit, weil es immer großgeschrieben ist.
    """
    phrases = []
    for sentence in split_sentences(text):
        tokens = words(sentence)
        current = []
        for index, token in enumerate(tokens + [""]):
            lower = token.lower()
            capitalized = bool(token) and index > 0 and token[:1].isupper() and not token.isupper() \
                and lower not in STOPWORDS_DE and lower not in STOPWORDS_EN and lower not in GENERIC_NEWS_WORDS
            if capitalized:
                current.append(token)
                continue
            if 2 <= len(current) <= 3:
                phrases.append(" ".join(current))
            current = []
    return phrases
