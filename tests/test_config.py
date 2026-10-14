import pytest
import yaml

from newsgrabber.config import ConfigError, build_config, deep_merge, load_config, read_file, EXAMPLE_CONFIG, \
    write_example


def test_example_config_is_valid_and_complete():
    config = load_config(None, required=False)
    assert [c.key for c in config.categories] == ["weltgeschehen", "deutschland", "it"]
    assert set(config.categories[2].topics) == {"Cybersecurity", "KI", "Softwareentwicklung", "Open Source"}
    assert len(config.enabled_sources()) >= 20
    assert not config.warnings
    for category in config.categories:
        assert any(category.key in s.category for s in config.enabled_sources())


def test_user_config_overrides_and_replaces_lists(tmp_path):
    path = tmp_path / "n.yaml"
    path.write_text(yaml.safe_dump({
        "report": {"max_words": 2500},
        "sources": [{"id": "x", "url": "https://example.org/feed", "category": "it"}],
        "unbekannt": 1,
    }), encoding="utf-8")
    config = load_config(path)
    assert config.report.max_words == 2500 and config.report.min_words == 2000
    assert [s.id for s in config.sources] == ["x"]
    assert config.sources[0].category == ["it"]
    assert config.base_dir == tmp_path
    assert config.resolve("ausgabe") == tmp_path / "ausgabe"
    assert any("unbekannt" in w for w in config.warnings)


def _with(**changes):
    return deep_merge(read_file(EXAMPLE_CONFIG), changes)


@pytest.mark.parametrize("changes, message", [
    ({"timezone": "Mars/Olympus"}, "Zeitzone"),
    ({"report": {"min_words": 4000, "max_words": 3000}}, "min_words"),
    ({"report": {"formats": ["pdf"]}}, "report.formats"),
    ({"sources": [{"id": "a", "url": "https://x", "category": "sport"}]}, "unbekannte Kategorie"),
    ({"sources": [{"id": "a", "url": "https://x", "category": "it", "type": "ftp"}]}, "unbekannter Typ"),
    ({"sources": [{"id": "a", "url": "https://x", "category": "it"}, {"id": "a", "url": "https://y",
                                                                      "category": "it"}]}, "nicht eindeutig"),
    ({"window": {"end_time": "25:00"}}, "Uhrzeit"),
    ({"collection": {"timeout": "schnell"}}, "Zahl erwartet"),
    ({"summarizer": {"backend": "gpt"}}, "summarizer.backend"),
])
def test_invalid_configs_are_rejected(changes, message):
    with pytest.raises(ConfigError, match=message):
        build_config(_with(**changes))


def test_missing_explicit_config_fails(tmp_path):
    with pytest.raises(ConfigError, match="nicht gefunden"):
        load_config(tmp_path / "fehlt.yaml")


def test_write_example(tmp_path):
    target = write_example(tmp_path / "neu.yaml")
    assert load_config(target).path == target.resolve()
    with pytest.raises(ConfigError, match="existiert bereits"):
        write_example(target)
