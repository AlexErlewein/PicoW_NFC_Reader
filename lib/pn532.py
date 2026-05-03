"""
PN532 NFC Driver für MicroPython (I2C)
Basierend auf Adafruit CircuitPython PN532 Library
Unterstützt: ISO14443A (MIFARE Classic, MIFARE Ultralight, NTAG)
"""

import time

from machine import I2C

# PN532 I2C Adresse
PN532_I2C_ADDRESS = 0x24

# Konstanten
_PREAMBLE = 0x00
_STARTCODE1 = 0x00
_STARTCODE2 = 0xFF
_POSTAMBLE = 0x00
_HOSTTOPN532 = 0xD4
_PN532TOHOST = 0xD5

# Kommandos
_COMMAND_GETFIRMWAREVERSION = 0x02
_COMMAND_SAMCONFIGURATION = 0x14
_COMMAND_INLISTPASSIVETARGET = 0x4A
_COMMAND_INDATAEXCHANGE = 0x40

_ACK = b"\x00\x00\xff\x00\xff\x00"

_MIFARE_ISO14443A = 0x00

# MIFARE Classic Kommandos
_MIFARE_CMD_AUTH_A = 0x60
_MIFARE_CMD_AUTH_B = 0x61
_MIFARE_CMD_READ = 0x30
_MIFARE_CMD_WRITE = 0xA0

# Standard MIFARE Key (Factory Default)
MIFARE_DEFAULT_KEY = b'\xff\xff\xff\xff\xff\xff'

# Tag-Typen
TAG_TYPES = {
    0x00: "MIFARE Classic 1K",
    0x01: "MIFARE Classic 4K",
    0x02: "MIFARE Ultralight",
    0x03: "MIFARE Classic Mini",
    0x04: "MIFARE Plus",
    0x08: "MIFARE Classic",
    0x09: "MIFARE Classic Mini",
    0x10: "MIFARE Plus S 2K",
    0x11: "MIFARE Plus S 4K",
    0x18: "MIFARE Classic 4K",
    0x20: "MIFARE Plus X 2K",
    0x28: "MIFARE Plus X 4K",
    0x30: "NTAG203",
}


class PN532Error(Exception):
    pass


