"""
RC522 (MFRC522) NFC Driver für MicroPython (SPI)
Basierend auf getestetem Treiber von wendlers/micropython-mfrc522
"""

import time

from machine import Pin, SPI

MIFARE_DEFAULT_KEY = b'\xff\xff\xff\xff\xff\xff'


class RC522Error(Exception):
    pass


class RC522:
    def __init__(self, spi: SPI, sda: Pin, rst: Pin, debug: bool = False):
        """Initialisiert RC522 über SPI."""
        self._spi = spi
        self._sda = sda
        self._rst = rst
        self._debug = debug

        self._sda.value(1)
        self._rst.value(0)
        time.sleep_ms(10)
        self._rst.value(1)
        time.sleep_ms(50)

        self._init()

    def _wreg(self, reg: int, val: int) -> None:
        self._sda.value(0)
        self._spi.write(bytes([int(0xff & ((reg << 1) & 0x7e))]))
        self._spi.write(bytes([int(0xff & val)]))
        self._sda.value(1)

    def _rreg(self, reg: int) -> int:
        self._sda.value(0)
        self._spi.write(bytes([int(0xff & (((reg << 1) & 0x7e) | 0x80))]))
        val = self._spi.read(1)
        self._sda.value(1)
        return val[0]

    def _sflags(self, reg: int, mask: int) -> None:
        self._wreg(reg, self._rreg(reg) | mask)

    def _cflags(self, reg: int, mask: int) -> None:
        self._wreg(reg, self._rreg(reg) & (~mask))

    def _tocard(self, cmd: int, send: list) -> tuple:
        recv = []
        bits = irq_en = wait_irq = n = 0
        stat = 2  # ERR

        if cmd == 0x0E:
            irq_en = 0x12
            wait_irq = 0x10
        elif cmd == 0x0C:
            irq_en = 0x77
            wait_irq = 0x30

        self._wreg(0x02, irq_en | 0x80)
        self._cflags(0x04, 0x80)
        self._sflags(0x0A, 0x80)
        self._wreg(0x01, 0x00)

        for c in send:
            self._wreg(0x09, c)
        self._wreg(0x01, cmd)

        if cmd == 0x0C:
            self._sflags(0x0D, 0x80)

        i = 2000
        while True:
            n = self._rreg(0x04)
            i -= 1
            if ~((i != 0) and ~(n & 0x01) and ~(n & wait_irq)):
                break

        self._cflags(0x0D, 0x80)

        if i:
            if (self._rreg(0x06) & 0x1B) == 0x00:
                stat = 0  # OK

                if n & irq_en & 0x01:
                    stat = 1  # NOTAGERR
                elif cmd == 0x0C:
                    n = self._rreg(0x0A)
                    lbits = self._rreg(0x0C) & 0x07
                    if lbits != 0:
                        bits = (n - 1) * 8 + lbits
                    else:
                        bits = n * 8

                    if n == 0:
                        n = 1
                    elif n > 16:
                        n = 16

                    for _ in range(n):
                        recv.append(self._rreg(0x09))
            else:
                stat = 2  # ERR

        return stat, recv, bits

    def _crc(self, data: list) -> list:
        self._cflags(0x05, 0x04)
        self._sflags(0x0A, 0x80)

        for c in data:
            self._wreg(0x09, c)

        self._wreg(0x01, 0x03)

        i = 0xFF
        while True:
            n = self._rreg(0x05)
            i -= 1
            if not ((i != 0) and not (n & 0x04)):
                break

        return [self._rreg(0x22), self._rreg(0x21)]

    def _init(self) -> None:
        self._wreg(0x01, 0x0F)  # Soft reset
        self._wreg(0x2A, 0x8D)
        self._wreg(0x2B, 0x3E)
        self._wreg(0x2D, 30)
        self._wreg(0x2C, 0)
        self._wreg(0x15, 0x40)
        self._wreg(0x11, 0x3D)
        self.antenna_on()

    def antenna_on(self, on: bool = True) -> None:
        if on and ~(self._rreg(0x14) & 0x03):
            self._sflags(0x14, 0x03)
        else:
            self._cflags(0x14, 0x03)

    def get_firmware_version(self) -> dict:
        version = self._rreg(0x37)
        return {
            "ic": 0x88,
            "ver": (version >> 4) & 0x0F,
            "rev": version & 0x0F,
            "support": 0x00,
        }

    def read_passive_target(self, timeout: float = 1.0) -> dict | None:
        """Liest NFC-Tag."""
        # Request
        self._wreg(0x0D, 0x07)
        (stat, recv, bits) = self._tocard(0x0C, [0x26])

        if (stat != 0) or (bits != 0x10):
            return None

        # Anti-Collision
        ser_chk = 0
        ser = [0x93, 0x20]

        self._wreg(0x0D, 0x00)
        (stat, recv, bits) = self._tocard(0x0C, ser)

        if stat == 0 and len(recv) == 5:
            for i in range(4):
                ser_chk = ser_chk ^ recv[i]
            if ser_chk != recv[4]:
                return None
        else:
            return None

        uid = recv[:4]

        # Select
        buf = [0x93, 0x70] + uid[:5]
        buf += self._crc(buf)
        (stat, recv, bits) = self._tocard(0x0C, buf)

        if not (stat == 0) or not (bits == 0x18):
            return None

        return {
            "uid": bytes(uid),
            "uid_hex": bytes(uid).hex().upper(),
            "uid_dec": int.from_bytes(bytes(uid), "big"),
            "tag_type": "MIFARE Classic",
            "atqa": "0x0400",
            "sak": "0x08",
        }

    def mifare_authenticate(self, uid: bytes, block: int, key: bytes = MIFARE_DEFAULT_KEY, key_type: str = "A") -> bool:
        """Authenticate mit MIFARE Classic."""
        if len(key) != 6:
            raise RC522Error("Key muss 6 Bytes haben")
        if len(uid) < 4:
            raise RC522Error("UID muss mindestens 4 Bytes haben")

        auth_cmd = 0x60 if key_type == "A" else 0x61
        sector = block // 4
        trailer_block = sector * 4 + 3

        auth_data = [auth_cmd, trailer_block] + list(key) + list(uid[:4])
        return self._tocard(0x0E, auth_data)[0] == 0

    def mifare_read_block(self, block: int) -> bytes:
        """Liest MIFARE Classic Block."""
        data = [0x30, block]
        data += self._crc(data)
        (stat, recv, _) = self._tocard(0x0C, data)

        if stat == 0:
            return bytes(recv[:16])
        raise RC522Error(f"Lesen von Block {block} fehlgeschlagen")

    def mifare_write_block(self, block: int, data: bytes) -> bool:
        """Schreibt MIFARE Classic Block."""
        if len(data) != 16:
            raise RC522Error("Daten müssen exakt 16 Bytes haben")
        if block % 4 == 3:
            raise RC522Error(f"Block {block} ist Sektor-Trailer")

        buf = [0xA0, block]
        buf += self._crc(buf)
        (stat, recv, bits) = self._tocard(0x0C, buf)

        if not (stat == 0) or not (bits == 4) or not ((recv[0] & 0x0F) == 0x0A):
            return False
        else:
            buf = []
            for i in range(16):
                buf.append(data[i])
            buf += self._crc(buf)
            (stat, recv, bits) = self._tocard(0x0C, buf)
            return stat == 0
