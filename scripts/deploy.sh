#!/bin/bash
# =============================================================
#  deploy.sh – Dateien auf den Pico W übertragen via mpremote
#  Voraussetzung: uv sync  (installiert mpremote automatisch)
# =============================================================

set -e

# Ins Projektverzeichnis wechseln (damit uv run das .venv findet)
cd "$(dirname "$0")/.."

PORT=${1:-auto}   # Ersten gefundenen Pico nehmen, oder z.B. /dev/ttyACM0
MPREMOTE="uv run mpremote"

echo "=== Deploying auf Pico W (Port: $PORT) ==="

# lib/-Verzeichnis anlegen falls nicht vorhanden
$MPREMOTE connect $PORT mkdir lib 2>/dev/null || true

# Bibliotheken übertragen
echo "→ lib/pn532.py"
$MPREMOTE connect $PORT cp lib/pn532.py :lib/pn532.py

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
echo "✓ Fertig! Starte REPL …"
echo "  (Strg+C zum Unterbrechen, Strg+X zum Beenden)"
echo ""

$MPREMOTE connect $PORT repl
