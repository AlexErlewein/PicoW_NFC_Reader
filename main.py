"""
main.py – Einstiegspunkt für den Pico W
NFC-Tags per I2C lesen und via MQTT weiterleiten.
"""

import json
import time

import wifi
from machine import I2C, Pin

import config
from lib.mqtt_manager import MQTTError, MQTTManager
from lib.pn532 import PN532, PN532Error, MIFARE_DEFAULT_KEY

# Onboard-LED als Statusanzeige
led = Pin("LED", Pin.OUT)
led_state = False

# NFC-Hardware-Status
NFC_STATUS_OK = False
NFC_ERROR_MSG = None

# Card write operation state
current_write_job = None  # Pending write operation
WRITE_TIMEOUT_S = 30  # Seconds to wait for card presentation


def handle_command(topic: bytes, msg: bytes):
    """Verarbeitet eingehende MQTT-Commands."""
    global led_state, current_write_job
    try:
        # Try to parse as JSON first (for structured commands)
        try:
            cmd = json.loads(msg.decode("utf-8"))
        except ValueError:
            # Not JSON, treat as plain string command
            cmd = msg.decode("utf-8").strip()

        if isinstance(cmd, str):
            print(f"[CMD] Received: {cmd}")
            if cmd == "led_toggle":
                led_state = not led_state
            elif cmd == "led_on":
                led_state = True
            elif cmd == "led_off":
                led_state = False
            else:
                print(f"[CMD] Unknown command: {cmd}")
                return

            # Apply LED state
            if led_state:
                led.on()
                print("[CMD] LED turned ON")
            else:
                led.off()
                print("[CMD] LED turned OFF")
            return

        # JSON command handling
        action = cmd.get("action")
        print(f"[CMD] JSON command: {action}")

        if action == "write_card":
            # Store write job - will execute when card presented
            current_write_job = {
                "member_id": cmd.get("member_id", ""),
                "name": cmd.get("name", ""),
                "email": cmd.get("email", ""),
                "signature": cmd.get("signature", ""),
                "sector": cmd.get("sector", 1),  # Default sector 1
                "timeout": time.time() + WRITE_TIMEOUT_S,
                "request_id": cmd.get("request_id", "")
            }
            print(f"[WRITE] Job stored, waiting for card (timeout: {WRITE_TIMEOUT_S}s)")

    except Exception as e:
        print(f"[CMD] Error processing command: {e}")


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

    # Längere Pause damit PN532 komplett hochfährt
    time.sleep_ms(500)

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
        nfc.SAM_configuration()
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


