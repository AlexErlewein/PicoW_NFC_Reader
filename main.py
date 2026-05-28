"""
main.py – Einstiegspunkt für den Pico W
NFC-Tags per SPI lesen und via MQTT weiterleiten.
"""

import json
import time

import wifi
from machine import Pin, I2C

import config
from lib.mqtt_manager import MQTTError, MQTTManager

# RFID-Treiber basierend auf Konfiguration laden
if config.RFID_READER_TYPE == "rc522":
    from lib.rc522 import MFRC522 as RC522
elif config.RFID_READER_TYPE == "pn522":
    from lib.pn522 import PN522 as RC522
else:
    raise ValueError(f"Unbekannter RFID-Reader-Typ: {config.RFID_READER_TYPE}")

MIFARE_DEFAULT_KEY = b'\xff\xff\xff\xff\xff\xff'

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


def setup_nfc() -> RC522 | None:
    """Initialisiert NFC-Hardware. Gibt None bei Fehler zurück (setzt globale Status-Variablen)."""
    global NFC_STATUS_OK, NFC_ERROR_MSG

    if config.RFID_READER_TYPE == "rc522":
        print("[NFC] Initialisiere RC522 (SPI) …")
        try:
            nfc = RC522(
                sck=config.SPI_SCK,
                mosi=config.SPI_MOSI,
                miso=config.SPI_MISO,
                rst=config.SPI_RST,
                cs=config.SPI_CS
            )
            print("[NFC] RC522 initialisiert")
            NFC_STATUS_OK = True
            NFC_ERROR_MSG = None
            return nfc
        except Exception as e:
            error = f"RC522 Initialisierung fehlgeschlagen: {e}"
            print(f"[NFC] FEHLER: {error}")
            NFC_STATUS_OK = False
            NFC_ERROR_MSG = error
            return None

    elif config.RFID_READER_TYPE == "pn522":
        print("[NFC] Initialisiere PN522 (I2C) …")
        try:
            i2c = I2C(config.I2C_BUS, sda=Pin(config.I2C_SDA), scl=Pin(config.I2C_SCL), freq=config.I2C_FREQ)

            # I2C-Scan zur Fehlersuche
            print(f"[NFC] Scanne I2C-Bus {config.I2C_BUS}...")
            devices = i2c.scan()
            if not devices:
                print(f"[NFC] FEHLER: Keine I2C-Geräte gefunden. Prüfe Verdrahtung!")
                print(f"[NFC] SDA: GP{config.I2C_SDA}, SCL: GP{config.I2C_SCL}")
                NFC_STATUS_OK = False
                NFC_ERROR_MSG = "Keine I2C-Geräte gefunden"
                return None
            else:
                print(f"[NFC] I2C-Geräte gefunden: {[hex(d) for d in devices]}")

            nfc = RC522(i2c)
            fw = nfc.get_firmware_version()
            print(f"[NFC] PN522 Firmware: {fw}")

            # SAM-Konfiguration - wichtig für PN522!
            print("[NFC] Konfiguriere SAM...")
            nfc.SAM_configuration()

            print("[NFC] PN522 initialisiert")
            NFC_STATUS_OK = True
            NFC_ERROR_MSG = None
            return nfc
        except Exception as e:
            error = f"PN522 Initialisierung fehlgeschlagen: {e}"
            print(f"[NFC] FEHLER: {error}")
            NFC_STATUS_OK = False
            NFC_ERROR_MSG = error
            return None

    else:
        print(f"[NFC] Unbekannter Reader-Typ: {config.RFID_READER_TYPE}")
        NFC_STATUS_OK = False
        NFC_ERROR_MSG = f"Unbekannter Reader-Typ: {config.RFID_READER_TYPE}"
        return None


