"""
main.py – Einstiegspunkt für den Pico W
NFC-Tags per SPI lesen und via MQTT weiterleiten.
"""

import json
import math
import time
from array import array

import wifi
from machine import I2C, PWM, Pin, Timer

import config
from lib.mqtt_manager import MQTTError, MQTTManager

# RFID-Treiber basierend auf Konfiguration laden
if config.RFID_READER_TYPE == "rc522":
    from lib.rc522 import MFRC522 as RC522
elif config.RFID_READER_TYPE == "pn522":
    from lib.pn522 import PN522 as RC522
else:
    raise ValueError(f"Unbekannter RFID-Reader-Typ: {config.RFID_READER_TYPE}")

MIFARE_DEFAULT_KEY = b"\xff\xff\xff\xff\xff\xff"

# Onboard-LED als Statusanzeige
led = Pin("LED", Pin.OUT)
led_state = False

# RGB LED - Pinout: R=GP15, G=GP14, B=GP16
rgb_red = PWM(Pin(15, Pin.OUT))
rgb_blue = PWM(Pin(16, Pin.OUT))
rgb_red.freq(1000)
rgb_blue.freq(1000)

# Green LED unusable on some boards: either the green LED is defective
# (led_green_defect) or the pins are swapped so GPIO14 is the common pin
# and one anode is tied to GND (led_pins_swapped). When set, blue is used
# for idle & success and red remains for errors.
_GREEN_AVAILABLE = not (config.LED_GREEN_DEFECT or config.LED_PINS_SWAPPED)

# Common-anode LEDs invert the PWM logic: drive a channel LOW to turn a color
# ON, and the common pin must be held HIGH (3.3V) instead of LOW (GND).
_COMMON_ANODE = config.LED_COMMON_ANODE
_PWM_OFF = 65535 if _COMMON_ANODE else 0

if config.LED_PINS_SWAPPED:
    # GPIO14 is wired as the common pin instead of a real GND/VCC rail.
    # Drive it as a plain digital output (solid HIGH or LOW depending on
    # polarity) so the other colors can source/sink current reliably.
    rgb_common = Pin(14, Pin.OUT)
    if _COMMON_ANODE:
        rgb_common.high()
    else:
        rgb_common.low()

    class _NullPWM:
        """No-op stand-in so set_rgb()/update_led() can call duty_u16/freq
        without touching the pin that is now a digital common pin."""

        def duty_u16(self, _val: int):
            pass

        def freq(self, _freq: int):
            pass

    rgb_green = _NullPWM()
else:
    rgb_green = PWM(Pin(14, Pin.OUT))
    rgb_green.freq(10000)


def set_rgb(r: int, g: int, b: int):
    """Setze RGB-Farbwerte (0-255). Common cathode: higher = brighter.
    Common anode: inverted internally (drive LOW = ON)."""
    # r*257 == r*65535//255; integer-only so it's safe to call from the timer ISR
    if not _GREEN_AVAILABLE:
        g = 0
    if _COMMON_ANODE:
        r, g, b = 255 - r, 255 - g, 255 - b
    rgb_red.duty_u16(r * 257)
    rgb_green.duty_u16(g * 257)
    rgb_blue.duty_u16(b * 257)


def rgb_off():
    set_rgb(0, 0, 0)


def rainbow_cycle(delay_ms: int = 500):
    """Blende durch alle Farben des Regenbogens."""
    colors = [
        (255, 0, 0),  # Rot
        (255, 127, 0),  # Orange
        (255, 255, 0),  # Gelb
        (0, 255, 0),  # Grün
        (0, 0, 255),  # Blau
        (75, 0, 130),  # Indigo
        (148, 0, 211),  # Violett
        (255, 0, 0),  # Zurück zu Rot
    ]
    for r, g, b in colors:
        set_rgb(r, g, b)
        time.sleep_ms(delay_ms)
    rgb_off()


