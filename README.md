# Pico W – RC522 NFC → MQTT

NFC-Tags per SPI lesen und als JSON via MQTT weiterleiten.

---

## Verdrahtung (SPI)

```
RC522 Modul          Pico W
─────────────────────────────
3V3  ──────────────► 3V3 (Pin 36)
GND  ──────────────► GND (Pin 38)
RST  ──────────────► GP22 (Pin 29)
SDA  ──────────────► GP5 (Pin 7)  - SPI CS
SCK  ──────────────► GP6 (Pin 9)  - SPI SCK
MOSI ──────────────► GP7 (Pin 10) - SPI MOSI
MISO ──────────────► GP4 (Pin 6)  - SPI MISO
IRQ  ──────────────► (optional, nicht benötigt)
```

```
Pico W Pin-Übersicht (relevant):
  Pin 6  = GP4  ← MISO
  Pin 7  = GP5  ← CS
  Pin 9  = GP6  ← SCK
  Pin 10 = GP7  ← MOSI
  Pin 29 = GP22 ← RST
  Pin 36 = 3V3
  Pin 38 = GND
```

---

## Projektstruktur

```
pico-nfc-mqtt/
├── main.py               # Hauptprogramm
├── config.py             # Lädt config.json
├── config.json           # Konfiguration (nicht in Git)
├── example-config.json   # Konfigurations-Beispiel
├── lib/
│   ├── rc522.py          # RC522 SPI Treiber
│   ├── mqtt_manager.py   # MQTT Client Wrapper
│   └── wifi.py           # WLAN-Verbindung
├── scripts/
│   └── deploy.sh         # Deployment auf Pico W
└── .zed/
    └── tasks.json        # Zed Build-Tasks
```

---

## Setup

### 1. MicroPython flashen

1. [micropython.org/download/RPI_PICO_W/](https://micropython.org/download/RPI_PICO_W/) – neueste `.uf2` herunterladen
2. BOOTSEL-Knopf halten → USB einstecken → loslassen
3. `RPI-RP2` Laufwerk erscheint → `.uf2` raufziehen
4. Pico startet neu

### 2. Entwicklungsumgebung einrichten (uv)

```bash
# uv installieren (falls noch nicht vorhanden)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Im Projektverzeichnis: virtualenv erstellen & alle Tools installieren
uv sync
```

Das installiert automatisch: `mpremote`, `ruff`, `pyright` und MicroPython-Stubs.

### 3. Konfiguration anpassen

```bash
# example-config.json nach config.json kopieren
cp example-config.json config.json

# config.json öffnen und anpassen:
# - wifi_ssid / wifi_password
# - mqtt_broker – IP-Adresse des Raspberry Pi
```

### 4. Auf Pico deployen (Zed Task oder manuell)

**Via Zed:** `Cmd+Shift+P` → *Run Task* → *Deploy → Pico W*

**Manuell:**
```bash
bash scripts/deploy.sh
```

Einzelne mpremote-Befehle immer via `uv run mpremote …` aufrufen, z.B.:
```bash
uv run mpremote ls :
uv run mpremote repl
```

---

## Raspberry Pi: Mosquitto Broker

```bash
# Installieren
sudo apt install mosquitto mosquitto-clients -y

# Konfiguration (Authentifizierung deaktivieren für lokales Netz)
echo "listener 1883" | sudo tee /etc/mosquitto/conf.d/local.conf
echo "allow_anonymous true" | sudo tee -a /etc/mosquitto/conf.d/local.conf

# Starten & aktivieren
sudo systemctl enable --now mosquitto

# Topics lauschen (Test)
mosquitto_sub -h localhost -t 'nfc/#' -v
```

---

## MQTT Payload (Beispiel)

### Heartbeat (alle 60 Sekunden)

Topic: `nfc/heartbeat`

```json
{
  "status": "online",
  "nfc_ok": true,
  "nfc_error": null,
  "uptime": 12345,
  "timestamp": "12345.67"
}
```

Der Heartbeat zeigt an, ob die NFC-Hardware verfügbar ist. Das GroundControl Dashboard kann diese Statusinformationen anzeigen.

### Tag-Scan

Topic: `nfc/tag`

```json
{
  "uid":       "A3F2B1C4",
  "uid_dec":   2751447492,
  "tag_type":  "MIFARE Classic 1K",
  "atqa":      "0x0400",
  "sak":       "0x08",
  "timestamp": "12345.67"
}
```

---

## LED-Statuscodes (Onboard LED)

| Muster | Bedeutung |
|--------|-----------|
| 2× lang | Start |
| 3× kurz | WLAN verbunden |
| 1× lang | Alles OK, warte auf Tags |
| 1× kurz | Tag erfolgreich gesendet |
| 4× mittel | NFC-Fehler, aber MQTT läuft weiter |
| 10× schnell | WLAN-Fehler |
| 5× mittel | MQTT-Fehler |

---

## Fehlerbehebung

kein USB: sudo usermod -aG dialout alex


**RC522 nicht gefunden:**
- Verdrahtung prüfen (SCK=GP6, MOSI=GP7, MISO=GP4, CS=GP5, RST=GP22)
- `mpremote repl` → `from machine import SPI, Pin; SPI(0, sck=Pin(6), mosi=Pin(7), miso=Pin(4))`
- **Hinweis:** Der Pico W läuft weiter und sendet Heartbeat mit `nfc_ok: false`

**MQTT Verbindung schlägt fehl:**
- Pi-IP korrekt in `config.json`?
- Mosquitto läuft? `sudo systemctl status mosquitto`
- Firewall? `sudo ufw allow 1883`
