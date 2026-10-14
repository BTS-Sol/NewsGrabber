"""Claude-Zusammenfassung mit einem Fake-Client (keine echten API-Aufrufe)."""

import datetime as dt
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")

from newsgrabber.config import ClaudeConfig  # noqa: E402
from newsgrabber.models import Article, Story  # noqa: E402
from newsgrabber.process.keywords import DocumentFrequencies  # noqa: E402
from newsgrabber.storage import NullStorage, Storage  # noqa: E402
from newsgrabber.summarize.claude import FALLBACK_BETA, ClaudeSummarizer  # noqa: E402
from newsgrabber.summarize.extractive import ExtractiveSummarizer  # noqa: E402
from conftest import BERLIN, NOW  # noqa: E402

ANSWER = {"headline": "Bundestag beschließt Pflegereform", "summary": "Der Bundestag hat eine Reform beschlossen.",
          "facts": ["Beitragssatz steigt auf 3,9 Prozent."], "keywords": ["Pflege", "Bundestag"]}


class FakeMessages:
    def __init__(self, stop_reason="end_turn", payload=None):
        self.calls = []
        self.stop_reason = stop_reason
        self.payload = payload or ANSWER

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def make(monkeypatch, storage=None, stop_reason="end_turn", fallbacks=True):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    summarizer = ClaudeSummarizer(ClaudeConfig(fallbacks=fallbacks, max_workers=2),
                                  ExtractiveSummarizer(df=DocumentFrequencies()), storage or NullStorage(), NOW,
                                  tz=BERLIN)
    messages = FakeMessages(stop_reason)
    summarizer.client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))
    return summarizer, messages


def story():
    return Story([Article(source_id="t", source_name="tagesschau", url="https://t.de/1", title="Pflegereform beschlossen",
                          teaser="Der Bundestag hat die Pflegereform beschlossen. Der Beitrag steigt.",
                          published=dt.datetime(2026, 10, 13, 15, tzinfo=dt.timezone.utc), quality=0.95)])


def test_request_and_parsing(monkeypatch):
    summarizer, messages = make(monkeypatch)
    [result] = summarizer.summarize_all([(story(), 80)])
    assert result.headline == ANSWER["headline"] and result.method == "claude"
    assert result.facts == ANSWER["facts"] and result.keywords == ANSWER["keywords"]
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "medium"
    assert "<quelle nr=\"1\" name=\"tagesschau\"" in call["messages"][0]["content"]
    assert "etwa 80 Wörter" in call["messages"][0]["content"]


def test_without_server_side_fallbacks(monkeypatch):
    summarizer, messages = make(monkeypatch, fallbacks=False)
    summarizer.summarize_all([(story(), 80)])
    assert "betas" not in messages.calls[0] and "fallbacks" not in messages.calls[0]


def test_refusal_falls_back_to_extractive(monkeypatch):
    summarizer, _ = make(monkeypatch, stop_reason="refusal")
    [result] = summarizer.summarize_all([(story(), 80)])
    assert result.method == "extraktiv" and summarizer.failures == 1


def test_results_are_cached(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    summarizer, messages = make(monkeypatch, storage=storage)
    summarizer.summarize_all([(story(), 80)])
    again, messages2 = make(monkeypatch, storage=storage)
    [result] = again.summarize_all([(story(), 80)])
    assert result.headline == ANSWER["headline"] and not messages2.calls and again.cache_hits == 1
    storage.close()
