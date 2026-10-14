"""Logging: Konsole (stderr) und rotierende Logdatei."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"


def setup_logging(level: str = "INFO", logfile: Optional[Path] = None, max_bytes: int = 2_000_000,
                  backup_count: int = 7, console_level: Optional[str] = None) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel((console_level or level).upper())
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT, "%H:%M:%S"))
    root.addHandler(console)

    if logfile:
        try:
            logfile.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(logfile, maxBytes=max_bytes, backupCount=backup_count,
                                                           encoding="utf-8")
            handler.setLevel(level.upper())
            handler.setFormatter(logging.Formatter(FORMAT))
            root.addHandler(handler)
        except OSError as exc:
            logging.getLogger(__name__).warning("Logdatei %s nicht nutzbar: %s", logfile, exc)

    # Bibliotheken nicht zu gesprächig
    for name in ("urllib3", "charset_normalizer", "trafilatura", "htmldate", "courlan", "httpx", "httpx2",
                 "anthropic", "filelock", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_console_encoding() -> None:
    """Windows-Konsolen (cp1252/cp850) sollen bei Sonderzeichen nicht abbrechen."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
