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


def blink(times: int = 1, on_ms: int = 100, off_ms: int = 100):
    for _ in range(times):
        led.on()
        time.sleep_ms(on_ms)
        led.off()
        time.sleep_ms(off_ms)


def get_timestamp() -> str:
    """Einfacher Timestamp (Sekunden seit Boot – NTP wäre Erweiterung)."""
    return str(time.time())


def setup_nfc() -> PN532:
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
        raise RuntimeError("PN532 nicht gefunden! Verdrahtung prüfen (SDA=GP4, SCL=GP5)")

    nfc = PN532(i2c)
    fw = nfc.get_firmware_version()
    print(f"[NFC] PN532 Firmware: v{fw['ver']}.{fw['rev']}")
    nfc.sam_configuration()
    print("[NFC] Bereit.")
    return nfc


def main():
    print("\n=== Pico W NFC → MQTT ===\n")
    blink(2, 200)  # Startblinken

    # 1. WLAN
    try:
        wifi.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        blink(3, 100)
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

    # 3. NFC
    try:
        nfc = setup_nfc()
    except Exception as e:
        print(f"[FEHLER] NFC: {e}")
        blink(7, 100)
        return

    print("\n[MAIN] Warte auf NFC-Tags …\n")
    blink(1, 500)  # Langes Blinken = alles OK

    last_uid = None
    last_seen = 0
    ping_timer = time.time()

    # --- Hauptschleife ---
    try:
        while True:
            now = time.time()

            # MQTT Keepalive
            if now - ping_timer > 30:
                mqtt.ping()
                ping_timer = now

            # Reconnect wenn nötig
            if not mqtt.is_connected():
                print("[MAIN] MQTT getrennt, reconnect …")
                mqtt.connect()

        # NFC lesen
        try:
            tag = nfc.read_passive_target(timeout_ms=config.READ_INTERVAL_MS)
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
            time.sleep_ms(config.READ_INTERVAL_MS)
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

            nfc.wait_for_tag_removed()

    except Exception as e:
        print(f"[MAIN] Fehler: {e}")
        blink(5, 100)
    finally:
        # Aufräumen beim Beenden
        print("[MAIN] Beende...")
        mqtt.disconnect()
        print("[MAIN] MQTT disconnected")


# Automatisch starten wenn direkt ausgeführt oder vom Boot geladen
main()
