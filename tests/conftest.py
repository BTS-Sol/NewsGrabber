"""Gemeinsame Test-Hilfen: lokaler HTTP-Server mit Testdaten und Testkonfiguration."""

from __future__ import annotations

import datetime as _dt
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BERLIN = ZoneInfo("Europe/Berlin")
NOW = _dt.datetime(2026, 10, 14, 6, 0, tzinfo=BERLIN)


class _Handler(BaseHTTPRequestHandler):
    hits: dict = {}

    def log_message(self, *args):  # Testausgabe ruhig halten
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        type(self).hits[path] = type(self).hits.get(path, 0) + 1
        if path == "/kaputt.xml":
            self.send_response(503)
            self.end_headers()
            return
        file = (FIXTURES / path.lstrip("/")).resolve()
        if not str(file).startswith(str(FIXTURES)) or not file.is_file():
            self.send_response(404)
            self.end_headers()
            return
        base = f"http://{self.headers['Host']}"
        body = file.read_text(encoding="utf-8").replace("{BASE}", base).encode("utf-8")
        ctype = {".rss": "application/rss+xml", ".atom": "application/atom+xml", ".json": "application/json",
                 ".txt": "text/plain"}.get(file.suffix) or mimetypes.guess_type(file.name)[0] or "text/html"
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="session")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def hits():
    _Handler.hits.clear()
    return _Handler.hits


def fixture_sources(base: str) -> list:
    return [
        {"id": "tagesschau_ausland", "name": "tagesschau", "type": "tagesschau_api",
         "url": f"{base}/tagesschau_ausland.json", "category": "weltgeschehen", "quality": 0.95},
        {"id": "tagesschau_inland", "name": "tagesschau", "type": "tagesschau_api",
         "url": f"{base}/tagesschau_inland.json", "category": "deutschland", "quality": 0.95},
        {"id": "dlf", "name": "Deutschlandfunk", "type": "rss", "url": f"{base}/dlf.rss",
         "category": ["weltgeschehen", "deutschland"], "quality": 0.95},
        {"id": "bbc", "name": "BBC News", "type": "rss", "url": f"{base}/bbc.rss", "category": "weltgeschehen",
         "language": "en", "quality": 0.9},
        {"id": "heise", "name": "heise online", "type": "rss", "url": f"{base}/heise.atom", "category": "it",
         "quality": 0.9},
        {"id": "golem", "name": "Golem.de", "type": "rss", "url": f"{base}/golem.rss", "category": "it",
         "quality": 0.85},
        {"id": "hackernews", "name": "Hacker News", "type": "hackernews", "url": f"{base}/hn.json",
         "category": "it", "language": "en", "quality": 0.6, "options": {"min_points": 100}},
        {"id": "liste", "name": "Kurzmeldungen", "type": "html", "url": f"{base}/artikel/liste.html",
         "category": "deutschland", "quality": 0.9,
         "options": {"link_selector": "ul.meldungen a", "include_pattern": "/artikel/", "render": "static"}},
        {"id": "kaputt", "name": "Kaputte Quelle", "type": "rss", "url": f"{base}/kaputt.xml",
         "category": "weltgeschehen"},
    ]


def write_config(tmp_path: Path, base: str, **sections) -> Path:
    data = {
        "collection": {"per_host_interval": 0, "retries": 1, "backoff": 0.01, "timeout": 5,
                       "playwright": {"enabled": False}},
        "database": {"path": str(tmp_path / "newsgrabber.sqlite3")},
        "logging": {"file": None},
        "report": {"output_dir": str(tmp_path / "ausgabe"), "min_words": 300, "max_words": 3000},
        "summarizer": {"backend": "extractive"},
        "sources": fixture_sources(base),
    }
    for key, value in sections.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    path = tmp_path / "newsgrabber.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def config_file(tmp_path, server):
    return write_config(tmp_path, server)
