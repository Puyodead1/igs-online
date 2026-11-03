"""The We Dance server against a scripted client: registration, login, player login."""
import asyncio
import os
import struct
import tempfile
import unittest

from igs_online.common.packet import Reader, Writer
from igs_online.wedance import konet, profile
from igs_online.wedance.server import Server

KEYCPU = bytes(range(16))
KEYMAC = bytes.fromhex("020000000001")


class Client:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.key = konet.STATIC_KEY
        self.dec = konet.Decoder()

    def send(self, cmd, mode, data):
        for f in konet.encode(cmd, mode, data, self.key):
            self.w.write(struct.pack("<H", len(f)) + f)

    async def recv(self):
        while True:
            n = struct.unpack("<H", await self.r.readexactly(2))[0]
            msg = self.dec.feed(await self.r.readexactly(n))
            if msg:
                cmd, mode, body = msg
                return cmd, konet.decode_body(mode, body, self.key)


async def session(port, coro):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    try:
        return await coro(Client(r, w))
    finally:
        w.close()


class ServerFlow(unittest.TestCase):
    def test_register_login_player(self):
        async def run():
            path = os.path.join(tempfile.mkdtemp(), "t.db")
            srv = Server(path)
            s = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
            port = s.sockets[0].getsockname()[1]

            async def register(c):
                c.send(0x21, konet.AES_MODE, struct.pack("<HH", 1, 56))
                cmd, ch = await c.recv()
                self.assertEqual((cmd, len(ch)), (0x22, 16))
                mac = konet.login_hmac(ch[:8], KEYCPU, KEYMAC)
                c.send(0x23, konet.ZLIB_AES, mac + KEYCPU + KEYMAC)
                c.key = KEYCPU
                cmd, sid = await c.recv()
                self.assertEqual(cmd, 0x24)
                return struct.unpack("<i", sid)[0]

            sid = await session(port, register)
            self.assertGreater(sid, 0)
            srv.db.create_player(konet.account_value("123456"), konet.account_value("654321"))

            async def login(c):
                c.send(0x11, konet.ZLIB_AES, struct.pack("<I", sid) + bytes(38))
                cmd, d = await c.recv()
                self.assertEqual((cmd, len(d)), (0x12, 25))
                c.send(0x13, konet.AES_MODE, konet.login_hmac(d[:8], KEYCPU, KEYMAC))
                c.key = KEYCPU
                cmd, d = await c.recv()
                self.assertEqual((cmd, d), (0x14, b"\0"))
                cmd, d = await c.recv()
                self.assertEqual((cmd, d), (0x13B, b"\0"))     # song popularity, none yet
                w = Writer().raw(bytes(20)).u64(konet.account_value("123456"))
                c.send(0x211, konet.AES_MODE, w.u64(konet.account_value("000000")).u8(0).bytes())
                cmd, d = await c.recv()
                self.assertEqual((cmd, d), (0x212, b"\xfe"))
                w = Writer().raw(bytes(20)).u64(konet.account_value("123456"))
                c.send(0x211, konet.AES_MODE, w.u64(konet.account_value("654321")).u8(0).bytes())
                cmd, d = await c.recv()
                self.assertEqual(cmd, 0x212)
                p = profile.parse(d)
                self.assertEqual((p["uid"], p["logins"]), (10001, 1))
                c.send(0x222, konet.PLAIN, bytes(6))           # no fighters: one byte, never empty
                cmd, d = await c.recv()
                self.assertEqual((cmd, d), (0x224, b"\0"))

            await session(port, login)
            s.close()
            await s.wait_closed()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
