"""
main.py – Einstiegspunkt für den Pico W
NFC-Tags per I2C lesen und via MQTT weiterleiten.
"""

import time

import wifi
from machine import I2C, Pin

import config
from lib.mqtt_manager import MQTTError, MQTTManager
from lib.pn532 import PN532, PN532Error

# Onboard-LED als Statusanzeige
led = Pin("LED", Pin.OUT)

# NFC-Hardware-Status
NFC_STATUS_OK = False
NFC_ERROR_MSG = None


def blink(times: int = 1, on_ms: int = 100, off_ms: int = 100):
    for _ in range(times):
        led.on()
        time.sleep_ms(on_ms)
        led.off()
        time.sleep_ms(off_ms)


def get_timestamp() -> str:
    """Einfacher Timestamp (Sekunden seit Boot – NTP wäre Erweiterung)."""
    return str(time.time())


def setup_nfc() -> PN532 | None:
    """Initialisiert NFC-Hardware. Gibt None bei Fehler zurück (setzt globale Status-Variablen)."""
    global NFC_STATUS_OK, NFC_ERROR_MSG

    print("[NFC] Initialisiere I2C …")
    i2c = I2C(
        config.I2C_BUS,
        sda=Pin(config.I2C_SDA),
        scl=Pin(config.I2C_SCL),
        freq=config.I2C_FREQ,
    )

    # Kurze Pause damit PN532 hochfährt
    time.sleep_ms(100)

    # I2C-Scan zur Fehlerdiagnose
    devices = i2c.scan()
    print(f"[NFC] Gefundene I2C-Geräte: {[hex(d) for d in devices]}")
    if 0x24 not in devices:
        error = "PN532 nicht gefunden! Verdrahtung prüfen (SDA=GP4, SCL=GP5)"
        print(f"[NFC] FEHLER: {error}")
        NFC_STATUS_OK = False
        NFC_ERROR_MSG = error
        return None

    try:
        nfc = PN532(i2c)
        fw = nfc.get_firmware_version()
        print(f"[NFC] PN532 Firmware: v{fw['ver']}.{fw['rev']}")
        nfc.sam_configuration()
        print("[NFC] Bereit.")
        NFC_STATUS_OK = True
        NFC_ERROR_MSG = None
        return nfc
    except Exception as e:
        error = f"PN532 Initialisierung fehlgeschlagen: {e}"
        print(f"[NFC] FEHLER: {error}")
        NFC_STATUS_OK = False
        NFC_ERROR_MSG = error
        return None


def main():
    print("\n=== Pico W NFC → MQTT ===\n")
    blink(2, 200)  # Startblinken

    # 1. WLAN
    try:
        wifi.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        blink(3, 100)  # 3× kurz = WLAN OK
    except Exception as e:
        print(f"[FEHLER] WLAN: {e}")
        blink(10, 50)  # Schnelles Blinken = WLAN-Fehler
        return

    # 2. MQTT
    mqtt = MQTTManager(
        broker_ip=config.MQTT_BROKER,
        client_id=config.MQTT_CLIENT_ID,
        port=config.MQTT_PORT,
        topic_prefix=config.MQTT_TOPIC_PREFIX,
        keepalive=config.MQTT_KEEPALIVE_S,
    )
    if not mqtt.connect():
        print("[FEHLER] MQTT-Verbindung fehlgeschlagen")
        blink(5, 200)
        return

    # 3. NFC (optional - läuft weiter auch ohne NFC!)
    nfc = setup_nfc()
    if nfc is None:
        print("[WARNUNG] NFC-Hardware nicht verfügbar - MQTT läuft ohne NFC")
        blink(4, 150)  # 4× mittel = NFC Fehler, aber weiter
    else:
        print("[NFC] Hardware erfolgreich initialisiert")
        blink(1, 500)  # 1× lang = alles OK

    print("\n[MAIN] Hauptgestartet - warte auf Tags…\n")

    # Initialer Heartbeat mit NFC-Status senden
    try:
        mqtt.publish_heartbeat(
            nfc_ok=NFC_STATUS_OK, nfc_error=NFC_ERROR_MSG, uptime=int(time.time())
        )
    except MQTTError as e:
        print(f"[MAIN] Initialer Heartbeat fehlgeschlagen: {e}")

    last_uid = None
    last_seen = 0
    ping_timer = time.time()
    heartbeat_timer = time.time()
    read_timer = time.time()

    # --- Hauptschleife ---
    try:
        while True:
            now = time.time()

            # 1. MQTT Keepalive (alle 30s)
            if now - ping_timer > 30:
                mqtt.ping()
                ping_timer = now

            # 2. Heartbeat mit NFC-Status (alle 60s)
            if now - heartbeat_timer > 60:
                try:
                    mqtt.publish_heartbeat(
                        nfc_ok=NFC_STATUS_OK, nfc_error=NFC_ERROR_MSG, uptime=int(now)
                    )
                    print("[HEARTBEAT] Status gesendet")
                except MQTTError as e:
                    print(f"[HEARTBEAT] Fehler: {e}")
                heartbeat_timer = now

            # 3. MQTT Reconnect wenn nötig
            if not mqtt.is_connected():
                print("[MAIN] MQTT getrennt, reconnect …")
                if mqtt.connect():
                    print("[MAIN] MQTT reconnect erfolgreich")
                else:
                    print("[MAIN] MQTT reconnect fehlgeschlagen")
                    time.sleep(5)
                    continue

            # 4. NFC lesen (nur wenn Hardware verfügbar)
            if nfc is not None and (now - read_timer) > (config.READ_INTERVAL_MS / 1000):
                read_timer = now
                try:
                    tag = nfc.read_passive_target(timeout_ms=100)
                except PN532Error as e:
                    print(f"[NFC] Lesefehler: {e}")
                    time.sleep_ms(500)
                    continue

                if tag is None:
                    last_uid = None  # Tag weg → Debounce zurücksetzen
                    continue

                uid = tag["uid_hex"]
                print(f"[NFC] Tag erkannt: {uid} ({tag['tag_type']})")

                # Debounce: gleichen Tag nicht zu oft senden
                if uid == last_uid and (now - last_seen) < config.DEBOUNCE_SAME_TAG_S:
                    continue

                # Publizieren
                try:
                    mqtt.publish_tag(tag, extra={"timestamp": get_timestamp()})
                    led.on()
                    time.sleep_ms(50)
                    led.off()
                    last_uid = uid
                    last_seen = now
                except MQTTError as e:
                    print(f"[MAIN] Publish-Fehler: {e}")

            else:
                # Kurze Pause wenn kein NFC oder Reading Interval
                time.sleep_ms(50)

    except Exception as e:
        print(f"[MAIN] Fehler: {e}")
        blink(5, 100)
    finally:
        # Aufräumen beim Beenden
        print("[MAIN] Beende...")
        try:
            mqtt.publish_status("offline")
        except MQTTError:
            pass
        mqtt.disconnect()
        print("[MAIN] MQTT disconnected")


# Automatisch starten wenn direkt ausgeführt oder vom Boot geladen
main()
