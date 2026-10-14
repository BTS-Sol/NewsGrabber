import time

import pytest

from newsgrabber.collect import CollectContext, HttpClient, run_source
from newsgrabber.collect.http import FetchError, HostRateLimiter, RobotsDisallowed
from newsgrabber.config import CollectionConfig, load_config
from conftest import write_config


def client(**kw):
    return HttpClient(CollectionConfig(per_host_interval=0, retries=1, backoff=0.01, timeout=5, **kw))


def test_robots_txt_blocks_disallowed_paths(server):
    http = client()
    with pytest.raises(RobotsDisallowed):
        http.get(f"{server}/artikel/privat/geheim.html")
    assert http.get(f"{server}/artikel/heise-kernel.html").status == 200


def test_robots_can_be_disabled_but_is_on_by_default(server):
    assert client().config.respect_robots_txt
    assert client(respect_robots_txt=False).get(f"{server}/artikel/privat/geheim.html").status == 200


def test_retry_on_server_error_then_fail(server, hits):
    with pytest.raises(FetchError) as info:
        client().get(f"{server}/kaputt.xml")
    assert info.value.status == 503
    assert hits["/kaputt.xml"] == 2


def test_no_retry_on_404(server, hits):
    with pytest.raises(FetchError) as info:
        client().get(f"{server}/gibtsnicht.xml")
    assert info.value.status == 404 and hits["/gibtsnicht.xml"] == 1


def test_rate_limiter_spaces_requests_per_host():
    limiter = HostRateLimiter(0.05)
    started = time.monotonic()
    for _ in range(4):
        limiter.wait("a.example")
    limiter.wait("b.example")  # anderer Host: keine Wartezeit
    assert 0.14 <= time.monotonic() - started < 0.5


def test_fallback_source_is_used(tmp_path, server):
    config = load_config(write_config(tmp_path, server, sources=[{
        "id": "x", "type": "rss", "url": f"{server}/kaputt.xml", "category": "it",
        "fallback": {"url": f"{server}/heise.atom"}}]))
    ctx = CollectContext(config=config, http=HttpClient(config.collection))
    articles, result = run_source(config.sources[0], ctx)
    assert result.ok and result.used_fallback and len(articles) == 4
