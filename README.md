# NewsGrabber – Morgenbriefing aus seriösen Quellen

Sammelt täglich aktuelle Nachrichten aus RSS-Feeds und APIs (bei Bedarf auch von dynamischen
Websites per Playwright), bereinigt und bündelt sie, bewertet sie nach **Aktualität, Relevanz
und Quellenqualität** und erstellt daraus ein kompaktes **Morgenbriefing** mit

* den Kategorien **Weltgeschehen**, **Deutschland** und **IT & Technologie** (mit den Themen
  Cybersecurity, KI, Softwareentwicklung und Open Source),
* je Meldung **Überschrift, Schlagwörtern, verständlicher Zusammenfassung und den wichtigsten
  Fakten** – die Informationen stehen im Bericht, die Links dienen nur als Quellenangabe,
* einem Zielumfang von **2.000–3.000 Wörtern** (wird automatisch gekürzt),
* Ausgabe als **Markdown, HTML und JSON** in einem Datumsordner.

Ein Lauf erstellt das Briefing für **genau einen Tag** und wird über die Kommandozeile gestartet –
manuell, aus einem übergeordneten System, per Windows-Aufgabenplanung oder Cron.

---

## Inhalt

1. [Installation](#installation)
2. [Schnellstart](#schnellstart)
3. [Kommandozeile](#kommandozeile)
4. [Automatisch starten (Aufgabenplanung, Cron)](#automatisch-starten)
5. [Einbindung in ein größeres System](#einbindung-in-ein-größeres-system)
6. [Konfiguration](#konfiguration)
7. [Quellen](#quellen)
8. [Ablauf und Architektur](#ablauf-und-architektur)
9. [Bewertung und Auswahl](#bewertung-und-auswahl)
10. [Zusammenfassungen](#zusammenfassungen)
11. [Ausgabe](#ausgabe)
12. [Datenbank (SQLite)](#datenbank-sqlite)
13. [Zeitfenster, Zeitzone, Wiederholungen, Ausfälle](#zeitfenster-zeitzone-wiederholungen-ausfälle)
14. [Rechtliches: Urheberrecht, robots.txt, Nutzungsbedingungen](#rechtliches)
15. [Erweitern](#erweitern)
16. [Tests](#tests)
17. [Fehlersuche](#fehlersuche)

---

## Installation

Voraussetzung: **Python 3.10 oder neuer** ([python.org](https://www.python.org/downloads/); unter Windows bei
der Installation „Add python.exe to PATH“ anhaken).

### Windows

```bat
cd C:\NewsGrabber                      :: Ordner mit diesem Projekt (beschreibbar, nicht unter „Programme“)
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt

:: optional – Zusammenfassungen per Claude-API und/oder dynamische Websites
.venv\Scripts\python -m pip install anthropic playwright
.venv\Scripts\python -m playwright install chromium
```

`newsgrabber.bat` verwendet automatisch die Python-Umgebung `.venv` im Projektordner.

### Linux / macOS

```bash
cd /opt/newsgrabber
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install anthropic playwright        # optional
.venv/bin/python -m playwright install chromium   # optional, nur mit Playwright
```

Alternativ als Paket: `pip install .` (bzw. `pip install ".[alle]"` mit allen Optionen) stellt den
Befehl `newsgrabber` bereit.

| Paket | Zweck | Pflicht |
|---|---|---|
| requests, feedparser | HTTP, RSS/Atom | ja |
| trafilatura, beautifulsoup4, lxml | Haupttext aus Artikelseiten, HTML-Bereinigung | ja |
| PyYAML, Jinja2 | Konfiguration, Berichtsvorlagen | ja |
| snowballstemmer | Wortstämme (Duplikate, Schlagwörter) | ja |
| tzdata | Zeitzone Europe/Berlin unter Windows | Windows |
| anthropic | Zusammenfassungen in eigenen Worten (Claude) | nein |
| playwright | dynamische Websites rendern | nein |

## Schnellstart

```bat
newsgrabber.bat init                 :: legt newsgrabber.yaml (kommentiert) im Projektordner an
newsgrabber.bat pruefen              :: Konfiguration und Umgebung prüfen
newsgrabber.bat quellen --testen     :: jede Quelle einmal abrufen
newsgrabber.bat erstellen            :: Briefing für heute erstellen
```

Unter Linux/macOS entsprechend `./newsgrabber.sh …` oder `python -m newsgrabber …`.

Das Ergebnis liegt danach z. B. in `ausgabe\2026-10-14\morgenbriefing_2026-10-14.html` (plus `.md`
und `.json`). Ohne eigene Konfigurationsdatei läuft `python -m newsgrabber erstellen` mit der
mitgelieferten Beispielkonfiguration (Ausgabe relativ zum aktuellen Ordner).

## Kommandozeile

```text
python -m newsgrabber erstellen [-c KONFIG] [-d DATUM] [--stunden N] [-o ORDNER] [-f md|html|json ...]
                                [--quelle ID ...] [--zusammenfassung auto|extraktiv|claude]
                                [--max-woerter N] [--min-woerter N] [--ohne-volltext] [--ohne-db]
                                [--vorschau] [--streng] [-v] [-q]
python -m newsgrabber quellen   [-c KONFIG] [--testen] [--quelle ID ...] [--alle]
python -m newsgrabber pruefen   [-c KONFIG]
python -m newsgrabber init      [-c ZIELDATEI] [--ueberschreiben]
python -m newsgrabber db        [-c KONFIG] [--aufraeumen] [--tage N]
```

| Option | Bedeutung |
|---|---|
| `-c`, `--konfig` | Konfigurationsdatei. Standard: Umgebungsvariable `NEWSGRABBER_KONFIG`, sonst `./newsgrabber.yaml`, sonst die Beispielkonfiguration |
| `-d`, `--datum` | Berichtstag: `JJJJ-MM-TT`, `TT.MM.JJJJ`, `heute`, `morgen`, `gestern` (Standard: heute) |
| `--stunden` | Zeitfenster in Stunden (z. B. `72` montags, um das Wochenende abzudecken) |
| `-o`, `--ausgabe` | Basisordner der Ausgabe (darin der Datumsordner) |
| `-f`, `--format` | nur diese Formate erzeugen (mehrfach möglich) |
| `--quelle` | nur diese Quelle(n) verwenden – praktisch zum Testen |
| `--zusammenfassung` | Verfahren erzwingen (`extraktiv` oder `claude`) |
| `--max-woerter`, `--min-woerter` | Wortbudget überschreiben |
| `--ohne-volltext` | keine Artikelseiten abrufen (nur Feeds/APIs) – schneller, weniger Anfragen |
| `--ohne-db` | SQLite nicht verwenden |
| `--vorschau` | Bericht auf stdout ausgeben, keine Dateien schreiben, Datenbank unverändert |
| `--streng` | Exit-Code 2, wenn Quellen ausgefallen sind oder Hinweise vorliegen |
| `-v` / `-q` | ausführliche bzw. nur Warnungen/Fehler auf der Konsole |

Englische Aliase sind vorhanden (`generate`, `sources`, `check`, `--config`, `--date`, `--preview`, …).

**Exit-Codes:** `0` Bericht erstellt · `1` Fehler (Konfiguration ungültig, keine Quelle erreichbar, …) ·
`2` Bericht erstellt, aber mit Warnungen (nur mit `--streng`; bei `quellen --testen`, wenn eine Quelle
nicht erreichbar ist).

## Automatisch starten

### Windows-Aufgabenplanung

Einmalig in einer Eingabeaufforderung (Pfad anpassen) – täglich um 05:30 Uhr:

```bat
schtasks /Create /TN "NewsGrabber Morgenbriefing" /SC DAILY /ST 05:30 ^
  /TR "\"C:\NewsGrabber\newsgrabber.bat\" erstellen -q"
```

Oder über die grafische Aufgabenplanung: *Einfache Aufgabe erstellen* → täglich → *Programm starten* →
Programm `C:\NewsGrabber\newsgrabber.bat`, Argumente `erstellen -q`. Empfehlenswert unter
*Eigenschaften*: „Unabhängig von der Benutzeranmeldung ausführen“ und „Aufgabe so schnell wie möglich
nach einem verpassten Start ausführen“.

Hinweise:

* Die Aufgabenplanung startet Programme in `C:\Windows\System32`. `newsgrabber.bat` verwendet deshalb
  immer die `newsgrabber.yaml` **im Projektordner**; relative Pfade (Ausgabe, Datenbank, Log) gelten ab
  dort. Mit `-c` kann eine andere Datei angegeben werden.
* Für die Claude-API die Umgebungsvariable `ANTHROPIC_API_KEY` systemweit setzen
  (Systemsteuerung → System → Umgebungsvariablen) oder in einer eigenen .bat vor dem Aufruf mit `set`.
* Ergebnis und Fehler stehen in `logs\newsgrabber.log`.

### Cron (Linux/macOS)

```cron
# Dienstag bis Sonntag um 05:30 Uhr (Zeitzone des Servers!)
30 5 * * 0,2-6  /opt/newsgrabber/newsgrabber.sh erstellen -q >> /opt/newsgrabber/logs/cron.log 2>&1
# montags mit 72 Stunden, um das Wochenende abzudecken
30 5 * * 1      /opt/newsgrabber/newsgrabber.sh erstellen -q --stunden 72 >> /opt/newsgrabber/logs/cron.log 2>&1
```

Für einen einfachen täglichen Lauf genügt `30 5 * * *`. Läuft der Server nicht in deutscher Zeit, die
Uhrzeit anpassen oder `CRON_TZ=Europe/Berlin` voranstellen. Die Berichtszeiten selbst rechnet der
NewsGrabber immer in der konfigurierten Zeitzone (`timezone`, Standard Europe/Berlin).

## Einbindung in ein größeres System

* **Ein Aufruf je Tag:** `python -m newsgrabber erstellen --datum 2026-10-14` – der Datumsordner ist
  vorhersagbar (`report.folder`, `report.filename`).
* **stdout** enthält ausschließlich die Pfade der erzeugten Dateien (eine pro Zeile); alle Meldungen
  gehen nach stderr bzw. in die Logdatei. Mit `--vorschau -f md` kommt stattdessen der Bericht selbst
  auf stdout.
* **Exit-Codes** siehe oben; mit `--streng` lässt sich auf ausgefallene Quellen reagieren.
* **JSON** (`schema_version: 1`) enthält alle Inhalte strukturiert: Kategorien, Meldungen mit
  Überschrift, Zusammenfassung, Fakten, Schlagwörtern, Thema, Quellen (Name, URL, Zeit), Bewertung,
  Quellenstatus und Statistik – siehe [Ausgabe](#ausgabe).
* **Python-API:**

  ```python
  import datetime
  from newsgrabber.config import load_config
  from newsgrabber.pipeline import RunOptions, run

  result = run(load_config("newsgrabber.yaml"), RunOptions(date=datetime.date(2026, 10, 14)))
  print(result.files, result.briefing.word_count)
  ```

## Konfiguration

Alle Einstellungen stehen kommentiert in der Beispielkonfiguration
[`newsgrabber/resources/beispiel_konfiguration.yaml`](newsgrabber/resources/beispiel_konfiguration.yaml);
`init` legt eine Kopie an. Eine eigene Datei muss nur abweichende Werte enthalten – Ausnahme:
`sources` und `categories` ersetzen, wenn angegeben, die Listen vollständig. Unbekannte Schlüssel
werden als Warnung gemeldet (Tippfehler), ungültige Werte führen zu einer klaren Fehlermeldung.

| Abschnitt | Inhalt |
|---|---|
| `timezone` | Zeitzone für Zeitfenster und Anzeige (Standard `Europe/Berlin`) |
| `window` | `hours` (Zeitfenster), `end_time` (optionale Stichzeit, z. B. `"06:00"`) |
| `collection` | User-Agent, Timeouts, Wiederholungen, parallele Abrufe, Mindestabstand je Host, robots.txt, Volltext, Playwright |
| `processing` | Schwelle für Duplikate, Mindestlänge von Titeln, zusätzliche Boilerplate-Muster |
| `scoring` | Gewichte, Halbwertszeit, Abschläge (Werbung, Wiederholung, ohne Datum), Negativ-Schlagwörter |
| `summarizer` | `auto`/`extractive`/`claude`, Sprache, Claude-Modell und -Einstellungen |
| `report` | Titel, Ausgabeordner, Datei-/Ordnermuster, Formate, Wortbudget, Vorlagen |
| `database` | SQLite an/aus, Pfad, Aufbewahrungsdauer |
| `logging` | Level, Logdatei, Rotation |
| `categories` | Kategorien in Berichtsreihenfolge mit `max_items`, `min_items`, `keywords`, `topics` |
| `sources` | Quellenliste (siehe unten) |

**Schlagwort-Regeln** (für `keywords`, `topics`, `negative_keywords`): Begriffe mit höchstens drei
Zeichen oder in GROSSBUCHSTABEN (`KI`, `EU`, `CVE`) werden exakt als ganzes Wort gesucht; alle anderen
ohne Rücksicht auf Groß-/Kleinschreibung als Wortanfang – `Sicherheitslücke` findet also auch
„Sicherheitslücken“. Ein `$` am Ende erzwingt die exakte Suche (`Rust$`, `Intel$` – damit nicht
„Intelligenz“ getroffen wird).

## Quellen

Jede Quelle hat `id`, `name`, `type`, `url`, `category` (eine oder mehrere), `quality` (0–1),
`language`, optional `enabled`, `fulltext`, `max_items`, `options` und `fallback`.

| `type` | Beschreibung | wichtige `options` |
|---|---|---|
| `rss` | RSS 2.0, Atom, RDF – bevorzugter Weg | – |
| `tagesschau_api` | tagesschau-API (`/api2u/news/?ressort=…`), liefert den Text gleich mit | – |
| `hackernews` | Hacker News über die Algolia-API (eine Anfrage) oder die Firebase-API | `min_points` |
| `json_api` | beliebige JSON-API mit Feldzuordnung, API-Schlüssel aus Umgebungsvariable | `items_path`, `fields`, `params`, `api_key_env`, `api_key_param`/`api_key_header` |
| `html` | Übersichtsseite ohne Feed: Links per CSS-Selektor; mit `render: auto` wird Playwright genutzt, wenn statisch nichts gefunden wird | `link_selector`, `include_pattern`, `exclude_pattern`, `render` |

* **Mehrere Kategorien** (`category: [weltgeschehen, deutschland]`): die Zuordnung je Meldung erfolgt
  über die Schlagwörter der Kategorien – so lassen sich gemischte Nachrichtenfeeds nutzen.
* **`fulltext: false`** für Quellen mit Bezahlschranke oder wenn der Abruf von Artikelseiten laut
  Nutzungsbedingungen nicht erlaubt ist; dann wird nur der Anrisstext des Feeds verwendet.
* **`fallback`** ersetzt einzelne Felder, wenn die Quelle ausfällt oder nichts liefert, z. B. die
  tagesschau-API durch den RSS-Feed: `fallback: {type: rss, url: "https://www.tagesschau.de/inland/index~rss2.xml"}`.

Die Beispielkonfiguration enthält ca. 27 aktive Quellen (u. a. tagesschau, Deutschlandfunk, ZDFheute,
DW, SPIEGEL, ZEIT, FAZ, SZ, NZZ, BBC, Guardian, heise, Golem, netzpolitik.org, BSI, t3n, The Decoder,
LWN, Ars Technica, The Register, BleepingComputer, Krebs on Security, Hacker News) sowie deaktivierte
Beispiele für `html` (Playwright) und `json_api`. **Nach der Installation einmal
`quellen --testen` ausführen** – Feed-Adressen ändern sich gelegentlich.

## Ablauf und Architektur

```text
          ┌────────────────────────────────── pipeline.run() ───────────────────────────────────┐
Quellen → │ 1 collect     2 process        3 rank          (Volltext)   4 summarize   5 report  │ → .md/.html/.json
          │ RSS/API/HTML  Bereinigen,      Kategorie,      Artikel der  extraktiv     Auswahl,  │
          │ parallel,     Duplikate        Aktualität,     besten       oder Claude   Wortbudget│
          │ robots.txt,   bündeln,         Relevanz,       Kandidaten,                Vorlagen  │
          │ Rate Limit    Zeitfenster      Qualität        neu bündeln                          │
          └──────────────────────────────── storage (SQLite, optional) ────────────────────────┘
```

```text
newsgrabber/
├── cli.py               Kommandozeile
├── pipeline.py          Ablauf eines Laufs (auch als Python-API)
├── config.py            Laden, Zusammenführen, Prüfen der Konfiguration
├── models.py            Datenmodelle (Article, Story, Briefing, …)
├── timeutil.py          Zeitzone, Datumsangaben, Zeitfenster
├── storage.py           SQLite: Artikel, Berichtsverlauf, Quellenstatus, Cache
├── logsetup.py          Logging (Konsole + rotierende Datei)
├── collect/             Datensammlung
│   ├── http.py          HTTP-Client: robots.txt, Rate Limit je Host, Wiederholungen, Größenlimit
│   ├── rss.py · api.py · html.py   Quellentypen
│   ├── browser.py       Playwright (optional, eigener Thread)
│   └── article.py       Volltext + Metadaten (trafilatura, Meta-Tags, JSON-LD)
├── process/             Textverarbeitung
│   ├── clean.py         HTML → Text, Titel/Teaser/Volltext bereinigen, Boilerplate
│   ├── text.py          Satztrennung, Sprache, Stoppwörter, Wortstämme
│   ├── keywords.py      Schlagwort-Erkennung und -Extraktion
│   └── dedup.py         URL-Normalisierung, identische Artikel, Clusterbildung
├── rank/scoring.py      Kategorie/Thema zuordnen, Bewertung
├── summarize/           extractive.py, claude.py (+ Schnittstelle in base.py)
├── report/              builder.py (Auswahl, Wortbudget), render.py, templates/*.j2
└── resources/beispiel_konfiguration.yaml
```

Die Stufen kommunizieren nur über die Datenmodelle in `models.py`; jede Stufe ist einzeln testbar.

## Bewertung und Auswahl

**Duplikate:** Gleiche URLs desselben Anbieters werden zusammengeführt. Gleiche Nachrichten aus
verschiedenen Quellen werden über TF-IDF-Vektoren aus Titel, Anrisstext und Textanfang gebündelt
(Wortstämme, Zeichen-N-Gramme für deutsche Komposita, Zahlen) plus einem Bonus für seltene
gemeinsame Eigennamen/Zahlen – so werden auch deutsch- und englischsprachige Berichte über dasselbe
Ereignis erkannt. Schwelle: `processing.dedup_threshold`.

**Bewertung** je Nachricht (jeweils 0–1, gewichtet nach `scoring.weights`):

| Teilwert | Berechnung |
|---|---|
| Aktualität | `0,5 ^ (Alter in Stunden / recency_half_life_hours)` – maßgeblich ist die neueste Meldung |
| Relevanz | Treffer der Kategorie-/Themen-Schlagwörter (Titel doppelt), Position im Feed, vorhandene Textmenge, Eilmeldung |
| Quellenqualität | höchste `quality` der beteiligten Quellen |
| Verbreitung | Anzahl unabhängiger Quellen (gesättigt bei `coverage_saturation`) |

Abschläge (Faktoren): Negativ-Schlagwörter (Werbung, Kaufberatung, Sport …), bereits berichtet
(`repeat_penalty`), Fortsetzung eines berichteten Themas (`update_penalty`), kein Datum.

**Auswahl:** je Kategorie höchstens `max_items` Meldungen, absteigend nach Bewertung; Themenvielfalt
über `topic_diversity`; Meldungen unter `min_score` nur bis `min_items`; Meldungen ganz ohne Text
werden nie aufgenommen. Mit `report.show_scores: true` stehen die Teilwerte im Bericht.

**Wortbudget:** Die Länge der Zusammenfassungen wird aus dem Ziel (Mitte von `min_words`/`max_words`)
und der Zahl der Meldungen abgeleitet. Ist der Bericht zu lang, werden zuerst die am niedrigsten
bewerteten Meldungen entfernt (solange `min_items` und `min_words` gewahrt bleiben), dann Fakten und
letzte Sätze gekürzt.

## Zusammenfassungen

| Verfahren | Funktionsweise | Voraussetzung |
|---|---|---|
| **extraktiv** (Standard) | wählt die aussagekräftigsten Sätze aller Quellen einer Nachricht (Zentralität, Bezug zur Überschrift, Position, Quellenqualität), vermeidet Wiederholungen, verdichtet Sätze mit Zahlen/Namen zu Fakten; höchstens `max_words_per_source` Wörter je Quelle | keine – läuft offline |
| **claude** | Zusammenfassung **in eigenen Worten** auf Deutsch (auch aus englischen Quellen) mit Überschrift, Fakten und Schlagwörtern als strukturiertes JSON; bei Fehlern je Meldung automatisch extraktiv; Ergebnisse werden in SQLite zwischengespeichert | `pip install anthropic`, Umgebungsvariable `ANTHROPIC_API_KEY` |

`summarizer.backend: auto` verwendet Claude, sobald Paket und Schlüssel vorhanden sind. Standardmodell
ist `claude-opus-5-5` mit `effort: medium`; serverseitige Ausweichmodelle bei Ablehnungen sind aktiv
(`fallbacks: true`). Pro Briefing entstehen ca. 20–25 Anfragen mit je einigen Tausend Eingabe-Token
(Auszüge, nicht ganze Artikel; begrenzt durch `max_input_words`). Kosten senken: `effort: low` oder ein
günstigeres Modell (z. B. `claude-haiku-5-5`; dann `fallbacks: false`).

## Ausgabe

```text
ausgabe/
└── 2026-10-14/
    ├── morgenbriefing_2026-10-14.md
    ├── morgenbriefing_2026-10-14.html     (eigenständig, ohne externe Dateien; hell/dunkel, druckbar)
    └── morgenbriefing_2026-10-14.json
```

Aufbau des Berichts: Kopf mit Zeitfenster → „Auf einen Blick“ (Top-Meldungen) → je Kategorie die
Meldungen mit Thema, Zeit, Schlagwörtern, Zusammenfassung, Fakten und Quellen → Quellenstatus,
Hinweise und Angabe des Verfahrens.

Dateien werden atomar geschrieben (erst temporär, dann ersetzt) – ein abgebrochener Lauf hinterlässt
keine halbfertigen Berichte. Ein erneuter Lauf für denselben Tag überschreibt die Dateien.

JSON (gekürzt):

```json
{
  "schema_version": 1,
  "date": "2026-10-14",
  "timezone": "Europe/Berlin",
  "window": {"start": "2026-10-13T06:00:00+02:00", "end": "2026-10-14T06:00:00+02:00"},
  "word_count": 2480,
  "categories": [{
    "key": "it", "title": "IT & Technologie",
    "items": [{
      "id": "it-1", "rank": 1, "headline": "…", "summary": "…", "facts": ["…"],
      "keywords": ["…"], "topic": "Cybersecurity", "published": "2026-10-14T05:40:00+02:00",
      "history": "", "summary_method": "extraktiv", "score": 0.81,
      "sources": [{"name": "heise online", "url": "https://…", "published": "…"}]
    }]
  }],
  "sources": [{"id": "heise", "ok": true, "items": 40, "error": "", "consecutive_failures": 0}],
  "stats": {"sources_ok": 26, "sources_failed": 1, "stories_selected": 23, "http_requests": 118}
}
```

Eigene Layouts: `report.templates_dir` auf einen Ordner mit `briefing.md.j2` und/oder
`briefing.html.j2` setzen (Vorlagen in `newsgrabber/report/templates/` als Ausgangspunkt).

## Datenbank (SQLite)

Optional (`database.enabled`), Standard `newsgrabber.sqlite3` neben der Konfiguration. Gespeichert
werden **nur Metadaten und eigene Ergebnisse, keine Volltexte**:

| Tabelle | Inhalt | Zweck |
|---|---|---|
| `articles` | URL, Quelle, Titel, Veröffentlichung, erster/letzter Fund, Prüfsumme des Textes | Meldungen ohne Datum nicht fälschlich als neu werten |
| `reports`, `report_stories` | erstellte Berichte, Überschriften und URLs je Meldung | Wiederholungen/Fortsetzungen erkennen |
| `source_status` | letzter Erfolg, Fehler in Folge, letzte Fehlermeldung | dauerhaft ausgefallene Quellen melden |
| `summary_cache` | Claude-Zusammenfassungen | erneuter Lauf am selben Tag ohne API-Kosten |

Einträge älter als `retention_days` werden bei jedem Lauf gelöscht; manuell: `newsgrabber db --aufraeumen`.

## Zeitfenster, Zeitzone, Wiederholungen, Ausfälle

* **Zeitzone:** Alle Zeitpunkte werden intern in UTC geführt und in `timezone` (Europe/Berlin, inkl.
  Sommer-/Winterzeit) angezeigt. Feeds ohne Zeitzone werden als UTC gelesen; offensichtlich falsche
  Zukunftsdaten werden auf den Abrufzeitpunkt gesetzt.
* **Zeitfenster:** Ende ist der Start des Laufs (bzw. `end_time` am Berichtstag, bei vergangenen Tagen
  höchstens Mitternacht), Beginn `hours` Stunden davor. Meldungen ohne Datum erhalten den Zeitpunkt, an
  dem sie zum ersten Mal gefunden wurden (Datenbank), bzw. bei Artikelseiten das Datum aus den
  Metadaten. Hinweis: Feeds enthalten nur die letzten Meldungen – Berichte für weit zurückliegende Tage
  sind daher nur eingeschränkt möglich.
* **Wiederholungsversuche:** Netzwerkfehler, Timeouts, HTTP 408/425/429/500/502/503/504 werden `retries`-mal mit
  exponentiellem Backoff wiederholt; `Retry-After` wird beachtet. 4xx-Fehler werden nicht wiederholt.
* **Wiederholte Nachrichten:** Meldungen, deren Artikel bereits in einem Bericht der letzten
  `history_days` Tage standen, werden abgewertet und als „bereits berichtet“ markiert; neue Entwicklungen
  zu einem berichteten Thema erscheinen als „Fortsetzung“.
* **Ausgefallene Quellen:** Jede Quelle läuft isoliert; ein Fehler bricht den Lauf nicht ab. Optional
  springt eine Ersatzquelle (`fallback`) ein. Ausfälle stehen im Log, im JSON und am Ende des Berichts;
  nach drei Fehlschlägen in Folge erscheint ein Hinweis, die URL zu prüfen. Nur wenn **keine** Quelle
  etwas liefert, endet der Lauf mit Exit-Code 1.

## Rechtliches

> Keine Rechtsberatung – die Verantwortung für den Einsatz liegt beim Betreiber.

* **Urheberrecht:** Der NewsGrabber speichert **keine vollständigen Artikel**. Volltexte werden nur
  während des Laufs im Arbeitsspeicher verarbeitet; in Bericht und Datenbank landen eigene
  Zusammenfassungen, kurze Fakten, Überschriften und Links. Das extraktive Verfahren übernimmt kurze
  Auszüge mit Quellenangabe und begrenzt sie je Quelle (`max_words_per_source`); für Zusammenfassungen
  in eigenen Worten ist das Claude-Verfahren vorgesehen. Bei Weitergabe oder Veröffentlichung der
  Berichte (z. B. intern im Unternehmen, im Radio) prüfen, ob dafür eine Lizenz nötig ist
  (Presseverleger-Leistungsrecht §§ 87f ff. UrhG, Zitatrecht § 51 UrhG).
* **robots.txt** wird für jeden Abruf beachtet (auch für Playwright), inklusive `Crawl-delay`. Ist
  robots.txt nicht erreichbar (Serverfehler), wird der Host in diesem Lauf nicht abgerufen (RFC 9309).
* **Rate Limits:** Mindestabstand je Host (`per_host_interval`, Standard 1,5 s), Begrenzung der
  Artikelabrufe je Lauf (`fulltext.max_articles`), Größenlimit je Antwort, ehrlicher User-Agent –
  bitte eine Kontaktadresse eintragen (`collection.user_agent`).
* **Nutzungsbedingungen:** Viele Anbieter erlauben RSS-Feeds nur für private bzw. nicht-kommerzielle
  Nutzung, die tagesschau-API ebenso. Vor dem Einsatz die Bedingungen jeder Quelle prüfen und Quellen
  ggf. deaktivieren (`enabled: false`) oder den Abruf von Artikelseiten abschalten (`fulltext: false`).
  Bezahlinhalte werden nicht umgangen.

## Erweitern

* **Neuer Quellentyp:** Funktion `collect(source, ctx) -> list[Article]` schreiben (Muster: `collect/rss.py`,
  Artikel mit `make_article()` erzeugen), in `collect/__init__.py` unter `COLLECTORS` und in
  `config.SOURCE_TYPES` eintragen.
* **Neues Zusammenfassungsverfahren:** Klasse mit `name` und `summarize_all(jobs) -> list[StorySummary]`
  (siehe `summarize/base.py`), Auswahl in `summarize/__init__.py`.
* **Neue Kategorie:** Eintrag unter `categories` (mit `keywords`, optional `topics`) und Quellen mit dieser
  Kategorie – keine Codeänderung nötig.
* **Weiteres Ausgabeformat:** Methode in `report/render.py` und Eintrag in `EXTENSIONS`/`REPORT_FORMATS`.

## Tests

```bash
pip install pytest
python -m pytest
```

Die Tests laufen vollständig offline gegen einen lokalen HTTP-Server mit fiktiven Testdaten
(`tests/fixtures/`) – vom Feed-Abruf über robots.txt, Wiederholungen, Duplikaterkennung, Bewertung und
Wortbudget bis zu den fertigen Dateien. Die Claude-Anbindung wird mit einem Fake-Client geprüft, der
Playwright-Test läuft nur, wenn ein Browser verfügbar ist.

## Fehlersuche

| Problem | Lösung |
|---|---|
| `Unbekannte Zeitzone 'Europe/Berlin'` (Windows) | `pip install tzdata` |
| Quelle meldet `HTTP 404`/`Feed nicht lesbar` | URL im Browser prüfen, in der Konfiguration korrigieren oder `enabled: false`; `quellen --testen --quelle ID` |
| `robots.txt untersagt den Abruf` | Der Anbieter wünscht keinen automatisierten Abruf – Quelle deaktivieren |
| Firmen-Proxy | Umgebungsvariablen `HTTPS_PROXY`/`HTTP_PROXY` setzen (werden von requests übernommen) |
| Playwright: Browser fehlt | `python -m playwright install chromium` oder `collection.playwright.channel: msedge` (nutzt den installierten Edge) |
| Briefing zu kurz | mehr Quellen aktivieren, `--stunden` erhöhen, `max_items` der Kategorien erhöhen |
| Falsche Meldungen gebündelt / Dubletten | `processing.dedup_threshold` erhöhen bzw. senken (Standard 0,25) |
| Umlaute in der Windows-Konsole falsch | `newsgrabber.bat` setzt UTF-8; Dateien sind immer UTF-8 |
| Mehr Details | `-v` (DEBUG) oder `logging.level: DEBUG`, Log unter `logs/newsgrabber.log` |
