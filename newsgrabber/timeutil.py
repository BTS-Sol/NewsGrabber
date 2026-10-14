"""Zeitzonen, Datumsangaben und das Zeitfenster eines Briefings.

Intern werden ausschließlich zeitzonenbewusste ``datetime``-Objekte verwendet.
Veröffentlichungszeitpunkte werden beim Einlesen nach UTC normalisiert und erst
bei der Anzeige in die konfigurierte Zeitzone (Standard: Europe/Berlin) umgerechnet.
"""

from __future__ import annotations

import datetime as _dt
import email.utils
import re
import time
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = _dt.timezone.utc

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


class DateFormatError(ValueError):
    """Ungültige Datums- oder Uhrzeitangabe."""


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DateFormatError(
            f"Unbekannte Zeitzone '{name}' – unter Windows ggf. 'pip install tzdata' ausführen") from exc


def now(tz: _dt.tzinfo) -> _dt.datetime:
    return _dt.datetime.now(tz)


def parse_date(text: str, today: Optional[_dt.date] = None) -> _dt.date:
    """``JJJJ-MM-TT``, ``TT.MM.JJJJ``, ``heute``, ``morgen`` oder ``gestern``."""
    value = str(text).strip().lower()
    today = today or _dt.date.today()
    relative = {"heute": 0, "today": 0, "morgen": 1, "tomorrow": 1, "gestern": -1, "yesterday": -1}
    if value in relative:
        return today + _dt.timedelta(days=relative[value])
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y%m%d"):
        try:
            return _dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise DateFormatError(
        f"Ungültiges Datum '{text}' – erwartet JJJJ-MM-TT, TT.MM.JJJJ, 'heute', 'morgen' oder 'gestern'")


def parse_clock(text: str) -> _dt.time:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", str(text))
    if not match:
        raise DateFormatError(f"Ungültige Uhrzeit '{text}' – erwartet HH:MM")
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    if hours > 23 or minutes > 59 or seconds > 59:
        raise DateFormatError(f"Ungültige Uhrzeit '{text}'")
    return _dt.time(hours, minutes, seconds)


def briefing_window(report_date: _dt.date, tz: _dt.tzinfo, hours: float,
                    end_time: Optional[_dt.time] = None,
                    current: Optional[_dt.datetime] = None) -> "tuple[_dt.datetime, _dt.datetime]":
    """Zeitfenster der Meldungen für das Briefing eines Tages.

    Ende des Fensters ist der Stichzeitpunkt ``end_time`` am Berichtstag (falls
    gesetzt), sonst das Ende des Berichtstags – jeweils höchstens „jetzt“. Ein
    Lauf am Morgen des Berichtstags erfasst also die letzten ``hours`` Stunden bis
    zum Start; ein nachträglicher Lauf für einen vergangenen Tag endet an dessen
    Stichzeit bzw. um Mitternacht.
    """
    current = (current or now(tz)).astimezone(tz)
    if end_time is not None:
        end = _dt.datetime.combine(report_date, end_time, tzinfo=tz)
    else:
        end = _dt.datetime.combine(report_date + _dt.timedelta(days=1), _dt.time(0, 0), tzinfo=tz)
    end = min(end, current)
    start = end - _dt.timedelta(hours=hours)
    return start, end


def from_struct_time(value) -> Optional[_dt.datetime]:
    """feedparser liefert ``*_parsed`` als UTC-``struct_time``."""
    if not value:
        return None
    try:
        return _dt.datetime(*value[:6], tzinfo=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def parse_datetime(value, default_tz: _dt.tzinfo = UTC) -> Optional[_dt.datetime]:
    """Liest ISO-8601, RFC 2822, Unix-Zeitstempel oder ``struct_time`` ein.

    Angaben ohne Zeitzone werden als ``default_tz`` interpretiert. Ergebnis in UTC.
    """
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        result = value if value.tzinfo else value.replace(tzinfo=default_tz)
        return result.astimezone(UTC)
    if isinstance(value, time.struct_time):
        return from_struct_time(value)
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value  # Millisekunden erkennen
        try:
            return _dt.datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if re.fullmatch(r"\d{9,13}", text):
        return parse_datetime(int(text), default_tz)
    iso = text.replace("Z", "+00:00") if text.endswith("Z") else text
    # Python < 3.11 versteht keine Bruchteile mit != 3/6 Stellen – normalisieren
    iso = re.sub(r"(\.\d{1,6})\d*(?=[+-]\d{2}:?\d{2}$|$)", lambda m: m.group(1).ljust(7, "0"), iso)
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)
    try:
        result = _dt.datetime.fromisoformat(iso)
    except ValueError:
        try:
            result = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if result is None:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=default_tz)
    return result.astimezone(UTC)


def format_long_date(day: _dt.date) -> str:
    """``Mittwoch, 14. Oktober 2026`` – unabhängig vom System-Locale."""
    return f"{WEEKDAYS[day.weekday()]}, {day.day}. {MONTHS[day.month - 1]} {day.year}"


def format_datetime(value: Optional[_dt.datetime], tz: _dt.tzinfo, with_date: bool = True) -> str:
    if value is None:
        return ""
    local = value.astimezone(tz)
    return local.strftime("%d.%m.%Y, %H:%M Uhr" if with_date else "%H:%M Uhr")


def format_relative(value: Optional[_dt.datetime], tz: _dt.tzinfo, reference: _dt.datetime) -> str:
    """Kurze Zeitangabe für den Bericht: ``13.10., 18:42`` bzw. ``06:15`` am Berichtstag."""
    if value is None:
        return "ohne Datum"
    local = value.astimezone(tz)
    ref = reference.astimezone(tz)
    if local.date() == ref.date():
        return local.strftime("%H:%M")
    return local.strftime("%d.%m., %H:%M")


def age_hours(value: Optional[_dt.datetime], reference: _dt.datetime) -> Optional[float]:
    if value is None:
        return None
    return max(0.0, (reference - value).total_seconds() / 3600.0)
