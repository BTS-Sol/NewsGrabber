"""Sprachwerkzeuge ohne externe Modelle: Satztrennung, Tokenisierung, Stoppwörter, Stemming."""

from __future__ import annotations

import functools
import re
import unicodedata

import snowballstemmer

STOPWORDS_DE = frozenset("""
aber alle allem allen aller alles als also am an ander andere anderem anderen anderer anderes anders auch auf aus
bei beim bereits bin bis bisher bist da dabei dadurch dafür dagegen daher dahin damals damit danach dann daran darauf
daraus darf darin darum darunter das dass dasselbe davon davor dazu dein deine dem demnach den denen denn dennoch der
deren derer des deshalb dessen dich die dies diese diesem diesen dieser dieses dir doch dort du durch eben ebenfalls
ein eine einem einen einer eines einige einigen einiger einmal er es etwa etwas euch euer eure für gab gegen gegenüber
gewesen gibt ging habe haben hat hatte hatten hier hin hinter ich ihm ihn ihnen ihr ihre ihrem ihren ihrer ihres im
immer in indem ins ist ja je jede jedem jeden jeder jedes jedoch jetzt kam kann kaum kein keine keinem keinen keiner
können könnte konnte machen man manche mehr mein meine mich mir mit muss musste müssen nach nachdem neben nein nicht
nichts noch nun nur ob oder ohne rund schon sehr sei seien sein seine seinem seinen seiner seit selbst sich sie sind so
sogar solche soll sollen sollte sondern sowie später statt um und uns unser unsere unter viel viele vom von vor
während war waren warum was weil weiter weitere welche welchem welchen welcher welches wenn wer werde werden wie wieder
will wir wird wo wohl wollen worden wurde wurden zu zum zur zwar zwischen über laut sagte sagt sagten erklärte hieß
heißt hatten habe sowie zudem mehrere derzeit bislang demnach zufolge seitdem inzwischen allerdings jedoch beim ab
""".split())

STOPWORDS_EN = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between both but
by can could did do does doing down during each few for from further had has have having he her here hers herself him
himself his how however i if in into is it its itself just me more most my myself no nor not now of off on once only
or other our ours ourselves out over own same she should so some such than that the their theirs them themselves then
there these they this those through to too under until up very was we were what when where which while who whom why
will with would you your yours yourself yourselves said says say according told new also may might must one two
""".split())

# Wörter, die in Nachrichten so häufig sind, dass sie als Schlagwort nichts aussagen
GENERIC_NEWS_WORDS = frozenset("""
uhr jahr jahre jahren jahres monat monate monaten woche wochen tag tage tagen heute gestern morgen montag dienstag
mittwoch donnerstag freitag samstag sonntag januar februar märz april mai juni juli august september oktober november
dezember prozent menschen angaben bericht berichte zeit seite seiten ende anfang fall fälle frage fragen teil
mittel milliarden millionen euro dollar mal beispiel thema themen grund gründe lage weg ziel nachrichten news
norden süden osten westen nord süd ost west neue neuer neues erste ersten zweite dritte große großen update
deutsche deutschen deutscher deutschland stand angaben
year years month week day today yesterday monday tuesday wednesday thursday friday saturday sunday percent people
report reports time week weeks million billion first last next way part story stories news update
""".split())

_ABBREVIATIONS = (
    "z. B.", "z.B.", "u. a.", "u.a.", "d. h.", "d.h.", "v. a.", "v.a.", "o. ä.", "u. U.", "bzw.", "ca.", "Dr.",
    "Prof.", "Nr.", "St.", "vgl.", "etc.", "usw.", "Mio.", "Mrd.", "Tsd.", "inkl.", "ggf.", "evtl.", "sog.",
    "Abs.", "Art.", "Hr.", "Fr.", "Std.", "Min.", "Sek.", "Jh.", "Jan.", "Feb.", "Mär.", "Apr.", "Jun.", "Jul.",
    "Aug.", "Sep.", "Sept.", "Okt.", "Nov.", "Dez.", "Mr.", "Mrs.", "Ms.", "U.S.", "U.K.", "e.g.", "i.e.", "vs.",
    "Inc.", "Ltd.", "Co.", "Corp.", "No.", "Gov.", "Sen.", "Rep.", "Gen.", "Lt.", "Col.", "approx.", "a.m.", "p.m.",
)
_ABBREV_RE = re.compile("|".join(re.escape(a) for a in sorted(_ABBREVIATIONS, key=len, reverse=True)))
_PLACEHOLDER = "⁣"  # unsichtbares Trennzeichen als Platzhalter für geschützte Punkte

_MONTHS = ("januar februar märz april mai juni juli august september oktober november dezember "
           "january february march april may june july august september october november december").split()
_ORDINAL_CONTEXT = frozenset(
    "der die das dem den des am im zum zur vom beim seit bis ab vor nach für um an zu ein eine einem einer jeder "
    "jede jedes dieser diese dieses zum".split())

_BOUNDARY_RE = re.compile(r"([.!?…]+[\"“”»«’')\]]*)\s+(?=[„\"“«»'(\[]?[A-ZÄÖÜ0-9])")
_WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿĀ-žẞß]+(?:[-'’][0-9A-Za-zÀ-ÖØ-öø-ÿĀ-žẞß]+)*")
_DIGIT_RE = re.compile(r"\d")


def normalize(text: str) -> str:
    """Unicode-Normalisierung, geschützte Leerzeichen, Steuerzeichen."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    text = re.sub(r"[​-‏⁠⁣﻿­]", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def collapse(text: str) -> str:
    """Alles auf eine Zeile zusammenziehen."""
    return re.sub(r"\s+", " ", normalize(text)).strip()


