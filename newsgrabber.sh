#!/bin/sh
# Startet den NewsGrabber aus diesem Ordner - egal, in welchem Ordner man gerade steht.
# Beispiele:  ./newsgrabber.sh init
#             ./newsgrabber.sh erstellen --datum 2026-10-14
# Ohne -c wird newsgrabber.yaml in diesem Ordner verwendet (relative Pfade gelten ab hier).
DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"
export NEWSGRABBER_KONFIG="${NEWSGRABBER_KONFIG:-$DIR/newsgrabber.yaml}"
if [ -x "$DIR/.venv/bin/python" ]; then PY="$DIR/.venv/bin/python"; else PY="python3"; fi
exec "$PY" -m newsgrabber "$@"