def read_card_data(nfc: RC522, tag: dict) -> dict | None:
    """Read GNDCTRL member data from card if present.

    Returns dict with member_id, name, email or None if no data / read error.
    """
    sak_str = tag.get("sak", "0x08")
    try:
        sak_val = int(sak_str, 16) if isinstance(sak_str, str) else int(sak_str)
    except Exception:
        sak_val = 0x08
    is_ntag = (sak_val == 0x00)

    try:
        if is_ntag:
            # NTAG READ (0x30) returns 4 pages (16 bytes) at once
            data_header = nfc.mifare_read_block(4)   # pages 4-7
            data_name = nfc.mifare_read_block(8)     # pages 8-11
            data_email = nfc.mifare_read_block(12)   # pages 12-15
        else:
            # MIFARE Classic: authenticate sector 1, read blocks 4-6
            uid = tag["uid"]
            if not nfc.mifare_authenticate(uid, 4, MIFARE_DEFAULT_KEY, "A"):
                return None
            data_header = nfc.mifare_read_block(4)
            data_name = nfc.mifare_read_block(5)
            data_email = nfc.mifare_read_block(6)

        # Check for GNDCTRL magic header
        if data_header[:8] != b"GNDCTRL|":
            return None

        member_id = data_header[8:].rstrip(b'\x00').decode('utf-8', 'replace')
        name = data_name.rstrip(b'\x00').decode('utf-8', 'replace')
        email = data_email.rstrip(b'\x00').decode('utf-8', 'replace')

        result = {"member_id": member_id}
        if name:
            result["name"] = name
        if email:
            result["email"] = email

        # Read signature
        try:
            if is_ntag:
                # Pages 16-27: 3 reads × 16 bytes = 48 bytes
                sig_bytes = b""
                for page_start in [16, 20, 24]:
                    d = nfc.mifare_read_block(page_start)
                    if d:
                        sig_bytes += bytes(d)
                sig_str = sig_bytes.rstrip(b'\x00').decode('utf-8', 'replace')
                if sig_str:
                    result["signature"] = sig_str
            else:
                # Sector 2, blocks 8-10: 3 × 16 bytes = 48 bytes
                uid = tag["uid"]
                if nfc.mifare_authenticate(uid, 8, MIFARE_DEFAULT_KEY, "A"):
                    sig_bytes = b""
                    for blk in [8, 9, 10]:
                        d = nfc.mifare_read_block(blk)
                        if d:
                            sig_bytes += bytes(d)
                    sig_str = sig_bytes.rstrip(b'\x00').decode('utf-8', 'replace')
                    if sig_str:
                        result["signature"] = sig_str
        except Exception:
            pass  # Signature read failure is non-fatal

        return result

    except Exception as e:
        print(f"[READ] Card data read error: {e}")
        return None


