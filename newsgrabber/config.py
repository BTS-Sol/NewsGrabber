"""Konfiguration laden, mit Standardwerten zusammenführen und prüfen.

Grundlage ist die mitgelieferte, kommentierte Beispielkonfiguration
(``resources/beispiel_konfiguration.yaml``). Eine eigene Konfigurationsdatei muss
nur die abweichenden Werte enthalten; ``sources`` und ``categories`` ersetzen –
falls angegeben – die Listen der Beispielkonfiguration vollständig.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
import json
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .timeutil import DateFormatError, get_zone, parse_clock

DEFAULT_CONFIG_NAME = "newsgrabber.yaml"
RESOURCE_DIR = Path(__file__).resolve().parent / "resources"
EXAMPLE_CONFIG = RESOURCE_DIR / "beispiel_konfiguration.yaml"

SOURCE_TYPES = ("rss", "tagesschau_api", "hackernews", "json_api", "html")
REPORT_FORMATS = ("markdown", "html", "json")
SUMMARIZER_BACKENDS = ("auto", "extractive", "claude")


class ConfigError(ValueError):
    """Ungültige oder unvollständige Konfiguration."""


# --------------------------------------------------------------------- Abschnitte
@dataclass
class WindowConfig:
    hours: float = 24.0
    end_time: Optional[str] = None  # Stichzeit „HH:MM“ am Berichtstag, leer = bis jetzt


@dataclass
class FulltextConfig:
    enabled: bool = True
    candidates_per_category: int = 16
    max_articles: int = 80
    min_chars: int = 400


@dataclass
class PlaywrightConfig:
    enabled: bool = True
    article_fallback: bool = True
    timeout: float = 30.0
    wait_until: str = "domcontentloaded"
    extra_wait_ms: int = 1500
    channel: Optional[str] = None  # z. B. "msedge" oder "chrome": installierten Browser verwenden
    executable_path: Optional[str] = None


@dataclass
class CollectionConfig:
    user_agent: str = "NewsGrabber/1.0 (+https://github.com/BTS-Sol/NewsGrabber)"
    timeout: float = 20.0
    retries: int = 2
    backoff: float = 2.0
    max_workers: int = 6
    per_host_interval: float = 1.5
    respect_robots_txt: bool = True
    max_crawl_delay: float = 30.0
    max_response_mb: float = 5.0
    max_items_per_source: int = 40
    fulltext: FulltextConfig = field(default_factory=FulltextConfig)
    playwright: PlaywrightConfig = field(default_factory=PlaywrightConfig)


@dataclass
class ProcessingConfig:
    dedup_threshold: float = 0.25
    min_title_words: int = 3
    max_sentences: int = 40
    boilerplate_patterns: list = field(default_factory=list)


@dataclass
class ScoringConfig:
    weights: dict = field(default_factory=lambda: {
        "recency": 0.30, "relevance": 0.30, "quality": 0.20, "coverage": 0.20})
    recency_half_life_hours: float = 10.0
    coverage_saturation: int = 4
    undated_penalty: float = 0.7
    negative_keywords: list = field(default_factory=list)
    negative_penalty: float = 0.3
    history_days: int = 3
    repeat_penalty: float = 0.35
    update_penalty: float = 0.8
    topic_diversity: float = 0.85
    min_score: float = 0.2
    no_text_penalty: float = 0.6


@dataclass
class ClaudeConfig:
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_tokens: int = 16000
    max_workers: int = 4
    timeout: float = 180.0
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_input_words: int = 2500
    fallbacks: bool = True


@dataclass
class SummarizerConfig:
    backend: str = "auto"
    language: str = "de"
    max_words_per_source: int = 70
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)


@dataclass
class ReportConfig:
    title: str = "Morgenbriefing"
    output_dir: str = "ausgabe"
    folder: str = "{date:%Y-%m-%d}"
    filename: str = "morgenbriefing_{date:%Y-%m-%d}"
    formats: list = field(default_factory=lambda: ["markdown", "html", "json"])
    min_words: int = 2000
    max_words: int = 3000
    top_stories: int = 5
    templates_dir: Optional[str] = None
    show_scores: bool = False
    show_source_status: bool = True


@dataclass
class DatabaseConfig:
    enabled: bool = True
    path: str = "newsgrabber.sqlite3"
    retention_days: int = 90


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Optional[str] = "logs/newsgrabber.log"
    max_bytes: int = 2_000_000
    backup_count: int = 7


@dataclass
class CategoryConfig:
    key: str
    title: str = ""
    max_items: int = 8
    min_items: int = 2
    weight: float = 1.0
    keywords: list = field(default_factory=list)
    topics: dict = field(default_factory=dict)


@dataclass
class SourceConfig:
    id: str
    name: str = ""
    type: str = "rss"
    url: str = ""
    category: list = field(default_factory=list)
    language: str = "de"
    quality: float = 0.7
    enabled: bool = True
    fulltext: bool = True
    max_items: Optional[int] = None
    options: dict = field(default_factory=dict)
    fallback: Optional[dict] = None

    @property
    def host(self) -> str:
        from urllib.parse import urlsplit
        return urlsplit(self.url).hostname or ""


@dataclass
class Config:
    timezone: str = "Europe/Berlin"
    window: WindowConfig = field(default_factory=WindowConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    summarizer: SummarizerConfig = field(default_factory=SummarizerConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    categories: list = field(default_factory=list)  # list[CategoryConfig]
    sources: list = field(default_factory=list)  # list[SourceConfig]
    # nicht aus der Datei:
    path: Optional[Path] = None
    base_dir: Path = field(default_factory=Path.cwd)
    warnings: list = field(default_factory=list)

    # ------------------------------------------------------------ Hilfen
    @property
    def tz(self):
        return get_zone(self.timezone)

    @property
    def end_time(self) -> Optional[_dt.time]:
        return parse_clock(self.window.end_time) if self.window.end_time else None

    def resolve(self, value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else (self.base_dir / path)

    def category(self, key: str) -> Optional[CategoryConfig]:
        return next((c for c in self.categories if c.key == key), None)

    def enabled_sources(self) -> list:
        return [s for s in self.sources if s.enabled]


_LIST_SECTIONS = {"categories": CategoryConfig, "sources": SourceConfig}
_IGNORED_TOP_KEYS = {"version"}


# --------------------------------------------------------------------- Laden
def read_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(f"Konfiguration '{path}' kann nicht gelesen werden: {exc}") from exc
    try:
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Konfiguration '{path}' ist fehlerhaft: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Konfiguration '{path}' muss ein Objekt (Schlüssel: Wert) enthalten")
    return data


def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in _LIST_SECTIONS or not isinstance(value, dict) or not isinstance(result.get(key), dict):
            result[key] = copy.deepcopy(value)
        else:
            result[key] = deep_merge(result[key], value)
    return result


def load_config(path: Optional[Path] = None, required: bool = True) -> Config:
    """Lädt die Beispielkonfiguration und überlagert sie mit ``path``.

    Ist ``required`` falsch und existiert ``path`` nicht, wird nur die
    Beispielkonfiguration verwendet (Ausgabe relativ zum aktuellen Ordner).
    """
    data = read_file(EXAMPLE_CONFIG)
    base_dir = Path.cwd()
    used_path = None
    if path is not None:
        path = Path(path).expanduser()
        if path.exists():
            data = deep_merge(data, read_file(path))
            base_dir = path.resolve().parent
            used_path = path.resolve()
        elif required:
            raise ConfigError(f"Konfigurationsdatei '{path}' nicht gefunden "
                              f"(anlegen mit: python -m newsgrabber init -c \"{path}\")")
    config = build_config(data)
    config.path = used_path
    config.base_dir = base_dir
    return config


def build_config(data: dict) -> Config:
    warnings: list = []
    config = _build(Config, {k: v for k, v in data.items()
                             if k not in _LIST_SECTIONS and k not in _IGNORED_TOP_KEYS},
                    "", warnings, skip={"path", "base_dir", "warnings", "categories", "sources"})
    config.categories = [_build_category(item, i, warnings) for i, item in enumerate(data.get("categories") or [])]
    config.sources = [_build_source(item, i, warnings) for i, item in enumerate(data.get("sources") or [])]
    config.warnings = warnings
    validate(config)
    return config


def _build_category(item: Any, index: int, warnings: list) -> CategoryConfig:
    if not isinstance(item, dict) or not item.get("key"):
        raise ConfigError(f"categories[{index}]: Objekt mit 'key' erwartet")
    category = _build(CategoryConfig, item, f"categories[{item['key']}]", warnings)
    category.title = category.title or category.key
    category.keywords = [str(k) for k in category.keywords or []]
    topics = {}
    for name, keywords in (category.topics or {}).items():
        if not isinstance(keywords, list):
            raise ConfigError(f"categories[{category.key}].topics.{name}: Liste von Schlagwörtern erwartet")
        topics[str(name)] = [str(k) for k in keywords]
    category.topics = topics
    return category


def _build_source(item: Any, index: int, warnings: list) -> SourceConfig:
    if not isinstance(item, dict) or not item.get("id"):
        raise ConfigError(f"sources[{index}]: Objekt mit 'id' erwartet")
    item = dict(item)
    if isinstance(item.get("category"), str):
        item["category"] = [item["category"]]
    source = _build(SourceConfig, item, f"sources[{item['id']}]", warnings)
    source.name = source.name or source.id
    return source


def _build(cls, data: dict, where: str, warnings: list, skip: frozenset = frozenset()):
    if not isinstance(data, dict):
        raise ConfigError(f"{where or 'Konfiguration'}: Objekt erwartet, gefunden: {type(data).__name__}")
    hints = typing.get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)} - set(skip)
    kwargs = {}
    for key, value in data.items():
        label = f"{where}.{key}" if where else key
        if key not in names:
            warnings.append(f"Unbekannter Konfigurationsschlüssel '{label}' wird ignoriert")
            continue
        kwargs[key] = _coerce(hints[key], value, label, warnings)
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ConfigError(f"{where or 'Konfiguration'}: {exc}") from exc


def _coerce(hint, value, label: str, warnings: list):
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is typing.Union:
        if value is None:
            return None
        hint = next(a for a in args if a is not type(None))
        return _coerce(hint, value, label, warnings)
    if dataclasses.is_dataclass(hint):
        return _build(hint, value or {}, label, warnings)
    if hint is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("ja", "nein", "true", "false", "an", "aus"):
            return value.strip().lower() in ("ja", "true", "an")
        raise ConfigError(f"{label}: true/false erwartet, gefunden: {value!r}")
    if hint in (int, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            try:
                value = float(str(value).replace(",", "."))
            except ValueError:
                raise ConfigError(f"{label}: Zahl erwartet, gefunden: {value!r}") from None
        return int(value) if hint is int else float(value)
    if hint is str:
        if value is None:
            raise ConfigError(f"{label}: Text erwartet")
        return str(value)
    if hint is list or origin is list:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ConfigError(f"{label}: Liste erwartet, gefunden: {value!r}")
        return list(value)
    if hint is dict or origin is dict:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ConfigError(f"{label}: Objekt erwartet, gefunden: {value!r}")
        return dict(value)
    return value


# --------------------------------------------------------------------- Prüfen
def validate(config: Config) -> None:
    errors = []
    try:
        config.tz
    except DateFormatError as exc:
        errors.append(str(exc))
    if config.window.end_time:
        try:
            config.end_time
        except DateFormatError as exc:
            errors.append(f"window.end_time: {exc}")
    if config.window.hours <= 0:
        errors.append("window.hours muss größer als 0 sein")

    keys = [c.key for c in config.categories]
    if not keys:
        errors.append("Es ist keine Kategorie konfiguriert (categories)")
    if len(set(keys)) != len(keys):
        errors.append("Kategorie-Schlüssel sind nicht eindeutig")
    for category in config.categories:
        if category.max_items < 1 or category.min_items < 0 or category.min_items > category.max_items:
            errors.append(f"categories[{category.key}]: 0 <= min_items <= max_items und max_items >= 1 erforderlich")
        if category.weight <= 0:
            errors.append(f"categories[{category.key}]: weight muss größer als 0 sein")

    ids = [s.id for s in config.sources]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        errors.append(f"Quellen-IDs sind nicht eindeutig: {', '.join(duplicates)}")
    for source in config.sources:
        where = f"sources[{source.id}]"
        if source.type not in SOURCE_TYPES:
            errors.append(f"{where}: unbekannter Typ '{source.type}' (erlaubt: {', '.join(SOURCE_TYPES)})")
        if not source.url and source.type != "hackernews":
            errors.append(f"{where}: 'url' fehlt")
        if not source.category:
            errors.append(f"{where}: 'category' fehlt")
        for key in source.category:
            if key not in keys:
                errors.append(f"{where}: unbekannte Kategorie '{key}' (vorhanden: {', '.join(keys)})")
        if not 0 <= source.quality <= 1:
            errors.append(f"{where}: quality muss zwischen 0 und 1 liegen")
        if source.fallback is not None and not isinstance(source.fallback, dict):
            errors.append(f"{where}: fallback muss ein Objekt sein")
        elif source.fallback and source.fallback.get("type", source.type) not in SOURCE_TYPES:
            errors.append(f"{where}.fallback: unbekannter Typ '{source.fallback.get('type')}'")
    if config.sources and not config.enabled_sources():
        errors.append("Alle Quellen sind deaktiviert")

    weights = config.scoring.weights
    unknown = set(weights) - {"recency", "relevance", "quality", "coverage"}
    if unknown:
        errors.append(f"scoring.weights: unbekannte Gewichte {', '.join(sorted(unknown))}")
    if any((not isinstance(v, (int, float))) or v < 0 for v in weights.values()) or not sum(weights.values()):
        errors.append("scoring.weights: nicht-negative Zahlen erwartet, Summe > 0")

    report = config.report
    if report.min_words > report.max_words:
        errors.append("report.min_words darf nicht größer als report.max_words sein")
    bad_formats = [f for f in report.formats if f not in REPORT_FORMATS]
    if bad_formats or not report.formats:
        errors.append(f"report.formats: erlaubt sind {', '.join(REPORT_FORMATS)}")
    for pattern_name in ("folder", "filename"):
        try:
            getattr(report, pattern_name).format(date=_dt.date(2026, 1, 1))
        except (KeyError, ValueError, IndexError) as exc:
            errors.append(f"report.{pattern_name}: ungültiges Muster ({exc}) – nur {{date:...}} ist erlaubt")

    backend = config.summarizer.backend = _normalize_backend(config.summarizer.backend)
    if backend not in SUMMARIZER_BACKENDS:
        errors.append(f"summarizer.backend: erlaubt sind {', '.join(SUMMARIZER_BACKENDS)}")
    if config.summarizer.claude.effort not in ("low", "medium", "high", "xhigh", "max"):
        errors.append("summarizer.claude.effort: erlaubt sind low, medium, high, xhigh, max")

    if config.logging.level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        errors.append("logging.level: erlaubt sind DEBUG, INFO, WARNING, ERROR")
    if errors:
        raise ConfigError("Konfiguration ungültig:\n  - " + "\n  - ".join(errors))


def _normalize_backend(value: str) -> str:
    value = (value or "auto").strip().lower()
    return {"extraktiv": "extractive", "anthropic": "claude"}.get(value, value)


def write_example(target: Path, force: bool = False) -> Path:
    target = Path(target)
    if target.exists() and not force:
        raise ConfigError(f"'{target}' existiert bereits (überschreiben mit --ueberschreiben)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    return target
