"""Little-endian fixed-field reader / writer (the games' PacketReader / PacketWriter)."""
import struct


class Reader:
    def __init__(self, data):
        self.data = bytes(data)
        self.pos = 0

    def _take(self, n):
        if self.pos + n > len(self.data):
            raise ValueError("short packet: need %d at %d of %d" % (n, self.pos, len(self.data)))
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def _unpack(self, fmt):
        return struct.unpack("<" + fmt, self._take(struct.calcsize(fmt)))[0]

    def u8(self): return self._unpack("B")
    def i8(self): return self._unpack("b")
    def u16(self): return self._unpack("H")
    def i16(self): return self._unpack("h")
    def u32(self): return self._unpack("I")
    def i32(self): return self._unpack("i")
    def u64(self): return self._unpack("Q")
    def raw(self, n): return self._take(n)

    def str(self, n):
        """n bytes, cut at the first NUL"""
        return self._take(n).split(b"\0", 1)[0]

    def left(self):
        return len(self.data) - self.pos


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def _pack(self, fmt, v):
        self.buf += struct.pack("<" + fmt, v)
        return self

    def u8(self, v): return self._pack("B", v & 0xFF)
    def i8(self, v): return self._pack("b", v)
    def u16(self, v): return self._pack("H", v & 0xFFFF)
    def i16(self, v): return self._pack("h", v)
    def u32(self, v): return self._pack("I", v & 0xFFFFFFFF)
    def i32(self, v): return self._pack("i", v)
    def u64(self, v): return self._pack("Q", v & 0xFFFFFFFFFFFFFFFF)

    def raw(self, b):
        self.buf += bytes(b)
        return self

    def str(self, s, n):
        """fixed n bytes, zero-padded (cut to n)"""
        if isinstance(s, str):
            s = s.encode("latin-1", "replace")
        self.buf += bytes(s[:n]).ljust(n, b"\0")
        return self

    def bytes(self):
        return bytes(self.buf)

    def __len__(self):
        return len(self.buf)