def perform_card_write(nfc: RC522, tag: dict, job: dict) -> dict:
    """Write member data to MIFARE Classic card.

    Data layout (Sector 1 for 1K cards):
    - Block 4: Magic header + member_id (padded to 16 bytes)
    - Block 5: Name (16 bytes, truncated/padded)
    - Block 6: Email prefix (16 bytes, truncated/padded)
    - Sector 2: Signature for verification
    """
    uid = tag["uid"]
    member_id = job.get("member_id", "")
    name = job.get("name", "")
    email = job.get("email", "")
    signature = job.get("signature", "")
    request_id = job.get("request_id", "")

    # Detect card type from SAK (0x00 = NTAG/Ultralight, 0x08/0x18 = MIFARE Classic)
    sak_str = tag.get("sak", "0x08")
    try:
        sak_val = int(sak_str, 16) if isinstance(sak_str, str) else int(sak_str)
    except Exception:
        sak_val = 0x08
    is_ntag = (sak_val == 0x00)

    # Prepare data blocks (same layout for both card types, 16 bytes each)
    header = b"GNDCTRL|"
    mid_bytes = member_id.encode("utf-8")[:8]
    block_4 = header + mid_bytes + b"\x00" * (8 - len(mid_bytes))   # 16 bytes

    name_bytes = name.encode("utf-8")[:16]
    block_5 = name_bytes + b"\x00" * (16 - len(name_bytes))          # 16 bytes

    email_bytes = email.encode("utf-8")[:16]
    block_6 = email_bytes + b"\x00" * (16 - len(email_bytes))        # 16 bytes

    try:
        if is_ntag:
            # NTAG/Ultralight: page-based writes, 4 bytes per page, user memory at page 4
            print(f"[WRITE] NTAG detected (SAK={sak_str}), using page writes...")
            # MIFARE Ultralight C requires authentication before writes
            try:
                nfc.ultralight_c_authenticate()
                print("[WRITE] Ultralight C authentication successful")
            except Exception:
                print("[WRITE] Not Ultralight C or auth not required, proceeding...")
            start_page = 4
            ntag_success = True
            for chunk_idx, chunk in enumerate([block_4, block_5, block_6]):
                print(f"[WRITE] Batch {chunk_idx + 1}/3 (pages {start_page + chunk_idx * 4}-{start_page + chunk_idx * 4 + 3})")
                for i in range(4):
                    page = start_page + chunk_idx * 4 + i
                    page_data = chunk[i*4:(i+1)*4]
                    print(f"[WRITE] Writing NTAG page {page}...")
                    written = False
                    for attempt in range(8):
                        try:
                            if nfc.ntag_write_page(page, page_data):
                                written = True
                                break
                            # Error response — wait and retry
                            print(f"[WRITE] Retry {attempt + 1} for page {page}...")
                            if attempt < 4:
                                time.sleep_ms(300)
                            else:
                                # Persistent failure: re-detect card to reset comms
                                print("[WRITE] Re-detecting card...")
                                time.sleep_ms(1000)
                                retry_tag = nfc.read_passive_target(timeout=1.0)
                                if retry_tag is None:
                                    print("[WRITE] Card lost during retry")
                                    break
                                time.sleep_ms(1000)  # Stabilize after re-select
                        except Exception:
                            # Comms failure — re-detect
                            print(f"[WRITE] Retry {attempt + 1} for page {page} (re-detecting)...")
                            time.sleep_ms(1000)
                            retry_tag = nfc.read_passive_target(timeout=1.0)
                            if retry_tag is None:
                                print("[WRITE] Card lost during retry")
                                break
                            time.sleep_ms(1000)  # Stabilize after re-select
                    if not written:
                        ntag_success = False
                        print(f"[WRITE] NTAG write failed on page {page}, trying MIFARE Classic fallback...")
                        break
                    time.sleep_ms(100)  # NTAG EEPROM write time + RF recovery
                if not ntag_success:
                    break
                # Long recovery between batches to let card power supply recharge
                if chunk_idx < 2:
                    print(f"[WRITE] Recovery pause ({chunk_idx + 1}/3)...")
                    time.sleep_ms(2000)

            if ntag_success:
                print("[WRITE] NTAG write complete")
                # Write signature to pages 16-27 (48 bytes)
                if signature:
                    print("[WRITE] Writing signature to pages 16-27...")
                    sig_raw = signature.encode("utf-8")[:48]
                    sig_padded = sig_raw + b"\x00" * (48 - len(sig_raw))
                    for i in range(12):
                        nfc.ntag_write_page(16 + i, sig_padded[i * 4:(i + 1) * 4])
                    print("[WRITE] Signature written")
            else:
                print("[WRITE] NTAG write failed, falling back to MIFARE Classic block writes...")
                # Try MIFARE Classic write as fallback
                sector = job.get("sector", 1)
                base_block = sector * 4
                print(f"[WRITE] Authenticating sector {sector}...")
                if not nfc.mifare_authenticate(uid, base_block, MIFARE_DEFAULT_KEY, "A"):
                    return {"success": False, "error": f"Fallback: Authentication failed for sector {sector}", "uid": tag["uid_hex"], "request_id": request_id}
                print("[WRITE] Authentication successful")
                if not nfc.mifare_write_block(base_block, block_4):
                    return {"success": False, "error": f"Fallback: Failed to write block {base_block}", "uid": tag["uid_hex"], "request_id": request_id}
                if not nfc.mifare_write_block(base_block + 1, block_5):
                    return {"success": False, "error": f"Fallback: Failed to write block {base_block + 1}", "uid": tag["uid_hex"], "request_id": request_id}
                if not nfc.mifare_write_block(base_block + 2, block_6):
                    return {"success": False, "error": f"Fallback: Failed to write block {base_block + 2}", "uid": tag["uid_hex"], "request_id": request_id}
                print("[WRITE] MIFARE Classic fallback write complete")

        else:
            # MIFARE Classic: sector-based auth + 16-byte block writes
            sector = job.get("sector", 1)
            base_block = sector * 4
            print(f"[WRITE] MIFARE Classic detected (SAK={sak_str}), authenticating sector {sector}...")
            if not nfc.mifare_authenticate(uid, base_block, MIFARE_DEFAULT_KEY, "A"):
                return {"success": False, "error": f"Authentication failed for sector {sector}", "uid": tag["uid_hex"], "request_id": request_id}
            print("[WRITE] Authentication successful")

            for block_offset, (label, data) in enumerate([(("member_id", block_4)), ("name", block_5), ("email", block_6)]):
                block_num = base_block + block_offset
                print(f"[WRITE] Writing block {block_num} ({label})...")
                if not nfc.mifare_write_block(block_num, data):
                    return {"success": False, "error": f"Failed to write block {block_num}", "uid": tag["uid_hex"], "request_id": request_id}

            # Write signature to sector 2
            if signature:
                sig_base = 2 * 4  # Block 8
                if nfc.mifare_authenticate(uid, sig_base, MIFARE_DEFAULT_KEY, "A"):
                    sig_raw = signature.encode("utf-8")[:48]
                    sig_padded = sig_raw + b"\x00" * (48 - len(sig_raw))
                    for i in range(3):
                        nfc.mifare_write_block(sig_base + i, sig_padded[i*16:(i+1)*16])
                    print("[WRITE] Signature written")

        blink(5, 50)
        return {"success": True, "uid": tag["uid_hex"], "request_id": request_id}

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

    # Register command handler and subscribe to command topic
    mqtt.set_command_callback(handle_command)
    mqtt.subscribe_commands()

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
                    if config.RFID_READER_TYPE == "rc522":
                        tag = nfc.read_tag()

                        # RC522 uses REQIDL — a card briefly in ACTIVE state after
                        # the previous read won't respond. One retry after a short
                        # pause is enough for it to fall back to IDLE.
                        if tag is None and current_write_job:
                            print("[WRITE] Card not visible, retrying for RC522...")
                            time.sleep_ms(500)
                            tag = nfc.read_tag()
                            if tag:
                                print(f"[WRITE] Re-detected: {tag['uid_hex']}")

                    elif config.RFID_READER_TYPE == "pn522":
                        # PN522 API: read_passive_target() -> tag dict
                        tag = nfc.read_passive_target(timeout=0.5)

                        # If write job pending and no card detected, wait and retry
                        # (card may be in ACTIVE state from previous read_card_data)
                        if tag is None and current_write_job:
                            print("[WRITE] Card not visible, waiting for watchdog...")
                            time.sleep_ms(1000)
                            tag = nfc.read_passive_target(timeout=1.0)
                            if tag:
                                print(f"[WRITE] Re-detected: {tag['uid_hex']}")

                except Exception as e:
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

                # Read stored member data from card
                card_data = read_card_data(nfc, tag)
                if card_data:
                    print(f"[READ] Card data: {card_data}")

                # Publizieren
                try:
                    extra = {"timestamp": get_timestamp()}
                    if card_data:
                        extra.update(card_data)
                    mqtt.publish_tag(tag, extra=extra)
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
