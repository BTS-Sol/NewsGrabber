"""Bereinigung von Titeln, Anrisstexten und Volltexten."""

from __future__ import annotations

import html
import re
import warnings
from typing import Iterable, Optional

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning, NavigableString

from .text import collapse, normalize

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

_DROP_TAGS = ("script", "style", "noscript", "iframe", "svg", "figure", "figcaption", "aside", "nav", "footer",
              "form", "button", "template", "picture", "video", "audio")
_BLOCK_TAGS = ("p", "div", "section", "article", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
               "pre", "table", "tr", "dd", "dt", "header")

# Zeilen/Sätze, die keine Nachricht sind (Navigation, Werbung, Hinweise der Redaktion)
DEFAULT_BOILERPLATE = [
    r"^(mehr|lesen sie (auch|mehr)|weiterlesen|weitere informationen|mehr zum thema|zum thema|auch interessant)\b",
    r"^(read more|continue reading|related( articles| stories)?|see also|more on this story|recommended)\b",
    r"\b(newsletter|abonnier|abonnement|abo abschließen|jetzt registrieren|cookie|datenschutzeinstellungen)\b",
    r"\b(subscribe|sign up|log in|cookies|advertisement|sponsored)\b",
    r"^(anzeige|werbung|foto|bild|grafik|video|audio|quelle|copyright|©)\s*[:|]",
    r"^(image|photo|credit|caption)\s*[:|]",
    r"\balle rechte vorbehalten\b|\ball rights reserved\b",
    r"^the post .* appeared first on\b",
    r"^(dieser artikel|this article) (wurde|was) (zuerst|originally|first)\b",
    r"^\(?(dpa|afp|reuters|ap|epd|kna|sid)(/\w+)*\)?$",
    r"^(stand|updated|aktualisiert)\s*:?\s*\d",
    r"^(teilen|share|drucken|print|kommentare|comments)\b",
    r"hier klicken|click here",
]

_TITLE_SUFFIX_RE = re.compile(r"\s+[|–—-]\s+[^|–—-]{2,40}$")
_TRAILING_CRUFT_RE = re.compile(
    r"(\s*(\[(…|\.\.\.|mehr|more)\]|\(mehr\)|mehr\s*»|weiterlesen\s*»?|read more\s*»?|continue reading\.?|…|\.\.\.)\s*)+$",
    re.IGNORECASE)


class Boilerplate:
    def __init__(self, extra_patterns: Optional[Iterable[str]] = None):
        patterns = list(DEFAULT_BOILERPLATE) + list(extra_patterns or [])
        self._regex = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)

    def matches(self, text: str) -> bool:
        text = text.strip()
        # Boilerplate besteht aus kurzen Zeilen – lange Absätze sind Inhalt
        if len(text.split()) > 40:
            return False
        return bool(self._regex.search(text))


_DEFAULT_BOILERPLATE = Boilerplate()


def html_to_text(markup: str) -> str:
    """Wandelt HTML in Fließtext mit Absätzen um (Absätze durch ``\\n`` getrennt)."""
    if not markup:
        return ""
    if "<" not in markup:
        return normalize(html.unescape(markup))
    soup = BeautifulSoup(markup, "lxml")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_after(NavigableString("\n"))
    text = soup.get_text()
    return normalize(html.unescape(text))


def clean_title(title: str, source_name: str = "") -> str:
    title = collapse(html_to_text(title or ""))
    title = re.sub(r"^\+{2,}\s*|\s*\+{2,}$", "", title)  # „+++ Liveticker +++“
    if source_name:
        pattern = re.compile(r"\s*[|–—:-]\s*" + re.escape(source_name) + r"(\.\w{2,3})?\s*$", re.IGNORECASE)
        title = pattern.sub("", title)
    match = _TITLE_SUFFIX_RE.search(title)
    if match and re.search(r"\.(de|com|org|net|at|ch)\b|online|news", match.group(0), re.IGNORECASE):
        title = title[:match.start()]
    return title.strip(" -–—|:")


def clean_teaser(text: str, title: str = "", boilerplate: Boilerplate = _DEFAULT_BOILERPLATE) -> str:
    """Bereinigt den Anrisstext aus Feed/API."""
    text = html_to_text(text or "")
    lines = [line for line in text.split("\n") if line.strip() and not boilerplate.matches(line)]
    text = collapse(" ".join(lines))
    text = re.sub(r"^the post .*?appeared first on .*$", "", text, flags=re.IGNORECASE)
    text = _TRAILING_CRUFT_RE.sub("", text).strip()
    text = re.sub(r"(?<=[.!?…])\s+(mehr|weiter|more)$", "", text, flags=re.IGNORECASE)
    if title and text.lower().startswith(title.lower()):
        text = text[len(title):].lstrip(" .:-–—")
    if title and text.strip().lower() == title.strip().lower():
        return ""
    return text


def clean_content(text: str, boilerplate: Boilerplate = _DEFAULT_BOILERPLATE, max_chars: int = 40000) -> str:
    """Bereinigt extrahierten Volltext: Boilerplate-Zeilen, Navigationsreste, Dubletten."""
    text = normalize(text or "")[:max_chars]
    seen, lines = set(), []
    for line in text.split("\n"):
        line = line.strip()
        if not line or boilerplate.matches(line):
            continue
        # sehr kurze Zeilen ohne Satzzeichen sind meist Navigation/Zwischentitel
        if len(line.split()) < 4 and not re.search(r"[.!?:]$", line):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def strip_html_tags(text: str) -> str:
    return collapse(html_to_text(text))
