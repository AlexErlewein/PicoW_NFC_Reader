"""
RC522 (MFRC522) NFC Driver für MicroPython (SPI)
Original von wendlers/micropython-mfrc522, erweitert für Pico W
"""

from machine import Pin, SPI
from os import uname


class MFRC522:

    OK = 0
    NOTAGERR = 1
    ERR = 2

    REQIDL = 0x26
    REQALL = 0x52
    AUTHENT1A = 0x60
    AUTHENT1B = 0x61

    def __init__(self, sck, mosi, miso, rst, cs):

        self.sck = Pin(sck, Pin.OUT)
        self.mosi = Pin(mosi, Pin.OUT)
        self.miso = Pin(miso)
        self.rst = Pin(rst, Pin.OUT)
        self.cs = Pin(cs, Pin.OUT)

        self.rst.value(0)
        self.cs.value(1)

        board = uname()[0]

        if board == 'WiPy' or board == 'LoPy' or board == 'FiPy':
            self.spi = SPI(0)
            self.spi.init(SPI.MASTER, baudrate=1000000, pins=(self.sck, self.mosi, self.miso))
        elif board == 'esp8266':
            self.spi = SPI(baudrate=100000, polarity=0, phase=0, sck=self.sck, mosi=self.mosi, miso=self.miso)
            self.spi.init()
        elif board == 'rp2' or board == 'Raspberry Pi Pico W':
            self.spi = SPI(0, baudrate=100000, polarity=0, phase=0, sck=self.sck, mosi=self.mosi, miso=self.miso)
        else:
            raise RuntimeError("Unsupported platform")

        self.rst.value(1)
        self.init()

    def _wreg(self, reg, val):

        self.cs.value(0)
        self.spi.write(b'%c' % int(0xff & ((reg << 1) & 0x7e)))
        self.spi.write(b'%c' % int(0xff & val))
        self.cs.value(1)

    def _rreg(self, reg):

        self.cs.value(0)
        self.spi.write(b'%c' % int(0xff & (((reg << 1) & 0x7e) | 0x80)))
        val = self.spi.read(1)
        self.cs.value(1)

        return val[0]

    def _sflags(self, reg, mask):
        self._wreg(reg, self._rreg(reg) | mask)

    def _cflags(self, reg, mask):
        self._wreg(reg, self._rreg(reg) & (~mask))

    def _tocard(self, cmd, send):

        recv = []
        bits = irq_en = wait_irq = n = 0
        stat = self.ERR

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
                stat = self.OK

                if n & irq_en & 0x01:
                    stat = self.NOTAGERR
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
                stat = self.ERR

        return stat, recv, bits

    def _crc(self, data):

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

    def init(self):

        self.reset()
        self._wreg(0x2A, 0x8D)
        self._wreg(0x2B, 0x3E)
        self._wreg(0x2D, 30)
        self._wreg(0x2C, 0)
        self._wreg(0x15, 0x40)
        self._wreg(0x11, 0x3D)
        self.antenna_on()

    def reset(self):
        self._wreg(0x01, 0x0F)

    def antenna_on(self, on=True):

        if on and ~(self._rreg(0x14) & 0x03):
            self._sflags(0x14, 0x03)
        else:
            self._cflags(0x14, 0x03)

    def request(self, mode):

        self._wreg(0x0D, 0x07)
        (stat, recv, bits) = self._tocard(0x0C, [mode])

        if (stat != self.OK) | (bits != 0x10):
            stat = self.ERR

        return stat, bits

    def anticoll(self):

        ser_chk = 0
        ser = [0x93, 0x20]

        self._wreg(0x0D, 0x00)
        (stat, recv, bits) = self._tocard(0x0C, ser)

        if stat == self.OK:
            if len(recv) == 5:
                for i in range(4):
                    ser_chk = ser_chk ^ recv[i]
                if ser_chk != recv[4]:
                    stat = self.ERR
            else:
                stat = self.ERR

        return stat, recv

    def anticoll2(self):
        """Cascade level 2 for 7-byte UIDs (MIFARE Classic 1K/4K)."""
        ser_chk = 0
        ser = [0x95, 0x20]

        self._wreg(0x0D, 0x00)
        (stat, recv, bits) = self._tocard(0x0C, ser)

        if stat == self.OK:
            if len(recv) == 5:
                for i in range(4):
                    ser_chk = ser_chk ^ recv[i]
                if ser_chk != recv[4]:
                    stat = self.ERR
            else:
                stat = self.ERR

        return stat, recv

    def _select_tag_raw(self, anticoll_cmd, ser):
        """Select tag using raw 5-byte anticoll response. Returns (stat, sak)."""
        buf = [anticoll_cmd, 0x70] + list(ser[:5])
        buf += self._crc(buf)
        (stat, recv, bits) = self._tocard(0x0C, buf)
        sak = recv[0] if (stat == self.OK and recv) else 0
        return (self.OK if (stat == self.OK and bits == 0x18) else self.ERR), sak

    def select_tag(self, ser):
        stat, _ = self._select_tag_raw(0x93, ser)
        return stat

    def select_tag2(self, ser):
        """Select tag at cascade level 2."""
        stat, _ = self._select_tag_raw(0x95, ser)
        return stat

    def get_uid(self):
        """Read full UID (supports 4, 7, and 10 byte UIDs via cascade levels)."""
        uid = []

        # Cascade level 1
        self._wreg(0x0D, 0x00)
        (stat, recv) = self.anticoll()
        if stat != self.OK:
            return None

        if recv[0] == 0x88:  # Cascade tag byte — not part of UID
            uid.extend(recv[1:4])  # 3 actual UID bytes from level 1
            self.select_tag(recv[:5])  # Advance card to level 2

            # Cascade level 2
            (stat2, recv2) = self.anticoll2()
            if stat2 != self.OK:
                return None

            if recv2[0] == 0x88:  # Still cascading — 10-byte UID
                uid.extend(recv2[1:4])
                self.select_tag2(recv2[:5])

                # Cascade level 3
                self._wreg(0x0D, 0x00)
                (stat3, recv3, _) = self._tocard(0x0C, [0x97, 0x20])
                if stat3 != self.OK or len(recv3) < 5:
                    return None
                uid.extend(recv3[0:4])
            else:
                uid.extend(recv2[0:4])  # 4 bytes → 7-byte UID total
        else:
            uid.extend(recv[0:4])  # Standard 4-byte UID

        return bytes(uid)

    def get_atqa(self):
        """Read ATQA bytes from card."""
        self._wreg(0x0D, 0x07)
        (stat, recv, bits) = self._tocard(0x0C, [self.REQIDL])
        if stat != self.OK:
            return None
        # ATQA is 2 bytes in recv buffer
        if len(recv) >= 2:
            return (recv[0] << 8) | recv[1]
        # Fallback: use bits value if recv is empty
        return bits

    def get_sak(self):
        """Read SAK byte after selecting tag."""
        uid = self.get_uid()
        if uid is None:
            return None

        # Select tag to get SAK
        if len(uid) <= 4:
            stat = self.select_tag(list(uid))
        else:
            # For multi-level UIDs, select at level 1 first
            stat = self.select_tag(list(uid[:4]))
            if stat != self.OK:
                return None

        if stat != self.OK:
            return None

        # Read SAK from register
        sak = self._rreg(0x05)
        return sak

    def read_tag(self):
        """Read complete tag information including UID, ATQA, SAK with cascade support."""
        # REQA to detect card and read ATQA
        self._wreg(0x0D, 0x07)
        (stat, recv, bits) = self._tocard(0x0C, [self.REQIDL])
        if stat != self.OK:
            return None

        # ATQA: MFRC522 delivers LSByte first
        atqa = (recv[0] | (recv[1] << 8)) if len(recv) >= 2 else bits

        # Level 1 anticoll
        self._wreg(0x0D, 0x00)
        (stat, recv1, _) = self._tocard(0x0C, [0x93, 0x20])
        if stat != self.OK or len(recv1) < 5:
            return None

        uid = []

        if recv1[0] == 0x88:  # 7- or 10-byte UID
            stat, _ = self._select_tag_raw(0x93, recv1[:5])
            if stat != self.OK:
                return None
            uid.extend(recv1[1:4])

            # Level 2 anticoll
            self._wreg(0x0D, 0x00)
            (stat, recv2, _) = self._tocard(0x0C, [0x95, 0x20])
            if stat != self.OK or len(recv2) < 5:
                return None

            if recv2[0] == 0x88:  # 10-byte UID
                stat, _ = self._select_tag_raw(0x95, recv2[:5])
                if stat != self.OK:
                    return None
                uid.extend(recv2[1:4])

                # Level 3 anticoll
                self._wreg(0x0D, 0x00)
                (stat, recv3, _) = self._tocard(0x0C, [0x97, 0x20])
                if stat != self.OK or len(recv3) < 5:
                    return None
                stat, sak = self._select_tag_raw(0x97, recv3[:5])
                if stat != self.OK:
                    return None
                uid.extend(recv3[0:4])
            else:
                stat, sak = self._select_tag_raw(0x95, recv2[:5])
                if stat != self.OK:
                    return None
                uid.extend(recv2[0:4])
        else:
            stat, sak = self._select_tag_raw(0x93, recv1[:5])
            if stat != self.OK:
                return None
            uid.extend(recv1[0:4])

        uid_bytes = bytes(uid)
        tag_type = self._determine_tag_type(sak, atqa)

        return {
            "uid": uid_bytes,
            "uid_hex": uid_bytes.hex().upper(),
            "uid_dec": int.from_bytes(uid_bytes, "big"),
            "atqa": f"0x{atqa:04X}",
            "sak": f"0x{sak:02X}",
            "tag_type": tag_type,
        }

    def _determine_tag_type(self, sak, atqa):
        """Determine tag type from SAK and ATQA bytes."""
        # Based on NXP MIFARE tag identification
        if sak == 0x00:
            if atqa == 0x0044:
                return "MIFARE Classic 1K"
            elif atqa == 0x0042:
                return "MIFARE Classic 4K"
            else:
                return "MIFARE Ultralight/NTAG"
        elif sak == 0x08:
            if atqa == 0x0004:
                return "MIFARE Classic 1K"
            elif atqa == 0x0002:
                return "MIFARE Classic 4K"
            else:
                return "MIFARE Classic"
        elif sak == 0x09:
            return "MIFARE Classic Mini"
        elif sak == 0x18:
            return "MIFARE Classic 4K"
        elif sak == 0x20:
            return "MIFARE DESFire"
        else:
            return f"Unknown (SAK=0x{sak:02X}, ATQA=0x{atqa:04X})"

    def auth(self, mode, addr, sect, ser):
        return self._tocard(0x0E, [mode, addr] + sect + ser[:4])[0]

    def stop_crypto1(self):
        self._cflags(0x08, 0x08)

    def read(self, addr):

        data = [0x30, addr]
        data += self._crc(data)
        (stat, recv, _) = self._tocard(0x0C, data)
        return recv if stat == self.OK else None

    def write(self, addr, data):

        buf = [0xA0, addr]
        buf += self._crc(buf)
        (stat, recv, bits) = self._tocard(0x0C, buf)

        if not (stat == self.OK) or not (bits == 4) or not ((recv[0] & 0x0F) == 0x0A):
            stat = self.ERR
        else:
            buf = []
            for i in range(16):
                buf.append(data[i])
            buf += self._crc(buf)
            (stat, recv, bits) = self._tocard(0x0C, buf)
        return stat

    def mifare_authenticate(self, uid, block, key=b'\xff\xff\xff\xff\xff\xff', key_type="A"):
        """PN522-compatible authenticate wrapper for RC522."""
        mode = self.AUTHENT1A if key_type == "A" else self.AUTHENT1B
        self.select_tag(list(uid))
        stat = self.auth(mode, block, list(key), list(uid))
        return stat == self.OK

    def mifare_read_block(self, block):
        """PN522-compatible read wrapper: read 16 bytes from block/page address."""
        data = self.read(block)
        return bytes(data) if data is not None else None

    def mifare_write_block(self, block, data):
        """PN522-compatible write wrapper for RC522."""
        stat = self.write(block, list(data))
        self.stop_crypto1()
        return stat == self.OK

    def ntag_write_page(self, page, data):
        """Write 4 bytes to an NTAG/Ultralight page using command 0xA2."""
        buf = [0xA2, page] + list(data[:4])
        buf += self._crc(buf)
        (stat, recv, bits) = self._tocard(0x0C, buf)
        return stat == self.OK and bits == 4 and bool(recv) and (recv[0] & 0x0F) == 0x0A

    def read_passive_target(self, timeout=0.5):
        """PN522-compatible wrapper: detect and read tag."""
        return self.read_tag()