def pulse_rgb(r: int, g: int, b: int, cycle_time_ms: int = 200):
    """Pulse eine Farbe sanft ein und aus."""
    # Fade in (0 to 255)
    steps = 50
    for i in range(steps):
        intensity = int(255 * (1 - (i / steps) ** 2))  # Parabolic curve for smoother fade
        set_rgb(int(r * intensity / 255), int(g * intensity / 255), int(b * intensity / 255))
        time.sleep_ms(cycle_time_ms // (steps * 2))
    # Fade out (255 to 0)
    for i in range(steps):
        intensity = int(255 * ((i / steps) ** 2))
        set_rgb(int(r * intensity / 255), int(g * intensity / 255), int(b * intensity / 255))
        time.sleep_ms(cycle_time_ms // (steps * 2))


# NFC-Hardware-Status
NFC_STATUS_OK = False
NFC_ERROR_MSG = None

# Card write operation state
current_write_job = None  # Pending write operation
WRITE_TIMEOUT_S = 30  # Seconds to wait for card presentation

# LED state management
LED_STATE_IDLE = "idle"  # Green fading in and out
LED_STATE_SCANNING = "scanning"  # Blue solid when tag detected
LED_STATE_SUCCESS = "success"  # Fast green blink on success
LED_STATE_ERROR = "error"  # Red blink on error
current_led_state = LED_STATE_IDLE
LED_SCAN_DISPLAY_MS = 2000  # Show blue LED for 2s when scanning
LED_SUCCESS_BLINK_COUNT = 10  # Number of green blinks on success
LED_ERROR_BLINK_COUNT = 3  # Number of red blinks on error
LED_BREATH_PERIOD_MS = 2500  # Full breath in+out cycle time
LED_TICK_MS = 10  # Hardware-timer tick for LED animation (100 Hz)
_BREATH_LEN = 256
# Precomputed raised-cosine brightness table (0 -> full -> 0) for a smooth
# breathing curve; uint16 maps directly to duty_u16. Indexed inside the ISR.
BREATH_TABLE = array(
    "H",
    (
        int(65535 * (0.5 - 0.5 * math.cos(2 * math.pi * i / _BREATH_LEN)))
        for i in range(_BREATH_LEN)
    ),
)
led_anim_ms = 0  # Animation counter – advanced by real elapsed time on each LED update
led_last_update_ms = 0  # ticks_ms of the previous LED update
led_next_state = None  # State to transition to after current state's delay


def handle_command(topic: bytes, msg: bytes):
    """Verarbeitet eingehende MQTT-Commands."""
    global led_state, current_write_job, current_led_state
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
                # Toggle between idle and off
                if current_led_state == LED_STATE_IDLE:
                    rgb_off()
                    current_led_state = "off"
                else:
                    set_led_state(LED_STATE_IDLE)
            elif cmd == "led_on":
                set_led_state(LED_STATE_IDLE)
                print("[CMD] LED turned ON (idle mode)")
            elif cmd == "led_off":
                rgb_off()
                current_led_state = "off"
                print("[CMD] LED turned OFF")
            else:
                print(f"[CMD] Unknown command: {cmd}")
                return
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
                "request_id": cmd.get("request_id", ""),
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


def rgb_blink(
    times: int = 1, on_ms: int = 100, off_ms: int = 100, r: int = 255, g: int = 255, b: int = 255
):
    """Blinke mit RGB-LED in bestimmter Farbe."""
    for _ in range(times):
        set_rgb(r, g, b)
        time.sleep_ms(on_ms)
        rgb_off()
        time.sleep_ms(off_ms)


def rgb_rainbow_blink(times: int = 1, cycle_delay_ms: int = 500):
    """Blinke durch den Regenbogen mehrfach."""
    for _ in range(times):
        rainbow_cycle(cycle_delay_ms)
        time.sleep_ms(100)


def set_led_state(state: str):
    """Set the current LED state and reset timer.

    Timers are reset BEFORE flipping the state so the timer ISR can never
    observe a fresh state with a stale animation counter (which would cause
    a one-frame glitch or a spurious transition)."""
    global current_led_state, led_anim_ms, led_last_update_ms
    led_anim_ms = 0
    led_last_update_ms = time.ticks_ms()
    current_led_state = state


def update_led():
    """Update LED based on current state. Call this regularly in main loop."""
    global current_led_state, led_anim_ms, led_last_update_ms, led_next_state

    if current_led_state == "off":
        rgb_off()
        return

    if current_led_state == "nfc_error":
        # NFC hardware not available — solid red indicator
        set_rgb(255, 0, 0)
        return

    # Advance the animation by the real time elapsed since the last update, so
    # the animation stays at the correct speed even if the loop is busy (e.g.
    # during NFC reads). Capped to avoid a big jump after a long blocking pause.
    # Reset to 0 on each state change, so transitions don't jump.
    now_ms = time.ticks_ms()
    delta = time.ticks_diff(now_ms, led_last_update_ms)
    led_last_update_ms = now_ms
    if delta < 0:
        delta = 0
    elif delta > 250:
        delta = 250
    led_anim_ms += delta
    elapsed_ms = led_anim_ms

    if current_led_state == LED_STATE_IDLE:
        idx = (elapsed_ms * _BREATH_LEN // LED_BREATH_PERIOD_MS) % _BREATH_LEN
        val = BREATH_TABLE[idx]
        if _COMMON_ANODE:
            val = 65535 - val
        rgb_red.duty_u16(_PWM_OFF)
        if _GREEN_AVAILABLE:
            rgb_green.duty_u16(val)
            rgb_blue.duty_u16(_PWM_OFF)
        else:
            rgb_green.duty_u16(_PWM_OFF)
            rgb_blue.duty_u16(val)
        return

    if current_led_state == LED_STATE_SCANNING:
        set_rgb(0, 0, 255)
        if elapsed_ms >= LED_SCAN_DISPLAY_MS and led_next_state is not None:
            next_state = led_next_state
            led_next_state = None
            set_led_state(next_state)
        return

    if current_led_state == LED_STATE_SUCCESS:
        blink_pos = elapsed_ms // 100
        if blink_pos < LED_SUCCESS_BLINK_COUNT * 2:
            if blink_pos % 2 == 0:
                if _GREEN_AVAILABLE:
                    set_rgb(0, 255, 0)
                else:
                    set_rgb(0, 0, 255)
            else:
                set_rgb(0, 0, 0)
        else:
            set_led_state(LED_STATE_IDLE)
        return

    if current_led_state == LED_STATE_ERROR:
        blink_pos = elapsed_ms // 200
        if blink_pos < LED_ERROR_BLINK_COUNT * 2:
            if blink_pos % 2 == 0:
                set_rgb(255, 0, 0)
            else:
                set_rgb(0, 0, 0)
        else:
            set_led_state(LED_STATE_IDLE)
        return


def _led_timer_cb(_timer):
    """Hardware-timer ISR: advances the LED animation independently of the
    main loop, so breathing continues during blocking NFC reads / sleeps."""
    update_led()


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
                cs=config.SPI_CS,
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
            i2c = I2C(
                config.I2C_BUS,
                sda=Pin(config.I2C_SDA),
                scl=Pin(config.I2C_SCL),
                freq=config.I2C_FREQ,
            )

            # I2C-Scan zur Fehlersuche
            print(f"[NFC] Scanne I2C-Bus {config.I2C_BUS}...")
            devices = i2c.scan()
            if not devices:
                print("[NFC] FEHLER: Keine I2C-Geräte gefunden. Prüfe Verdrahtung!")
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
    is_ntag = sak_val == 0x00

    try:
        if is_ntag:
            # NTAG READ (0x30) returns 4 pages (16 bytes) at once
            data_header = nfc.mifare_read_block(4)  # pages 4-7
            data_name = nfc.mifare_read_block(8)  # pages 8-11
            data_email = nfc.mifare_read_block(12)  # pages 12-15
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

        member_id = data_header[8:].rstrip(b"\x00").decode("utf-8", "replace")
        name = data_name.rstrip(b"\x00").decode("utf-8", "replace")
        email = data_email.rstrip(b"\x00").decode("utf-8", "replace")

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
                sig_str = sig_bytes.rstrip(b"\x00").decode("utf-8", "replace")
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
                    sig_str = sig_bytes.rstrip(b"\x00").decode("utf-8", "replace")
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
    is_ntag = sak_val == 0x00

    # Prepare data blocks (same layout for both card types, 16 bytes each)
    header = b"GNDCTRL|"
    mid_bytes = member_id.encode("utf-8")[:8]
    block_4 = header + mid_bytes + b"\x00" * (8 - len(mid_bytes))  # 16 bytes

    name_bytes = name.encode("utf-8")[:16]
    block_5 = name_bytes + b"\x00" * (16 - len(name_bytes))  # 16 bytes

    email_bytes = email.encode("utf-8")[:16]
    block_6 = email_bytes + b"\x00" * (16 - len(email_bytes))  # 16 bytes

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
                print(
                    f"[WRITE] Batch {chunk_idx + 1}/3 (pages {start_page + chunk_idx * 4}-{start_page + chunk_idx * 4 + 3})"
                )
                for i in range(4):
                    page = start_page + chunk_idx * 4 + i
                    page_data = chunk[i * 4 : (i + 1) * 4]
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
                        print(
                            f"[WRITE] NTAG write failed on page {page}, trying MIFARE Classic fallback..."
                        )
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
                        nfc.ntag_write_page(16 + i, sig_padded[i * 4 : (i + 1) * 4])
                    print("[WRITE] Signature written")
            else:
                print("[WRITE] NTAG write failed, falling back to MIFARE Classic block writes...")
                # Try MIFARE Classic write as fallback
                sector = job.get("sector", 1)
                base_block = sector * 4
                print(f"[WRITE] Authenticating sector {sector}...")
                if not nfc.mifare_authenticate(uid, base_block, MIFARE_DEFAULT_KEY, "A"):
                    return {
                        "success": False,
                        "error": f"Fallback: Authentication failed for sector {sector}",
                        "uid": tag["uid_hex"],
                        "request_id": request_id,
                    }
                print("[WRITE] Authentication successful")
                if not nfc.mifare_write_block(base_block, block_4):
                    return {
                        "success": False,
                        "error": f"Fallback: Failed to write block {base_block}",
                        "uid": tag["uid_hex"],
                        "request_id": request_id,
                    }
                if not nfc.mifare_write_block(base_block + 1, block_5):
                    return {
                        "success": False,
                        "error": f"Fallback: Failed to write block {base_block + 1}",
                        "uid": tag["uid_hex"],
                        "request_id": request_id,
                    }
                if not nfc.mifare_write_block(base_block + 2, block_6):
                    return {
                        "success": False,
                        "error": f"Fallback: Failed to write block {base_block + 2}",
                        "uid": tag["uid_hex"],
                        "request_id": request_id,
                    }
                print("[WRITE] MIFARE Classic fallback write complete")

        else:
            # MIFARE Classic: sector-based auth + 16-byte block writes
            sector = job.get("sector", 1)
            base_block = sector * 4
            print(
                f"[WRITE] MIFARE Classic detected (SAK={sak_str}), authenticating sector {sector}..."
            )
            if not nfc.mifare_authenticate(uid, base_block, MIFARE_DEFAULT_KEY, "A"):
                return {
                    "success": False,
                    "error": f"Authentication failed for sector {sector}",
                    "uid": tag["uid_hex"],
                    "request_id": request_id,
                }
            print("[WRITE] Authentication successful")

            for block_offset, (label, data) in enumerate(
                [(("member_id", block_4)), ("name", block_5), ("email", block_6)]
            ):
                block_num = base_block + block_offset
                print(f"[WRITE] Writing block {block_num} ({label})...")
                if not nfc.mifare_write_block(block_num, data):
                    return {
                        "success": False,
                        "error": f"Failed to write block {block_num}",
                        "uid": tag["uid_hex"],
                        "request_id": request_id,
                    }

            # Write signature to sector 2
            if signature:
                sig_base = 2 * 4  # Block 8
                if nfc.mifare_authenticate(uid, sig_base, MIFARE_DEFAULT_KEY, "A"):
                    sig_raw = signature.encode("utf-8")[:48]
                    sig_padded = sig_raw + b"\x00" * (48 - len(sig_raw))
                    for i in range(3):
                        nfc.mifare_write_block(sig_base + i, sig_padded[i * 16 : (i + 1) * 16])
                    print("[WRITE] Signature written")

        blink(5, 50)
        return {"success": True, "uid": tag["uid_hex"], "request_id": request_id}

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "uid": tag.get("uid_hex", ""),
            "request_id": request_id,
        }


def main():
    global led_next_state, current_led_state, current_write_job
    print("\n=== Pico W NFC → MQTT ===\n")
    rainbow_cycle(300)  # Regenbogen-Startblinken
    blink(2, 200)  # Zusätzliches Blinken mit Onboard-LED

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
        # Set solid red to indicate NFC hardware error
        set_rgb(255, 0, 0)
        current_led_state = "nfc_error"
    else:
        print("[NFC] Hardware erfolgreich initialisiert")
        blink(1, 500)  # 1× lang = alles OK

    print("\n[MAIN] Hauptgestartet - warte auf Tags…\n")

    # Initialer Heartbeat mit NFC-Status senden
    try:
        mqtt.publish_heartbeat(
            nfc_ok=NFC_STATUS_OK,
            nfc_error=NFC_ERROR_MSG,
            uptime=int(time.time()),
            requires_permission=config.REQUIRES_PERMISSION,
        )
    except MQTTError as e:
        print(f"[MAIN] Initialer Heartbeat fehlgeschlagen: {e}")

    last_uid = None
    last_seen = 0
    ping_timer = time.time()
    heartbeat_timer = time.time()
    read_timer = time.time()
    card_present = False

    # Initialize LED state
    set_led_state(LED_STATE_IDLE)

    # Drive the LED animation from a hardware timer so it keeps running
    # smoothly even while the main loop blocks on NFC reads / sleeps.
    led_timer = Timer(period=LED_TICK_MS, mode=Timer.PERIODIC, callback=_led_timer_cb)

    # --- Hauptschleife ---
    try:
        while True:
            now = time.time()

            # LED update now runs in the timer ISR (_led_timer_cb).

            # 1. MQTT Messages prüfen (non-blocking)
            mqtt.check_messages()

            # 2. MQTT Keepalive (alle 30s)
            if now - ping_timer > 30:
                mqtt.ping()
                ping_timer = now

            # 3. Heartbeat mit NFC-Status (alle 60s)
            if now - heartbeat_timer > 60:
                try:
                    mqtt.publish_heartbeat(
                        nfc_ok=NFC_STATUS_OK,
                        nfc_error=NFC_ERROR_MSG,
                        uptime=int(now),
                        requires_permission=config.REQUIRES_PERMISSION,
                    )
                    print("[HEARTBEAT] Status gesendet")
                except MQTTError as e:
                    print(f"[HEARTBEAT] Fehler: {e}")
                heartbeat_timer = now

            # 4. MQTT Reconnect wenn nötig
            if not mqtt.is_connected():
                print("[MAIN] MQTT getrennt, reconnect …")
                if mqtt.connect():
                    print("[MAIN] MQTT reconnect erfolgreich")
                else:
                    print("[MAIN] MQTT reconnect fehlgeschlagen")
                    time.sleep(5)
                    continue

            # 5. NFC lesen (nur wenn Hardware verfügbar)
            if nfc is not None and (now - read_timer) > (config.READ_INTERVAL_MS / 1000):
                read_timer = now
                tag = None  # Initialize tag to handle exception cases

                # Check for write job timeout
                global current_write_job
                if current_write_job and now > current_write_job["timeout"]:
                    print("[WRITE] Job timeout - clearing")
                    try:
                        mqtt.publish_write_response(
                            {
                                "success": False,
                                "error": "Timeout - no card presented within 30s",
                                "request_id": current_write_job.get("request_id", ""),
                            }
                        )
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
                        # Short timeout keeps the loop responsive for LED updates;
                        # cards usually rest on the scanner long enough to be caught.
                        tag = nfc.read_passive_target(timeout=0.05)

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
                    if config.RFID_READER_TYPE == "pn522" and hasattr(nfc, "reset_rf_field"):
                        nfc.reset_rf_field()
                        time.sleep_ms(50)
                    time.sleep_ms(500)
                    continue

                if tag is None:
                    last_uid = None  # Tag weg → Debounce zurücksetzen
                    card_present = False
                    led_next_state = None
                    if current_led_state == LED_STATE_SCANNING:
                        set_led_state(LED_STATE_IDLE)
                    time.sleep_ms(50)
                    continue

                uid = tag["uid_hex"]
                print(f"[NFC] Tag erkannt: {uid} ({tag['tag_type']})")
                card_present = True

                # Set LED to blue when tag is detected (don't reset timer if already scanning)
                if current_led_state != LED_STATE_SCANNING:
                    set_led_state(LED_STATE_SCANNING)

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

                    # Set LED state based on write result
                    if result.get("success"):
                        set_led_state(LED_STATE_SUCCESS)
                    else:
                        set_led_state(LED_STATE_ERROR)

                    last_uid = uid
                    last_seen = now
                    continue

                # Debounce: gleichen Tag nicht zu oft senden
                # Extended to cover full LED cycle (2s blue + 2s success or 1.2s error)
                led_cycle_time = (LED_SCAN_DISPLAY_MS + (LED_SUCCESS_BLINK_COUNT * 200)) / 1000
                debounce_time = max(config.DEBOUNCE_SAME_TAG_S, led_cycle_time)
                if uid == last_uid and (now - last_seen) < debounce_time:
                    if current_led_state == LED_STATE_SCANNING and led_next_state is None:
                        set_led_state(LED_STATE_IDLE)
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

                    # Defer LED transition — show blue for LED_SCAN_DISPLAY_MS first
                    if card_data:
                        led_next_state = LED_STATE_SUCCESS  # Recognized card - green blink
                    else:
                        led_next_state = LED_STATE_ERROR  # Unrecognized card - red blink

                    last_uid = uid
                    last_seen = now
                except MQTTError as e:
                    print(f"[MAIN] Publish-Fehler: {e}")
                    set_led_state(LED_STATE_ERROR)  # Error - red blink
                except Exception as e:
                    print(f"[MAIN] Unexpected error: {e}")
                    set_led_state(LED_STATE_ERROR)  # Error - red blink

            else:
                # Kurze Pause wenn kein NFC oder Reading Interval
                time.sleep_ms(50)

    except Exception as e:
        print(f"[MAIN] Fehler: {e}")
        blink(5, 100)
    finally:
        # Aufräumen beim Beenden
        led_timer.deinit()
        rgb_off()
        print("[MAIN] Beende...")
        try:
            mqtt.publish_status("offline")
        except MQTTError:
            pass
        mqtt.disconnect()
        print("[MAIN] MQTT disconnected")


# Automatisch starten wenn direkt ausgeführt oder vom Boot geladen
main()
