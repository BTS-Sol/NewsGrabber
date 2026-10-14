import datetime as dt
import json
import os

import pytest

from newsgrabber.cli import main
from newsgrabber.collect import BrowserRenderer, CollectContext, HttpClient, run_source
from newsgrabber.collect.browser import playwright_installed
from newsgrabber.config import load_config
from newsgrabber.models import Article, SourceResult
from newsgrabber.storage import Storage
from conftest import write_config

UTC = dt.timezone.utc


def test_storage_estimates_dates_and_tracks_failures(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    first = dt.datetime(2026, 10, 12, 6, tzinfo=UTC)
    undated = Article(source_id="s", source_name="S", url="https://s.de/1", title="Ohne Datum")
    storage.remember_articles([undated], first)
    later = Article(source_id="s", source_name="S", url="https://s.de/1", title="Ohne Datum")
    storage.remember_articles([later], first + dt.timedelta(days=2))
    assert later.published == first and later.date_estimated  # erster Fundzeitpunkt, nicht „jetzt“

    for _ in range(3):
        result = SourceResult(source_id="x", name="X", ok=False, error="HTTP 404")
        storage.update_source_status([result], first)
    assert result.consecutive_failures == 3
    ok = SourceResult(source_id="x", name="X", ok=True, items=5)
    storage.update_source_status([ok], first)
    assert ok.consecutive_failures == 0 and storage.source_status()["x"]["last_items"] == 5

    counts = storage.purge(first + dt.timedelta(days=200), 90)
    assert counts["artikel"] == 1
    storage.close()


def _browser_options() -> "dict | None":
    """Startoptionen, mit denen Chromium hier läuft (oder None)."""
    if not playwright_installed():
        return None
    from playwright.sync_api import sync_playwright
    candidates = [{}]
    if os.path.exists("/opt/pw-browsers/chromium"):
        candidates.append({"executable_path": "/opt/pw-browsers/chromium"})
    with sync_playwright() as p:
        for options in candidates:
            try:
                p.chromium.launch(**options).close()
                return options
            except Exception:  # noqa: BLE001
                continue
    return None


def test_playwright_renders_dynamic_listing(tmp_path, server):
    options = _browser_options()
    if options is None:
        pytest.skip("Playwright/Chromium nicht verfügbar")
    config = load_config(write_config(tmp_path, server, sources=[{
        "id": "dyn", "type": "html", "url": f"{server}/artikel/dynamisch.html", "category": "deutschland",
        "options": {"link_selector": "a.meldung", "render": "auto"}}],
        collection={"playwright": {"enabled": True, "extra_wait_ms": 0, **options}}))
    http = HttpClient(config.collection)
    browser = BrowserRenderer(config.collection.playwright, http, config.collection.user_agent)
    try:
        articles, result = run_source(config.sources[0], CollectContext(config=config, http=http, browser=browser))
    finally:
        browser.close()
    assert result.ok, result.error
    assert [a.title for a in articles] == ["Ausschuss berät über Digitalisierung der Verwaltung"]
    assert browser.pages_rendered == 1


def test_cli_generate_check_sources_and_init(tmp_path, server, capsys):
    config_file = write_config(tmp_path, server)
    code = main(["erstellen", "-c", str(config_file), "--datum", "2026-10-14", "--stunden", "48", "-q"])
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0 and len(out) == 3 and all(line.endswith((".md", ".html", ".json")) for line in out)
    data = json.loads(next(open(p, encoding="utf-8").read() for p in out if p.endswith(".json")))
    assert data["date"] == "2026-10-14"

    code = main(["erstellen", "-c", str(config_file), "--datum", "14.10.2026", "--stunden", "48", "--vorschau",
                 "-f", "md", "--streng", "-q"])
    assert code == 2  # kaputte Quelle -> Warnung
    assert capsys.readouterr().out.startswith("# Morgenbriefing – Mittwoch, 14. Oktober 2026")

    assert main(["quellen", "-c", str(config_file), "--testen"]) == 2
    table = capsys.readouterr().out
    assert "kaputt" in table and "FEHLER" in table and "heise" in table

    assert main(["pruefen", "-c", str(config_file)]) == 0
    assert "Konfiguration ist gültig" in capsys.readouterr().out

    target = tmp_path / "neu" / "newsgrabber.yaml"
    assert main(["init", "-c", str(target)]) == 0 and target.exists()
    assert main(["init", "-c", str(target)]) == 1  # existiert bereits

    assert main(["db", "-c", str(config_file)]) == 0
    assert "reports" in capsys.readouterr().out


def test_cli_rejects_bad_arguments(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["erstellen", "--datum", "irgendwann"])
    assert main(["erstellen", "-c", str(tmp_path / "fehlt.yaml")]) == 1
