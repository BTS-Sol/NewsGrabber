"""NewsGrabber – sammelt Nachrichten aus seriösen Quellen und erstellt ein Morgenbriefing.

Die Verarbeitung ist in klar getrennte Stufen aufgeteilt:

* :mod:`newsgrabber.collect`   – Datensammlung (RSS/Atom, APIs, HTML/Playwright, Volltext)
* :mod:`newsgrabber.process`   – Textverarbeitung (Bereinigung, Sätze, Schlagwörter, Duplikate)
* :mod:`newsgrabber.rank`      – Bewertung (Aktualität, Relevanz, Quellenqualität)
* :mod:`newsgrabber.summarize` – Zusammenfassung (extraktiv oder per Claude-API)
* :mod:`newsgrabber.report`    – Berichtserstellung (Auswahl, Wortbudget, Markdown/HTML/JSON)

:mod:`newsgrabber.pipeline` verbindet die Stufen, :mod:`newsgrabber.cli` ist die Kommandozeile.
"""

__version__ = "1.0.0"
