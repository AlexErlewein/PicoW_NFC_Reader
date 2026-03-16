"""
PN532 NFC Driver für MicroPython (I2C)
Unterstützt: ISO14443A (MIFARE Classic, MIFARE Ultralight, NTAG)
"""

import time

from machine import I2C

# PN532 I2C Adresse (default)
PN532_I2C_ADDRESS = 0x24

# Kommandos
CMD_GETFIRMWAREVERSION = 0x02
CMD_SAMCONFIGURATION = 0x14
CMD_INLISTPASSIVETARGET = 0x4A

# Frame-Bytes
PREAMBLE = 0x00
STARTCODE1 = 0x00
STARTCODE2 = 0xFF
POSTAMBLE = 0x00
HOSTTOPN532 = 0xD4
PN532TOHOST = 0xD5

# Tag-Typen
TAG_TYPES = {
    0x00: "MIFARE Classic 1K",
    0x01: "MIFARE Classic 4K",
    0x02: "MIFARE Ultralight",
    0x03: "MIFARE Classic Mini",
    0x11: "MIFARE Plus S 2K",
    0x13: "MIFARE Plus S 4K",
    0x20: "MIFARE Plus X 2K",
    0x28: "MIFARE Plus X 4K",
    0x30: "NTAG203",
}


class PN532Error(Exception):
    pass


class PN532:
    def __init__(self, i2c: I2C, address: int = PN532_I2C_ADDRESS):
        self._i2c = i2c
        self._addr = address
        self._buf = bytearray(25)

    # ------------------------------------------------------------------
    # Interne Hilfsmethoden
    # ------------------------------------------------------------------

    def _write_frame(self, data: list):
        """Sendet einen kompletten PN532-Frame."""
        length = len(data)
        checksum = sum(data) & 0xFF
        checksum = (~checksum + 1) & 0xFF

        frame = bytearray(8 + length)
        frame[0] = PREAMBLE
        frame[1] = STARTCODE1
        frame[2] = STARTCODE2
        frame[3] = length & 0xFF
        frame[4] = (~length + 1) & 0xFF  # LCS
        for i, b in enumerate(data):
            frame[5 + i] = b
        frame[5 + length] = checksum
        frame[6 + length] = POSTAMBLE

        self._i2c.writeto(self._addr, frame)

    def _read_frame(self, length: int) -> bytearray:
        """Liest einen Frame vom PN532. Gibt nur den Datenteil zurück."""
        # Warten bis Ready-Byte (0x01) gesetzt
        for _ in range(100):
            resp = self._i2c.readfrom(self._addr, 1)
            if resp[0] == 0x01:
                break
            time.sleep_ms(10)
        else:
            raise PN532Error("PN532 antwortet nicht (timeout)")

        # Kompletten Frame lesen (ready + preamble + header + data + checksum + postamble)
        frame = self._i2c.readfrom(self._addr, length + 8)

        # Basis-Validierung
        if frame[1] != PREAMBLE or frame[2] != STARTCODE1 or frame[3] != STARTCODE2:
            raise PN532Error("Ungültiger Frame-Header")

        data_len = frame[4]
        return bytearray(frame[6 : 6 + data_len - 1])  # ohne TFI

    def _send_command(self, cmd: int, params: list | None = None):
        if params is None:
            params = []
        data = [HOSTTOPN532, cmd] + params  # type: ignore[reportArgumentType]
        self._write_frame(data)
        time.sleep_ms(50)

    def _read_response(self, length: int = 10) -> bytearray:
        return self._read_frame(length)

    # ------------------------------------------------------------------
    # Öffentliche API
    # ------------------------------------------------------------------

    def get_firmware_version(self) -> dict:
        """Liest Firmware-Version. Dient auch als Verbindungstest."""
        self._send_command(CMD_GETFIRMWAREVERSION)
        resp = self._read_response(6)
        return {
            "ic": resp[0],
            "ver": resp[1],
            "rev": resp[2],
            "support": resp[3],
        }

    def sam_configuration(self):
        """Konfiguriert den SAM (Security Access Module) – Pflichtaufruf beim Start."""
        # Normal mode, timeout 1s, IRQ aktiv
        self._send_command(CMD_SAMCONFIGURATION, [0x01, 0x14, 0x01])
        time.sleep_ms(20)

    def read_passive_target(self, timeout_ms: int = 1000) -> dict | None:
        """
        Wartet auf einen NFC-Tag im Feld.
        Gibt dict mit uid, uid_hex, tag_type, atqa, sak zurück oder None bei Timeout.
        """
        self._send_command(CMD_INLISTPASSIVETARGET, [0x01, 0x00])  # 1 target, 106kbps ISO14443A

        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            try:
                resp = self._read_response(20)
            except PN532Error:
                time.sleep_ms(50)
                continue

            if len(resp) < 6:
                time.sleep_ms(50)
                continue

            num_targets = resp[0]
            if num_targets == 0:
                return None

            # resp[1] = Tg (target number)
            atqa = (resp[2] << 8) | resp[3]
            sak = resp[4]
            uid_len = resp[5]
            uid = bytes(resp[6 : 6 + uid_len])

            tag_type = TAG_TYPES.get(sak, f"Unbekannt (SAK=0x{sak:02X})")

            return {
                "uid": uid,
                "uid_hex": uid.hex().upper(),
                "uid_dec": int.from_bytes(uid, "big"),
                "tag_type": tag_type,
                "atqa": f"0x{atqa:04X}",
                "sak": f"0x{sak:02X}",
            }

        return None  # Timeout

    def wait_for_tag_removed(self, timeout_ms: int = 3000):
        """Wartet bis der Tag aus dem Feld entfernt wird (Entprellung)."""
        time.sleep_ms(500)
