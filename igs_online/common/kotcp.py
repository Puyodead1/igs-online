"""KoTCP stream framing (IGS Ko library, KoTCP_SendMsg / KoTCP_RecvMsg).

Each frame: u16 len (1..max: We Dance 18000, Percussion Master 3 4096), then
len bytes.  len 0x4BFF alone is a ping, answered 0x4FFF; the games ping after
10 s without data and drop the link after 60 s.
"""
import asyncio
import struct

MAX_FRAME = 18000
PING = 0x4BFF
PONG = 0x4FFF


class FrameError(Exception):
    pass


class KoTCP:
    def __init__(self, reader, writer, idle_timeout=90, max_frame=MAX_FRAME):
        self.reader = reader
        self.writer = writer
        self.idle_timeout = idle_timeout
        self.max_frame = max_frame
        self.peer = writer.get_extra_info("peername")

    async def recv(self):
        """the next frame's bytes; pings are answered here"""
        while True:
            hdr = await asyncio.wait_for(self.reader.readexactly(2), self.idle_timeout)
            n = struct.unpack("<H", hdr)[0]
            if n == PING:
                self.writer.write(struct.pack("<H", PONG))
                continue
            if n == PONG:
                continue
            if n == 0 or n > self.max_frame:
                raise FrameError("bad frame length 0x%x" % n)
            return await asyncio.wait_for(self.reader.readexactly(n), self.idle_timeout)

    def send(self, frame):
        if not 0 < len(frame) <= self.max_frame:
            raise FrameError("frame length %d" % len(frame))
        self.writer.write(struct.pack("<H", len(frame)) + bytes(frame))

    def ping(self):
        self.writer.write(struct.pack("<H", PING))

    async def drain(self):
        await self.writer.drain()

    def close(self):
        try:
            self.writer.close()
        except Exception:
            pass
