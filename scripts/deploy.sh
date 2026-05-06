#!/bin/bash
# =============================================================
#  deploy.sh – Dateien auf den Pico W übertragen via mpremote
#  Voraussetzung: uv sync  (installiert mpremote automatisch)
# =============================================================

set -e

# Ins Projektverzeichnis wechseln
cd "$(dirname "$0")/.."

# mpremote finden - verschiedene Methoden für Windows/Unix
if command -v uv &> /dev/null; then
    MPREMOTE="uv run mpremote"
elif [ -f ".venv/Scripts/mpremote.exe" ]; then
    # Windows venv
    MPREMOTE=".venv/Scripts/mpremote.exe"
elif [ -f ".venv/bin/mpremote" ]; then
    # Unix venv
    MPREMOTE=".venv/bin/mpremote"
else
    echo "Fehler: mpremote nicht gefunden!"
    echo "  Bitte führe zuerst 'uv sync' aus."
    exit 1
fi

PORT=${1:-auto}   # Ersten gefundenen Pico nehmen, oder z.B. /dev/ttyACM0

echo "=== Deploying auf Pico W (Port: $PORT) ==="
echo "Verwende: $MPREMOTE"
echo ""

# lib/-Verzeichnis anlegen falls nicht vorhanden
$MPREMOTE connect $PORT mkdir lib 2>/dev/null || true
$MPREMOTE connect $PORT mkdir lib/umqtt 2>/dev/null || true

# Bibliotheken übertragen
echo "→ lib/umqtt/simple.py"
$MPREMOTE connect $PORT cp lib/umqtt/simple.py :lib/umqtt/simple.py

echo "→ lib/pn522.py"
$MPREMOTE connect $PORT cp lib/pn522.py :lib/pn522.py

echo "→ lib/rc522.py"
$MPREMOTE connect $PORT cp lib/rc522.py :lib/rc522.py

echo "→ lib/mqtt_manager.py"
$MPREMOTE connect $PORT cp lib/mqtt_manager.py :lib/mqtt_manager.py

echo "→ lib/wifi.py"
$MPREMOTE connect $PORT cp lib/wifi.py :lib/wifi.py

# Konfiguration und Hauptprogramm
echo "→ config.json"
$MPREMOTE connect $PORT cp config.json :config.json

echo "→ config.py"
$MPREMOTE connect $PORT cp config.py :config.py

echo "→ main.py"
$MPREMOTE connect $PORT cp main.py :main.py

echo ""
echo "✓ Fertig! REPL wird gestartet..."
echo "  Drücke Ctrl+D für Soft-Reset (main.py startet)"
echo "  (Strg+X zum Beenden)"
echo ""

# REPL starten
$MPREMOTE connect $PORT repl
