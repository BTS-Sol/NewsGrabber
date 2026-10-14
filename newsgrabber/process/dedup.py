"""Duplikaterkennung: identische Artikel zusammenführen und gleiche Nachrichten bündeln.

1. **Identische Artikel** (gleiche kanonische URL, z. B. aus zwei Feeds desselben
   Anbieters) werden zu einem Artikel verschmolzen.
2. **Gleiche Nachricht aus mehreren Quellen** wird über TF-IDF-Vektoren aus Titel,
   Anrisstext und Textanfang zu einer :class:`~newsgrabber.models.Story` gebündelt
   (Greedy-Clustering gegen Cluster-Zentroide, ohne Verkettungseffekte).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import Article, Story
from .keywords import DocumentFrequencies
from .text import STOPWORDS_DE, STOPWORDS_EN, terms, words

_TRACKING_RE = re.compile(
    r"^(utm_\w+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|wt_mc|wt_zmc|cmp|at_\w+|xtor|ns_\w+|ref|ref_src|"
    r"referrer|rss|feed|ito|itm_\w+|icid|sara_ref|_ga|ocid|smid|cid|campaign)$",
    re.IGNORECASE)


def canonical_url(url: str) -> str:
    """URL ohne Tracking-Parameter, Fragment, ``www.`` und abschließenden Schrägstrich."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_RE.match(k)]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def strip_tracking(url: str) -> str:
    """Entfernt Tracking-Parameter (utm_* usw.) und Fragment, lässt die URL sonst unverändert."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    if not parts.query and not parts.fragment:
        return url.strip()
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_RE.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _title_key(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def merge_identical(articles: list) -> list:
    """Verschmilzt doppelte Einträge desselben Anbieters (gleiche URL oder gleicher Titel).

    Verweist eine *andere* Quelle auf denselben Artikel (z. B. Hacker News auf heise),
    bleiben beide Einträge erhalten: die Clusterbildung bündelt sie, und die
    zusätzliche Quelle zählt als Hinweis auf Relevanz.
    """
    merged: dict = {}
    for article in articles:
        keys = [f"{article.source_name}|{canonical_url(article.url)}",
                f"{article.source_name}|{_title_key(article.title)}"]
        existing = next((merged[k] for k in keys if k in merged), None)
        if existing is None:
            for key in keys:
                merged[key] = article
            continue
        _merge_into(existing, article)
        for key in keys:
            merged.setdefault(key, existing)
    unique, seen = [], set()
    for article in merged.values():
        if id(article) not in seen:
            seen.add(id(article))
            unique.append(article)
    return unique


def _merge_into(target: Article, other: Article) -> None:
    target.categories = tuple(dict.fromkeys(target.categories + other.categories))
    if not target.category and other.category:
        target.category = other.category
    if other.quality > target.quality:
        target.quality, target.source_id = other.quality, other.source_id
    if len(other.content) > len(target.content):
        target.content, target.fulltext_status = other.content, other.fulltext_status
    if len(other.teaser) > len(target.teaser):
        target.teaser = other.teaser
    if target.published is None or (target.date_estimated and not other.date_estimated):
        target.published, target.date_estimated = other.published, other.date_estimated
    target.feed_rank = min(target.feed_rank, other.feed_rank)
    target.breaking = target.breaking or other.breaking
    target.fetch_fulltext = target.fetch_fulltext and other.fetch_fulltext
    target.fetch_priority = target.fetch_priority or other.fetch_priority
    for tag in other.tags:
        if tag not in target.tags:
            target.tags.append(tag)


# ------------------------------------------------------------------ Vektoren
NGRAM_SIZE = 5
NGRAM_WEIGHT = 0.35
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)+|\d{2,}")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def char_ngrams(text: str, size: int = NGRAM_SIZE) -> list:
    grams = []
    for word in words(text):
        if len(word) < size or word.lower() in STOPWORDS_DE or word.lower() in STOPWORDS_EN:
            continue
        for part in re.split(r"[-'’]", _fold(word)):
            grams.extend(f"~{part[i:i + size]}" for i in range(len(part) - size + 1))
    return grams


class Vectorizer:
    def __init__(self, articles: list):
        self.df = DocumentFrequencies()
        self._terms = {}
        for article in articles:
            weighted = self._weighted_terms(article)
            self._terms[id(article)] = weighted
            self.df.add(weighted.keys())

    @staticmethod
    def _weighted_terms(article: Article) -> Counter:
        lang = article.language
        counts: Counter = Counter()
        parts = ((f"{article.kicker} {article.title}", 3.0), (" ".join(article.teaser.split()[:60]), 1.5),
                 (" ".join(article.content.split()[:120]), 1.0))
        for text, weight in parts:
            for term in terms(text, lang):
                counts[term] += weight
            # Zeichen-N-Gramme verbinden deutsche Komposita („Klimakonferenz“ ~ „UN-Klimagipfel“)
            for gram in char_ngrams(text):
                counts[gram] += weight * NGRAM_WEIGHT
            for number in _NUMBER_RE.findall(text):
                counts[f"#{number}"] += weight
        return counts

    def vector(self, article: Article) -> dict:
        counts = self._terms.get(id(article)) or self._weighted_terms(article)
        vec = {t: (1 + math.log(c)) * self.df.idf(t) for t, c in counts.items()}
        return _normalize(vec)


def _normalize(vec: dict) -> dict:
    norm = math.sqrt(sum(v * v for v in vec.values()))
    return {k: v / norm for k, v in vec.items()} if norm else {}


def cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


ANCHOR_BONUS = 0.08
MAX_ANCHORS = 2


def anchors(article: Article) -> set:
    """Großgeschriebene Wörter und Zahlen aus Titel und Anrisstext (sprachunabhängige Merkmale)."""
    text = f"{article.kicker} {article.title} {' '.join(article.teaser.split()[:60])}"
    result = set()
    for token in words(text):
        lower = token.lower()
        if lower in STOPWORDS_DE or lower in STOPWORDS_EN:
            continue
        if token.isdigit() and len(token) >= 2 or (token[:1].isupper() and len(token) >= 3):
            result.add(_fold(token))
    result.update(f"#{n}" for n in _NUMBER_RE.findall(text) if not n.isdigit())
    return result


class _Cluster:
    __slots__ = ("articles", "sum", "centroid", "sources", "titles", "anchors")

    def __init__(self):
        self.articles, self.sum, self.centroid = [], Counter(), {}
        self.sources, self.titles, self.anchors = set(), set(), set()

    def add(self, article: Article, vec: dict, anchor_set: set) -> None:
        self.articles.append(article)
        self.sum.update(vec)
        self.centroid = _normalize(dict(self.sum))
        self.sources.add(article.source_name)
        self.titles.add(_title_key(article.title))
        self.anchors |= anchor_set


def cluster_articles(articles: list, threshold: float, same_source_bonus: float = 0.25) -> "tuple[list, Vectorizer]":
    """Bündelt Artikel zu Nachrichten. Liefert (Liste von Story, Vectorizer).

    Ähnlichkeit = Kosinus der TF-IDF-Vektoren (Wortstämme, Zeichen-N-Gramme, Zahlen) zum
    Cluster-Zentroid plus ein Bonus für seltene gemeinsame Eigennamen/Zahlen. Der Bonus
    verbindet auch deutsch- und englischsprachige Berichte über dasselbe Ereignis.
    """
    vectorizer = Vectorizer(articles)
    anchor_sets = {id(a): anchors(a) for a in articles}
    anchor_df = Counter(token for s in anchor_sets.values() for token in s)
    rare_limit = max(5, 0.02 * len(articles))
    order = sorted(articles, key=lambda a: (-a.quality, a.date_estimated, a.feed_rank))
    clusters: list = []
    index = defaultdict(set)  # Merkmal -> Cluster-Indizes (nur die stärksten Merkmale)
    for article in order:
        vec = vectorizer.vector(article)
        own_anchors = {t for t in anchor_sets[id(article)] if anchor_df[t] <= rare_limit}
        title_key = _title_key(article.title)
        top_terms = sorted(vec, key=vec.get, reverse=True)[:15]
        candidates = set()
        for term in top_terms:
            candidates |= index.get(term, set())
        for token in own_anchors:
            candidates |= index.get(f"@{token}", set())
        best, best_sim = None, 0.0
        for ci in candidates:
            cluster = clusters[ci]
            if title_key in cluster.titles:
                sim = 1.0
            else:
                shared = len(own_anchors & cluster.anchors)
                sim = cosine(vec, cluster.centroid) + ANCHOR_BONUS * min(shared, MAX_ANCHORS)
            # Mehrere Artikel derselben Quelle sind meist verschiedene Aspekte – strengere Schwelle
            required = threshold + (same_source_bonus if article.source_name in cluster.sources else 0.0)
            if sim >= required and sim > best_sim:
                best, best_sim = ci, sim
        if best is None:
            clusters.append(_Cluster())
            best = len(clusters) - 1
        clusters[best].add(article, vec, own_anchors)
        for term in top_terms:
            index[term].add(best)
        for token in own_anchors:
            index[f"@{token}"].add(best)
    stories = [Story(articles=c.articles) for c in clusters]
    return stories, vectorizer