class PN532:
    def __init__(self, i2c: I2C, address: int = PN532_I2C_ADDRESS, debug: bool = False):
        self._i2c = i2c
        self._addr = address
        self._debug = debug

    # ------------------------------------------------------------------
    # Low-Level I2C Kommunikation
    # ------------------------------------------------------------------

    def _read_data(self, count: int) -> bytes:
        """Lest 'count' Bytes vom PN532 über I2C."""
        # Auf Ready-Byte warten (0x01)
        for _ in range(100):
            resp = self._i2c.readfrom(self._addr, 1)
            if len(resp) > 0 and resp[0] == 0x01:
                break
            time.sleep_ms(10)
        else:
            raise PN532Error("PN532 nicht bereit (timeout)")

        # Kurze Pause für Datenvorbereitung
        time.sleep_ms(1)

        # Daten lesen (mit dem zusätzlichen Ready-Byte am Anfang!)
        data = self._i2c.readfrom(self._addr, count + 1)

        # Erstes Byte überspringen (es ist wieder das Ready-Byte 0x01)
        return data[1:]

    def _write_data(self, framebytes: bytes) -> None:
        """Schreibt Roh-bytes zum PN532."""
        self._i2c.writeto(self._addr, framebytes)

    def _wait_ready(self, timeout_s: float = 1.0) -> bool:
        """Wartet bis der PN532 bereit ist."""
        start = time.time()
        while (time.time() - start) < timeout_s:
            try:
                resp = self._i2c.readfrom(self._addr, 1)
                if len(resp) > 0 and resp[0] == 0x01:
                    return True
            except:
                pass
            time.sleep_ms(10)
        return False

    # ------------------------------------------------------------------
    # Frame-Handling (wie Adafruit Library)
    # ------------------------------------------------------------------

    def _write_frame(self, data: bytearray) -> None:
        """Schreibt einen Frame zum PN532."""
        length = len(data)
        frame = bytearray(length + 8)
        frame[0] = _PREAMBLE
        frame[1] = _STARTCODE1
        frame[2] = _STARTCODE2
        checksum = sum(frame[0:3])
        frame[3] = length & 0xFF
        frame[4] = (~length + 1) & 0xFF
        frame[5:-2] = data
        checksum += sum(data)
        frame[-2] = ~checksum & 0xFF
        frame[-1] = _POSTAMBLE

        if self._debug:
            print(f"[WRITE] {frame.hex()}")
        self._write_data(bytes(frame))

    def _read_frame(self, length: int) -> bytes:
        """Liest einen Response-Frame vom PN532."""
        response = self._read_data(length + 7)

        if self._debug:
            print(f"[READ] {response.hex()}")

        # Alle 0x00 Bytes am Anfang überspringen
        offset = 0
        while offset < len(response) and response[offset] == 0x00:
            offset += 1

        if offset >= len(response) or response[offset] != 0xFF:
            raise PN532Error("Ungültiges Frame-Format")

        offset += 1
        if offset >= len(response):
            raise PN532Error("Keine Daten im Frame")

        # Länge und Checksumme prüfen
        frame_len = response[offset]
        if (frame_len + response[offset + 1]) & 0xFF != 0:
            raise PN532Error("Frame-Length-Checksum fehlerhaft")

        # Frame-Checksumme prüfen
        checksum = sum(response[offset + 2 : offset + 2 + frame_len + 1]) & 0xFF
        if checksum != 0:
            raise PN532Error(f"Frame-Checksumme fehlerhaft: {checksum}")

        return response[offset + 2 : offset + 2 + frame_len]

    # ------------------------------------------------------------------
    # Command-Handling
    # ------------------------------------------------------------------

    def send_command(self, command: int, params: bytes = b"", timeout: float = 1.0) -> bool:
        """Sendet ein Kommando und wartet auf ACK."""
        # Frame zusammenbauen
        data = bytearray(2 + len(params))
        data[0] = _HOSTTOPN532
        data[1] = command & 0xFF
        for i, val in enumerate(params):
            data[2 + i] = val

        # Frame senden
        try:
            self._write_frame(data)
        except OSError as e:
            raise PN532Error(f"Senden fehlgeschlagen: {e}")

        # Auf Ready warten
        if not self._wait_ready(timeout):
            raise PN532Error("Kein ACK erhalten (timeout)")

        # ACK lesen und verifizieren
        ack = self._read_data(len(_ACK))
        if ack != _ACK:
            raise PN532Error(f"Kein gültiges ACK erhalten: {ack.hex()} (erwartete {_ACK.hex()})")

        return True

    def process_response(self, command: int, response_length: int = 0, timeout: float = 1.0) -> bytes:
        """Verarbeitet die Antwort auf ein Kommando."""
        # Auf Ready für Response warten
        if not self._wait_ready(timeout):
            raise PN532Error("Keine Antwort erhalten (timeout)")

        # Response-Frame lesen
        response = self._read_frame(response_length + 2)

        # Prüfen ob Antwort zum richtigen Kommando
        if response[0] != _PN532TOHOST or response[1] != (command + 1):
            raise PN532Error(f"Unerwartete Antwort: {response.hex()}")

        return response[2:]

    def call_function(
        self, command: int, response_length: int = 0, params: bytes = b"", timeout: float = 1.0
    ) -> bytes:
        """Kombiniert send_command und process_response."""
        self.send_command(command, params=params, timeout=timeout)
        return self.process_response(command, response_length=response_length, timeout=timeout)

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def get_firmware_version(self) -> dict:
        """Gibt die Firmware-Version zurück."""
        response = self.call_function(_COMMAND_GETFIRMWAREVERSION, 4, timeout=0.5)
        return {
            "ic": response[0],
            "ver": response[1],
            "rev": response[2],
            "support": response[3],
        }

    def SAM_configuration(self) -> None:
        """Konfiguriert SAM für MIFARE-Karten."""
        # 0x01 = Normal mode, 0x14 = timeout 50ms * 20 = 1s, 0x01 = IRQ verwenden
        self.call_function(_COMMAND_SAMCONFIGURATION, params=bytes([0x01, 0x14, 0x01]))

    def read_passive_target(self, timeout: float = 1.0) -> dict | None:
        """Wartet auf einen NFC-Tag und gibt die UID zurück."""
        try:
            # InListPassiveTarget senden
            self.send_command(
                _COMMAND_INLISTPASSIVETARGET, params=bytes([0x01, _MIFARE_ISO14443A]), timeout=timeout
            )
        except PN532Error:
            return None

        try:
            response = self.process_response(_COMMAND_INLISTPASSIVETARGET, response_length=64, timeout=timeout)
        except PN532Error:
            return None

        if response is None:
            return None

        # Antwort parsen
        if response[0] != 0x01:
            raise PN532Error("Mehr als eine Karte erkannt!")

        uid_len = response[5]
        if uid_len > 7:
            raise PN532Error(f"UID zu lang: {uid_len} Bytes")

        uid = bytes(response[6 : 6 + uid_len])

        # Phantom-Tags mit leerer UID filtern
        if uid_len == 0 or all(b == 0 for b in uid):
            return None

        # Tag-Informationen extrahieren
        sak = response[4]
        atqa = (response[2] << 8) | response[3]

        # Ungültige ATQA/SAK filtern
        if atqa == 0x00E1 and sak == 0xE8:
            return None

        tag_type = TAG_TYPES.get(sak, f"Unbekannt (SAK=0x{sak:02X})")

        return {
            "uid": uid,
            "uid_hex": uid.hex().upper(),
            "uid_dec": int.from_bytes(uid, "big"),
            "tag_type": tag_type,
            "atqa": f"0x{atqa:04X}",
            "sak": f"0x{sak:02X}",
        }

    # ------------------------------------------------------------------
    # MIFARE Classic Read/Write
    # ------------------------------------------------------------------

    def mifare_authenticate(self, uid: bytes, block: int, key: bytes = MIFARE_DEFAULT_KEY, key_type: str = "A") -> bool:
        """Authenticate with a MIFARE Classic sector.

        Args:
            uid: 4-byte card UID
            block: Block number (0-63 for 1K, 0-255 for 4K)
            key: 6-byte key (default: factory default)
            key_type: "A" or "B" (default: A)

        Returns:
            True if authentication succeeded
        """
        if len(key) != 6:
            raise PN532Error("Key must be 6 bytes")
        if len(uid) < 4:
            raise PN532Error("UID must be at least 4 bytes")

        # Authentication command
        auth_cmd = _MIFARE_CMD_AUTH_A if key_type == "A" else _MIFARE_CMD_AUTH_B

        # Parameters: Tg(0x01), auth_cmd, block, key (6 bytes), UID (4 bytes)
        params = bytes([0x01, auth_cmd, block]) + key + uid[:4]

        try:
            response = self.call_function(_COMMAND_INDATAEXCHANGE, response_length=1, params=params)
            return response[0] == 0x00  # Success
        except PN532Error:
            return False

    def mifare_read_block(self, block: int) -> bytes:
        """Read 16 bytes from a MIFARE Classic block.

        Args:
            block: Block number (must be authenticated first)

        Returns:
            16 bytes of block data
        """
        params = bytes([0x01, _MIFARE_CMD_READ, block])

        try:
            response = self.call_function(_COMMAND_INDATAEXCHANGE, response_length=17, params=params)
            if response[0] != 0x00:
                raise PN532Error(f"Read failed: 0x{response[0]:02X}")
            return response[1:]  # Skip status byte
        except PN532Error as e:
            raise PN532Error(f"Failed to read block {block}: {e}")

    def mifare_write_block(self, block: int, data: bytes) -> bool:
        """Write 16 bytes to a MIFARE Classic block.

        Args:
            block: Block number (must be authenticated first)
            data: Exactly 16 bytes to write

        Returns:
            True if write succeeded
        """
        if len(data) != 16:
            raise PN532Error("Data must be exactly 16 bytes")

        # Do not allow writing to sector trailer blocks (3, 7, 11, 15, ...)
        if block % 4 == 3:
            raise PN532Error(f"Block {block} is a sector trailer - writing not allowed")

        params = bytes([0x01, _MIFARE_CMD_WRITE, block]) + data

        try:
            response = self.call_function(_COMMAND_INDATAEXCHANGE, response_length=1, params=params)
            return response[0] == 0x00  # Success
        except PN532Error as e:
            raise PN532Error(f"Failed to write block {block}: {e}")

    # ------------------------------------------------------------------
    # NTAG / MIFARE Ultralight Read/Write
    # ------------------------------------------------------------------

    def ntag_write_page(self, page: int, data: bytes) -> bool:
        """Write 4 bytes to an NTAG or MIFARE Ultralight page.

        Args:
            page: Page number (user memory starts at page 4 for NTAG213/215/216)
            data: Exactly 4 bytes to write

        Returns:
            True if write succeeded
        """
        if len(data) != 4:
            raise PN532Error("NTAG page data must be exactly 4 bytes")

        params = bytes([0x01, 0xA2, page]) + data  # Tg + NTAG WRITE command

        try:
            response = self.call_function(_COMMAND_INDATAEXCHANGE, response_length=1, params=params)
            return response[0] == 0x00
        except PN532Error as e:
            raise PN532Error(f"Failed to write NTAG page {page}: {e}")
