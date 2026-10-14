"""Kommandozeile des NewsGrabbers.

Beispiele::

    python -m newsgrabber erstellen                       # Briefing für heute
    python -m newsgrabber erstellen -c newsgrabber.yaml --datum 2026-10-14
    python -m newsgrabber erstellen --vorschau --format markdown
    python -m newsgrabber quellen --testen
    python -m newsgrabber pruefen
    python -m newsgrabber init -c newsgrabber.yaml
    python -m newsgrabber db --aufraeumen

Ausgabe: ``erstellen`` schreibt die Pfade der erzeugten Dateien (eine pro Zeile) auf
stdout; alle Meldungen gehen nach stderr bzw. in die Logdatei. Exit-Codes: 0 = Erfolg,
1 = Fehler, 2 = Bericht erstellt, aber mit Warnungen (nur mit ``--streng``).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .config import DEFAULT_CONFIG_NAME, ConfigError, load_config, write_example
from .logsetup import configure_console_encoding, setup_logging
from .timeutil import DateFormatError, parse_date

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_WARNINGS = 2

ENV_CONFIG = "NEWSGRABBER_KONFIG"
FORMAT_ALIASES = {"md": "markdown", "markdown": "markdown", "html": "html", "json": "json"}

log = logging.getLogger("newsgrabber")


def _date(text: str) -> _dt.date:
    try:
        return parse_date(text)
    except DateFormatError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _format(text: str) -> str:
    value = FORMAT_ALIASES.get(text.strip().lower())
    if not value:
        raise argparse.ArgumentTypeError(f"Unbekanntes Format '{text}' (erlaubt: md, html, json)")
    return value


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("Zahl größer als 0 erwartet")
    return value


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--konfig", "--config", dest="config", type=Path, default=None,
                        help=f"Konfigurationsdatei (Standard: ${ENV_CONFIG} oder ./{DEFAULT_CONFIG_NAME}; "
                             "fehlt sie, gilt die Beispielkonfiguration)")
    parser.add_argument("-v", "--ausfuehrlich", "--verbose", dest="verbose", action="store_true",
                        help="Ausführliche Meldungen (DEBUG)")
    parser.add_argument("-q", "--ruhig", "--quiet", dest="quiet", action="store_true", help="Nur Warnungen und Fehler")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newsgrabber",
        description="Sammelt aktuelle Nachrichten aus seriösen Quellen und erstellt ein Morgenbriefing.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="BEFEHL")

    gen = sub.add_parser("erstellen", aliases=["generate"], help="Briefing für einen Tag erstellen")
    _add_common(gen)
    gen.add_argument("-d", "--datum", "--date", dest="date", type=_date, default=None,
                     help="Berichtstag (JJJJ-MM-TT, TT.MM.JJJJ, heute, morgen, gestern; Standard: heute)")
    gen.add_argument("--stunden", "--hours", dest="hours", type=float,
                     help="Zeitfenster in Stunden (Standard laut Konfiguration, z. B. 72 für montags)")
    gen.add_argument("-o", "--ausgabe", "--output", dest="output", type=Path,
                     help="Basisordner der Ausgabe (darin der Datumsordner)")
    gen.add_argument("-f", "--format", dest="formats", type=_format, action="append",
                     help="Ausgabeformat md, html oder json (mehrfach möglich; Standard laut Konfiguration)")
    gen.add_argument("--quelle", "--source", dest="sources", action="append", metavar="ID",
                     help="Nur diese Quelle(n) verwenden (mehrfach möglich)")
    gen.add_argument("--zusammenfassung", "--summarizer", dest="backend",
                     choices=["auto", "extraktiv", "extractive", "claude"],
                     help="Verfahren der Zusammenfassung (Standard laut Konfiguration)")
    gen.add_argument("--max-woerter", "--max-words", dest="max_words", type=_positive_int,
                     help="Höchstumfang des Berichts in Wörtern")
    gen.add_argument("--min-woerter", "--min-words", dest="min_words", type=_positive_int,
                     help="Mindestumfang (Zielbereich) des Berichts in Wörtern")
    gen.add_argument("--ohne-volltext", "--no-fulltext", dest="no_fulltext", action="store_true",
                     help="Keine Artikelseiten abrufen, nur Feeds/APIs auswerten")
    gen.add_argument("--ohne-db", "--no-db", dest="no_db", action="store_true",
                     help="SQLite-Datenbank nicht verwenden")
    gen.add_argument("--vorschau", "--preview", dest="preview", action="store_true",
                     help="Bericht nur ausgeben (stdout), keine Dateien schreiben, Datenbank unverändert")
    gen.add_argument("--streng", "--strict", dest="strict", action="store_true",
                     help=f"Exit-Code {EXIT_WARNINGS}, wenn Quellen ausgefallen sind oder Hinweise vorliegen")

    src = sub.add_parser("quellen", aliases=["sources"], help="Quellen auflisten und optional testen")
    _add_common(src)
    src.add_argument("--testen", "--test", dest="test", action="store_true", help="Jede Quelle einmal abrufen")
    src.add_argument("--quelle", "--source", dest="sources", action="append", metavar="ID",
                     help="Nur diese Quelle(n)")
    src.add_argument("--alle", "--all", dest="all", action="store_true", help="Auch deaktivierte Quellen")

    chk = sub.add_parser("pruefen", aliases=["check"], help="Konfiguration und Umgebung prüfen")
    _add_common(chk)

    init = sub.add_parser("init", help="Kommentierte Beispielkonfiguration anlegen")
    _add_common(init)
    init.add_argument("--ueberschreiben", "--force", dest="force", action="store_true",
                      help="Vorhandene Datei überschreiben")

    db = sub.add_parser("db", help="Datenbank anzeigen oder aufräumen")
    _add_common(db)
    db.add_argument("--aufraeumen", "--purge", dest="purge", action="store_true",
                    help="Einträge älter als database.retention_days (bzw. --tage) löschen")
    db.add_argument("--tage", "--days", dest="days", type=_positive_int, help="Aufbewahrungsdauer in Tagen")
    return parser


# ------------------------------------------------------------------ Hilfen
def _config_path(args) -> "tuple[Path, bool]":
    if args.config:
        return args.config, True
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env), True
    return Path.cwd() / DEFAULT_CONFIG_NAME, False


def _load(args):
    path, required = _config_path(args)
    config = load_config(path, required=required)
    lc = config.logging
    level = "DEBUG" if args.verbose else lc.level
    console = "WARNING" if args.quiet else level
    setup_logging(level, config.resolve(lc.file), lc.max_bytes, lc.backup_count, console)
    if config.path is None:
        log.info("Keine Konfigurationsdatei gefunden – Beispielkonfiguration wird verwendet "
                 "(eigene anlegen: python -m newsgrabber init)")
    for warning in config.warnings:
        log.warning(warning)
    return config


# ------------------------------------------------------------------ Befehle
def cmd_generate(args) -> int:
    from .pipeline import PipelineError, RunOptions, run

    config = _load(args)
    report_date = args.date or _dt.datetime.now(config.tz).date()
    backend = {"extraktiv": "extractive"}.get(args.backend, args.backend)
    options = RunOptions(
        date=report_date, hours=args.hours, output_dir=args.output,
        formats=list(dict.fromkeys(args.formats)) if args.formats else None,
        use_database=not args.no_db, fulltext=False if args.no_fulltext else None, backend=backend,
        max_words=args.max_words, min_words=args.min_words, source_ids=args.sources, preview=args.preview,
    )
    try:
        result = run(config, options)
    except PipelineError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    if args.preview:
        for fmt in (options.formats or config.report.formats):
            sys.stdout.write(result.preview[fmt])
    else:
        for path in result.files:
            print(path)
    for source in result.briefing.failed_sources:
        log.warning("Quelle ausgefallen: %s – %s", source.name, source.error)
    if args.strict and result.has_warnings:
        return EXIT_WARNINGS
    return EXIT_OK


def cmd_sources(args) -> int:
    config = _load(args)
    sources = config.sources if args.all or args.sources else config.enabled_sources()
    if args.sources:
        sources = [s for s in sources if s.id in set(args.sources)]
        if not sources:
            log.error("Keine passende Quelle gefunden")
            return EXIT_ERROR
    if not args.test:
        print(f"{'ID':<24} {'Typ':<15} {'Kategorie':<26} {'Qual.':>5}  {'aktiv':<5}  URL")
        for s in sources:
            print(f"{s.id:<24} {s.type:<15} {','.join(s.category):<26} {s.quality:>5.2f}  "
                  f"{'ja' if s.enabled else 'nein':<5}  {s.url}")
        return EXIT_OK

    from .collect import BrowserRenderer, CollectContext, HttpClient, run_source

    http = HttpClient(config.collection)
    browser = BrowserRenderer(config.collection.playwright, http, config.collection.user_agent) \
        if config.collection.playwright.enabled else None
    ctx = CollectContext(config=config, http=http, browser=browser)
    failures = 0
    now = _dt.datetime.now(config.tz)
    try:
        print(f"{'ID':<24} {'Status':<8} {'Einträge':>8} {'neueste Meldung':<18} {'Dauer':>6}  Hinweis")
        for source in sources:
            articles, result = run_source(source, ctx)
            dates = [a.published for a in articles if a.published]
            newest = max(dates).astimezone(config.tz).strftime("%d.%m. %H:%M") if dates else "–"
            note = result.error if not result.ok else ("Ersatzquelle verwendet" if result.used_fallback else "")
            if result.ok and dates and (now - max(dates)).total_seconds() > 3 * 86400:
                note = "neueste Meldung älter als 3 Tage"
            if result.ok and not articles:
                note = "keine Einträge"
            failures += 0 if result.ok else 1
            print(f"{source.id:<24} {'OK' if result.ok else 'FEHLER':<8} {result.items:>8} {newest:<18} "
                  f"{result.duration:>5.1f}s  {note}")
    finally:
        if browser is not None:
            browser.close()
    print(f"\n{len(sources) - failures} von {len(sources)} Quellen erreichbar.")
    return EXIT_WARNINGS if failures else EXIT_OK


def cmd_check(args) -> int:
    import importlib.util

    config = _load(args)
    print(f"Konfiguration:   {config.path or 'Beispielkonfiguration (keine eigene Datei)'}")
    print(f"Zeitzone:        {config.timezone}, Zeitfenster {config.window.hours:g} h"
          + (f" bis {config.window.end_time} Uhr" if config.window.end_time else ""))
    print(f"Kategorien:      {', '.join(f'{c.title} (max. {c.max_items})' for c in config.categories)}")
    enabled = config.enabled_sources()
    print(f"Quellen:         {len(enabled)} aktiv, {len(config.sources) - len(enabled)} deaktiviert")
    for category in config.categories:
        count = sum(1 for s in enabled if category.key in s.category)
        if count == 0:
            print(f"  WARNUNG: Keine aktive Quelle für Kategorie '{category.title}'")
    print(f"Ausgabe:         {config.resolve(config.report.output_dir)} ({', '.join(config.report.formats)}), "
          f"{config.report.min_words}–{config.report.max_words} Wörter")
    print(f"Datenbank:       {config.resolve(config.database.path) if config.database.enabled else 'deaktiviert'}")
    print(f"Logdatei:        {config.resolve(config.logging.file) or 'keine'}")
    playwright = importlib.util.find_spec("playwright") is not None
    print(f"Playwright:      {'installiert' if playwright else 'nicht installiert (optional)'}"
          + ("" if config.collection.playwright.enabled else " – in der Konfiguration deaktiviert"))
    anthropic = importlib.util.find_spec("anthropic") is not None
    key = bool(os.environ.get(config.summarizer.claude.api_key_env))
    print(f"Zusammenfassung: {config.summarizer.backend}; Claude-SDK {'installiert' if anthropic else 'fehlt'}, "
          f"{config.summarizer.claude.api_key_env} {'gesetzt' if key else 'nicht gesetzt'}")
    if config.summarizer.backend == "claude" and not (anthropic and key):
        print("  WARNUNG: backend 'claude' gewählt, aber SDK oder API-Schlüssel fehlen – es wird extraktiv zusammengefasst")
    print("Konfiguration ist gültig." if not config.warnings else
          f"Konfiguration ist gültig ({len(config.warnings)} Warnung(en), siehe oben).")
    return EXIT_OK


def cmd_init(args) -> int:
    configure_console_encoding()
    target = args.config or Path(os.environ.get(ENV_CONFIG) or DEFAULT_CONFIG_NAME)
    written = write_example(target, force=args.force)
    print(f"Beispielkonfiguration angelegt: {written}")
    print(f"Prüfen mit:  python -m newsgrabber pruefen -c \"{written}\"")
    print(f"Testen mit:  python -m newsgrabber quellen --testen -c \"{written}\"")
    return EXIT_OK


def cmd_db(args) -> int:
    from .storage import open_storage

    config = _load(args)
    if not config.database.enabled:
        print("Datenbank ist deaktiviert (database.enabled: false).")
        return EXIT_OK
    storage = open_storage(True, config.resolve(config.database.path))
    try:
        if args.purge:
            days = args.days or config.database.retention_days
            counts = storage.purge(_dt.datetime.now(config.tz), days)
            print(f"Aufgeräumt (älter als {days} Tage): "
                  + ", ".join(f"{v} {k}" for k, v in counts.items()))
        print(f"Datenbank: {storage.path}")
        for table, count in storage.stats().items():
            print(f"  {table:<16} {count:>7}")
        status = storage.source_status()
        failing = {k: v for k, v in status.items() if v["consecutive_failures"]}
        if failing:
            print("Quellen mit Fehlern:")
            for source_id, row in sorted(failing.items()):
                print(f"  {source_id:<24} {row['consecutive_failures']}x in Folge – {row['last_error'] or ''}")
    finally:
        storage.close()
    return EXIT_OK


COMMANDS = {
    "erstellen": cmd_generate, "generate": cmd_generate,
    "quellen": cmd_sources, "sources": cmd_sources,
    "pruefen": cmd_check, "check": cmd_check,
    "init": cmd_init,
    "db": cmd_db,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_console_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_ERROR
    try:
        return COMMANDS[args.command](args)
    except ConfigError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("Abgebrochen.", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 – letzte Instanz: Fehler protokollieren, sauberer Exit-Code
        logging.getLogger("newsgrabber").exception("Unerwarteter Fehler: %s", exc)
        return EXIT_ERROR
