"""End-to-End: kompletter Lauf gegen den lokalen Testserver."""

from __future__ import annotations

import datetime as _dt
import json

import pytest

from conftest import NOW, write_config
from newsgrabber.config import load_config
from newsgrabber.pipeline import PipelineError, RunOptions, run


def _run(config_file, **kwargs):
    options = RunOptions(date=_dt.date(2026, 10, 14), current_time=NOW, **kwargs)
    return run(load_config(config_file), options)


def _items(briefing):
    return {item.headline: item for item in briefing.all_items()}


def test_full_run_writes_all_formats(config_file, tmp_path):
    result = _run(config_file)
    names = sorted(p.name for p in result.files)
    assert names == ["morgenbriefing_2026-10-14.html", "morgenbriefing_2026-10-14.json",
                     "morgenbriefing_2026-10-14.md"]
    assert all(p.parent == tmp_path / "ausgabe" / "2026-10-14" for p in result.files)

    data = json.loads((tmp_path / "ausgabe" / "2026-10-14" / "morgenbriefing_2026-10-14.json").read_text("utf-8"))
    assert data["schema_version"] == 1
    assert [c["key"] for c in data["categories"]] == ["weltgeschehen", "deutschland", "it"]
    assert all(c["items"] for c in data["categories"])
    assert data["window"]["end"] == "2026-10-14T06:00:00+02:00"
    for category in data["categories"]:
        for item in category["items"]:
            assert item["headline"] and item["summary"] and item["sources"]

    markdown = (tmp_path / "ausgabe" / "2026-10-14" / "morgenbriefing_2026-10-14.md").read_text("utf-8")
    assert markdown.startswith("# Morgenbriefing – Mittwoch, 14. Oktober 2026")
    assert "## Auf einen Blick" in markdown and "## IT & Technologie" in markdown
    html = (tmp_path / "ausgabe" / "2026-10-14" / "morgenbriefing_2026-10-14.html").read_text("utf-8")
    assert html.startswith("<!DOCTYPE html>") and 'id="it-1"' in html


def test_duplicates_are_merged_across_sources(config_file):
    briefing = _run(config_file).briefing
    climate = [i for i in briefing.all_items() if "Klima" in i.headline or "climate" in i.headline.lower()]
    assert len(climate) == 1, [i.headline for i in briefing.all_items()]
    names = {s.name for s in climate[0].sources}
    assert {"tagesschau", "Deutschlandfunk"} <= names
    exim = [i for i in briefing.all_items() if "Exim" in i.headline]
    assert len(exim) == 1
    assert {"heise online", "Golem.de"} <= {s.name for s in exim[0].sources}
    # Hacker News verlinkt denselben heise-Artikel: kein eigener Eintrag, kein doppelter Link
    assert not any("vulnerability" in i.headline for i in briefing.all_items())
    assert len({s.url for s in exim[0].sources}) == len(exim[0].sources)


def test_filters_window_video_negative_and_categories(config_file):
    briefing = _run(config_file).briefing
    headlines = " | ".join(i.headline for i in briefing.all_items())
    assert "Alte Meldung" not in headlines  # außerhalb des Zeitfensters
    assert "Video" not in headlines  # tagesschau-Video ohne Text
    assert "Angebot" not in headlines and "Bundesliga" not in headlines  # Negativ-Schlagwörter
    sections = {s.key: s for s in briefing.sections}
    assert any("Pflege" in i.headline for i in sections["deutschland"].items)
    assert any("Bolivien" in i.headline for i in sections["weltgeschehen"].items)  # gemischte Quelle -> Welt
    topics = {i.topic for i in sections["it"].items}
    assert "Cybersecurity" in topics and "Open Source" in topics


def test_summaries_contain_facts_and_keywords(config_file):
    items = _run(config_file).briefing.all_items()
    exim = next(i for i in items if "Exim" in i.headline)
    assert "4.99.2" in exim.summary + " ".join(exim.facts)
    assert exim.keywords
    assert all(i.method == "extraktiv" for i in items)


def test_failed_source_is_reported(config_file, tmp_path):
    result = _run(config_file)
    failed = {r.source_id for r in result.briefing.failed_sources}
    assert failed == {"kaputt"}
    markdown = (tmp_path / "ausgabe" / "2026-10-14" / "morgenbriefing_2026-10-14.md").read_text("utf-8")
    assert "Nicht erreichbare Quellen:** Kaputte Quelle" in markdown


def test_robots_txt_is_respected(config_file, hits):
    _run(config_file)
    assert "/robots.txt" in hits
    assert "/artikel/privat/geheim.html" not in hits
    assert hits.get("/kaputt.xml") == 2  # ein Wiederholungsversuch


def test_word_budget_is_enforced(tmp_path, server):
    config_file = write_config(tmp_path, server, report={"output_dir": str(tmp_path / "out"), "min_words": 200,
                                                         "max_words": 450})
    result = _run(config_file)
    assert result.briefing.word_count <= 450
    assert all(len(s.items) >= 1 for s in result.briefing.sections)


def test_repeated_stories_are_penalized_next_day(config_file):
    first = _run(config_file).briefing
    assert all(i.history == "" for i in first.all_items())
    options = RunOptions(date=_dt.date(2026, 10, 15), current_time=NOW + _dt.timedelta(days=1), hours=48)
    second = run(load_config(config_file), options).briefing
    histories = {i.headline: i.history for i in second.all_items()}
    assert "wiederholung" in histories.values()


def test_preview_writes_nothing(config_file, tmp_path):
    result = _run(config_file, preview=True)
    assert result.files == []
    assert not (tmp_path / "ausgabe").exists()
    assert not (tmp_path / "newsgrabber.sqlite3").exists()
    assert result.preview["markdown"].startswith("# Morgenbriefing")


def test_without_database_and_fulltext(config_file, tmp_path, hits):
    result = _run(config_file, use_database=False, fulltext=False)
    assert result.files
    assert not (tmp_path / "newsgrabber.sqlite3").exists()
    assert "/artikel/heise-exim.html" not in hits


def test_no_reachable_source_raises(tmp_path, server):
    config_file = write_config(tmp_path, server, sources=[
        {"id": "kaputt", "type": "rss", "url": f"{server}/kaputt.xml", "category": "weltgeschehen"}])
    with pytest.raises(PipelineError):
        _run(config_file)


def test_preview_reads_history_but_does_not_write(config_file, tmp_path):
    import sqlite3

    _run(config_file)
    db = tmp_path / "newsgrabber.sqlite3"
    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    options = RunOptions(date=_dt.date(2026, 10, 15), current_time=NOW + _dt.timedelta(days=1), hours=48,
                         preview=True)
    result = run(load_config(config_file), options)
    assert "wiederholung" in {i.history for i in result.briefing.all_items()}
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM reports").fetchone()[0] == before == 1
    assert not (tmp_path / "ausgabe" / "2026-10-15").exists()