def detect_language(text: str, default: str = "de") -> str:
    """Grobe Spracherkennung Deutsch/Englisch über Stoppwörter."""
    words = [w.lower() for w in _WORD_RE.findall(text[:3000])]
    if len(words) < 5:
        return default
    de = sum(1 for w in words if w in STOPWORDS_DE)
    en = sum(1 for w in words if w in STOPWORDS_EN)
    umlauts = len(re.findall(r"[äöüß]", text[:3000].lower()))
    de += umlauts * 0.5
    if de == en:
        return default
    return "de" if de > en else "en"


def split_sentences(text: str) -> list:
    """Zerlegt Text in Sätze; Absätze (Zeilenumbrüche) sind immer Satzgrenzen."""
    sentences = []
    for paragraph in normalize(text).split("\n"):
        paragraph = paragraph.strip()
        if paragraph:
            sentences.extend(_split_paragraph(paragraph))
    return sentences


def _split_paragraph(paragraph: str) -> list:
    protected = _ABBREV_RE.sub(lambda m: m.group(0).replace(".", _PLACEHOLDER), paragraph)
    parts, start = [], 0
    for match in _BOUNDARY_RE.finditer(protected):
        end = match.end(1)
        before = protected[start:end]
        if _is_false_boundary(before, protected[match.end():]):
            continue
        parts.append(protected[start:end])
        start = match.end()
    parts.append(protected[start:])
    return [p.replace(_PLACEHOLDER, ".").strip() for p in parts if p.strip()]


def _is_false_boundary(before: str, after: str) -> bool:
    # Ordnungszahlen: „am 3. Oktober“, „der 2. Weltkrieg“, „zum 5. Mal“
    tokens = before.split()
    if tokens and re.fullmatch(r"\d{1,2}\.", tokens[-1]):
        next_word = (after.split(" ", 1)[0] if after else "").strip("„\"“«»'([").lower()
        prev_word = tokens[-2].lower() if len(tokens) > 1 else ""
        if next_word in _MONTHS or prev_word in _ORDINAL_CONTEXT:
            return True
    # Initialen wie „J. R. R. Tolkien“
    if re.search(r"(?:^|\s)[A-ZÄÖÜ]\.$", before):
        return True
    return False


def words(text: str) -> list:
    return _WORD_RE.findall(text or "")


_TOKEN_RE = re.compile(r"\S*\w\S*")


def word_count(text: str) -> int:
    """Wörter wie in einer Textverarbeitung: durch Leerraum getrennt, mit mindestens einem Buchstaben/Ziffer."""
    return len(_TOKEN_RE.findall(text or ""))


def truncate_words(text: str, limit: int, ellipsis: str = " …") -> str:
    tokens = (text or "").split()
    if len(tokens) <= limit:
        return text
    return " ".join(tokens[:limit]).rstrip(",;:–-") + ellipsis


def has_number(text: str) -> bool:
    return bool(_DIGIT_RE.search(text or ""))


@functools.lru_cache(maxsize=4)
def _stemmer(lang: str):
    return snowballstemmer.stemmer("german" if lang == "de" else "english")


def stopwords(lang: str) -> frozenset:
    return STOPWORDS_DE if lang == "de" else STOPWORDS_EN


@functools.lru_cache(maxsize=200_000)
def stem(word: str, lang: str = "de") -> str:
    return _stemmer(lang).stemWord(word.lower())


def is_content_word(token: str, lang: str) -> bool:
    lower = token.lower()
    if lower in STOPWORDS_DE or lower in STOPWORDS_EN:
        return False
    if lower.isdigit():
        return len(lower) >= 2
    return len(lower) >= 3


def terms(text: str, lang: str = "de") -> list:
    """Inhaltswörter als Stämme (für Ähnlichkeit und Gewichtung)."""
    result = []
    for token in _WORD_RE.findall(text or ""):
        if is_content_word(token, lang):
            result.append(token.lower() if token.isdigit() else stem(token, lang))
    return result


def surface_terms(text: str, lang: str = "de") -> list:
    """Paare (Stamm, Originalschreibweise) der Inhaltswörter."""
    return [(token.lower() if token.isdigit() else stem(token, lang), token)
            for token in _WORD_RE.findall(text or "") if is_content_word(token, lang)]