def perform_card_write(nfc: PN532, tag: dict, job: dict) -> dict:
    """Write member data to MIFARE Classic card.

    Data layout (Sector 1 for 1K cards):
    - Block 4: Magic header + member_id (padded to 16 bytes)
    - Block 5: Name (16 bytes, truncated/padded)
    - Block 6: Email prefix (16 bytes, truncated/padded)
    - Sector 2: Signature for verification
    """
    uid = tag["uid"]
    sector = job.get("sector", 1)
    member_id = job.get("member_id", "")
    name = job.get("name", "")
    email = job.get("email", "")
    signature = job.get("signature", "")
    request_id = job.get("request_id", "")

    # Calculate block numbers from sector
    # Sector 0: blocks 0-3, Sector 1: blocks 4-7, etc.
    base_block = sector * 4

    try:
        # Step 1: Authenticate sector 1 (key A)
        print(f"[WRITE] Authenticating sector {sector}...")
        if not nfc.mifare_authenticate(uid, base_block, MIFARE_DEFAULT_KEY, "A"):
            return {
                "success": False,
                "error": f"Authentication failed for sector {sector}",
                "uid": tag["uid_hex"],
                "request_id": request_id
            }
        print("[WRITE] Authentication successful")

        # Step 2: Prepare data blocks (each exactly 16 bytes)
        # Block 4: Magic header + member_id
        header = b"GNDCTRL|"
        mid_bytes = member_id.encode("utf-8")[:8]  # Max 8 chars for member_id
        block_4 = header + mid_bytes.ljust(8, b"\x00")  # Pad to 16 bytes

        # Block 5: Name (16 bytes)
        name_bytes = name.encode("utf-8")[:16]
        block_5 = name_bytes.ljust(16, b"\x00")

        # Block 6: Email prefix (16 bytes)
        email_bytes = email.encode("utf-8")[:16]
        block_6 = email_bytes.ljust(16, b"\x00")

        # Step 3: Write blocks
        print(f"[WRITE] Writing block {base_block} (member_id)...")
        if not nfc.mifare_write_block(base_block, block_4):
            return {"success": False, "error": f"Failed to write block {base_block}", "request_id": request_id}

        print(f"[WRITE] Writing block {base_block + 1} (name)...")
        if not nfc.mifare_write_block(base_block + 1, block_5):
            return {"success": False, "error": f"Failed to write block {base_block + 1}", "request_id": request_id}

        print(f"[WRITE] Writing block {base_block + 2} (email)...")
        if not nfc.mifare_write_block(base_block + 2, block_6):
            return {"success": False, "error": f"Failed to write block {base_block + 2}", "request_id": request_id}

        # Step 4: Write signature to sector 2 if provided
        if signature:
            sig_sector = 2
            sig_base = sig_sector * 4  # Block 8

            print(f"[WRITE] Authenticating sector {sig_sector} for signature...")
            if nfc.mifare_authenticate(uid, sig_base, MIFARE_DEFAULT_KEY, "A"):
                # Signature can be up to 48 bytes (3 blocks)
                sig_bytes = signature.encode("utf-8")[:48]
                sig_padded = sig_bytes.ljust(48, b"\x00")

                # Write to blocks 8, 9, 10 (sector 2, blocks 0-2)
                for i in range(3):
                    block_num = sig_base + i
                    block_data = sig_padded[i*16:(i+1)*16]
                    print(f"[WRITE] Writing signature block {block_num}...")
                    if not nfc.mifare_write_block(block_num, block_data):
                        print(f"[WRITE] Warning: Failed to write signature block {block_num}")
                        # Continue anyway - main data was written
                        break
                print("[WRITE] Signature written")
            else:
                print("[WRITE] Warning: Could not authenticate sector 2 for signature")

        # Success!
        blink(5, 50)  # Quick blinks = success
        return {
            "success": True,
            "uid": tag["uid_hex"],
            "sector": sector,
            "blocks_written": 3,
            "request_id": request_id
        }

    except Exception as e:
        return {"success": False, "error": str(e), "uid": tag.get("uid_hex", ""), "request_id": request_id}


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

    # Command-Handler registrieren und Topic abonnieren
    mqtt.set_command_callback(handle_command)
    try:
        mqtt.subscribe_commands()
    except MQTTError as e:
        print(f"[FEHLER] Command subscription fehlgeschlagen: {e}")

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

            # 0. MQTT Messages prüfen (non-blocking)
            mqtt.check_messages()

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

                # Check for write job timeout
                global current_write_job
                if current_write_job and now > current_write_job["timeout"]:
                    print("[WRITE] Job timeout - clearing")
                    try:
                        mqtt.publish_write_response({
                            "success": False,
                            "error": "Timeout - no card presented within 30s",
                            "request_id": current_write_job.get("request_id", "")
                        })
                    except MQTTError:
                        pass
                    current_write_job = None

                try:
                    tag = nfc.read_passive_target(timeout=0.1)
                except PN532Error as e:
                    print(f"[NFC] Lesefehler: {e}")
                    time.sleep_ms(500)
                    continue

                if tag is None:
                    last_uid = None  # Tag weg → Debounce zurücksetzen
                    continue

                uid = tag["uid_hex"]
                print(f"[NFC] Tag erkannt: {uid} ({tag['tag_type']})")

                # Check for pending write job
                if current_write_job:
                    print(f"[WRITE] Executing write job for card {uid}")
                    result = perform_card_write(nfc, tag, current_write_job)
                    try:
                        mqtt.publish_write_response(result)
                    except MQTTError as e:
                        print(f"[WRITE] Failed to publish response: {e}")
                    current_write_job = None

                    # Still publish normal tag read after write
                    try:
                        mqtt.publish_tag(tag, extra={"timestamp": get_timestamp(), "written": True})
                    except MQTTError:
                        pass

                    led.on()
                    time.sleep_ms(100)
                    led.off()
                    last_uid = uid
                    last_seen = now
                    continue

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
