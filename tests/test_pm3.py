"""The Percussion Master 3 server against a scripted cabinet: messages,
registration, login, sign-up, member login, saves, rankings, boards, dragons."""
import asyncio
import os
import struct
import tempfile
import unittest

from igs_online.pm3 import message, profile
from igs_online.pm3.events import Events, BOARD, TICKER
from igs_online.pm3.server import Server, Config

CPUID = bytes(range(16))
MAC = bytes(6)


def fixed(s, n):
    return s.encode().ljust(n, b"\0")


class Cabinet:
    def __init__(self, reader, writer):
        self.r, self.w = reader, writer

    def send(self, cmd, data=b""):
        f = message.encode(cmd, data)
        self.w.write(struct.pack("<H", len(f)) + f)

    async def recv(self):
        while True:
            n = struct.unpack("<H", await self.r.readexactly(2))[0]
            if n in (0x4BFF, 0x4FFF):
                continue
            return message.decode(await self.r.readexactly(n))

    async def expect(self, cmd):
        got, data = await asyncio.wait_for(self.recv(), 5)
        assert got == cmd, "0x%03X, wanted 0x%03X" % (got, cmd)
        return data

    async def handshake(self, first, sid=0):
        self.send(first, (struct.pack("<I", sid) if first == 0x121 else b"") + struct.pack("<4H", 1, 7, 0, 3))
        a, b = struct.unpack("<II", await self.expect(first + 1))
        self.send(first + 2, message.challenge_reply(a, b) + CPUID + MAC)


class Messages(unittest.TestCase):
    def test_frame(self):
        f = message.encode(0x211, b"\0123456")
        self.assertEqual(message.decode(f), (0x211, b"\0123456"))
        bad = bytearray(f)
        bad[-1] ^= 1
        with self.assertRaises(message.MessageError):
            message.decode(bytes(bad))

    def test_challenge(self):
        self.assertEqual(message.challenge_reply(0xF0F0F0F0, 0x0FF00FF0), b"00F000F0FFF0FFF0")

    def test_events(self):
        ev = Events()
        b = ev.boards([13, 14])
        self.assertEqual(len(b), 2 * BOARD)
        self.assertEqual((b[4], b[BOARD + 4]), (13, 14))
        self.assertEqual(b[436], ev.contest_song()[0])
        self.assertIn(str(ev.contest_song()[0]).encode(), b[14:270])
        self.assertEqual(len(ev.tickers([13, 14])), 4 * TICKER)
        n = ev.notice("ab")
        self.assertEqual(n, b"\x02ab")
        self.assertEqual(len(ev.hot_songs([5, 6])), 257)

    def test_profile(self):
        b = profile.new(7, b"Drummer", 2)
        self.assertEqual(len(b), profile.SIZE)
        self.assertEqual((profile.get(b, "member_id"), profile.get(b, "role"), b[25]), (7, 2, 1))
        self.assertEqual(profile.titles(b)[0], -1)
        b = profile.grant_title(b, 25, 1)
        b = profile.grant_title(b, 25, 0)          # the better one stays
        self.assertEqual(profile.titles(b)[25], 1)


class Flow(unittest.TestCase):
    def test_cabinet(self):
        async def run():
            path = os.path.join(tempfile.mkdtemp(), "t.db")
            srv = Server(path, Config(Events()))
            srv.db.set_setting("active", [11, 14])
            s = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
            port = s.sockets[0].getsockname()[1]
            r, w = await asyncio.open_connection("127.0.0.1", port)
            c = Cabinet(r, w)

            # registration, then login (NetManager::NetProcess states 5..7)
            await c.handshake(0x111)
            sid = struct.unpack("<i", await c.expect(0x114))[0]
            self.assertGreater(sid, 0)
            await c.handshake(0x121, sid)
            self.assertEqual(await c.expect(0x124), b"\0")
            self.assertEqual(len(await c.expect(0x134)), 8)
            c.send(0x411)
            hot = await c.expect(0x412)
            self.assertGreater(hot[0], 0)
            c.send(0x413)
            self.assertGreater(len(await c.expect(0x416)), 1)

            # sign-up: ID free? -> password, nickname, role -> profile
            c.send(0x211, b"\0" + fixed("1234", 16))
            self.assertEqual(await c.expect(0x212), b"\0\0")
            c.send(0x213, b"\0" + fixed("5678", 16) + fixed("Kuta", 16) + b"\1")
            prof = await c.expect(0x21B)
            self.assertEqual((len(prof), profile.name(prof), prof[24]), (profile.SIZE, b"Kuta", 1))
            mid = profile.get(prof, "member_id")
            # the same ID again: taken
            c.send(0x211, b"\1" + fixed("1234", 16))
            self.assertEqual(await c.expect(0x212), b"\1\xfe")

            # player 2: wrong password, unknown ID, then a member made from the CLI side
            srv.db.create_member("4321", "8765", b"Qlin", 1)
            c.send(0x215, b"\1" + fixed("4321", 16) + fixed("0000", 16))
            self.assertEqual(await c.expect(0x216), b"\1\xfc")
            c.send(0x215, b"\1" + fixed("9999", 16) + fixed("0000", 16))
            self.assertEqual(await c.expect(0x216), b"\1\xfe")
            c.send(0x215, b"\1" + fixed("1234", 16) + fixed("5678", 16))       # already playing as player 1
            self.assertEqual(await c.expect(0x216), b"\1\xfa")
            c.send(0x215, b"\1" + fixed("4321", 16) + fixed("8765", 16))
            prof2 = await c.expect(0x21B)
            self.assertEqual((prof2[0], profile.name(prof2)), (1, b"Qlin"))

            # both save after a versus game
            for p, b in ((0, prof), (1, prof2)):
                b = bytearray(b)
                b[0], b[25] = p, 5
                profile.put(b, "exp", 123 + p)
                c.send(0x21B, bytes(b))
                log = bytearray(144)
                log[0], log[1], log[13] = p, 2, 0
                struct.pack_into("<BBxxIH", log, 16, 1, 23, 900000 + p, 300)
                struct.pack_into("<IH", log, 136, 900000 + p, 300)
                c.send(0x231, bytes(log))
            # rankings: kind 0 (normal, difficulty 1), then the place and the lists
            for p, total in ((0, 800000), (1, 900000)):
                up = bytearray(20)
                struct.pack_into("<BBBB", up, 0, p, 0, 1, 0)
                struct.pack_into("<IHB", up, 12, total, 250, 3)
                c.send(0x232, bytes(up))
                ack = await c.expect(0x233)
                self.assertEqual(ack[:4], bytes((p, 0, 0, 0)))
            c.send(0x222, b"\0\1")
            c.send(0x234, b"\0\0")
            self.assertEqual(await c.expect(0x235), struct.pack("<BBH", 0, 0, 2))
            c.send(0x291, struct.pack("<BxH", 1, 0))
            page = await c.expect(0x292)
            self.assertEqual(len(page), 4 + 5 * 68)
            first = page[4:4 + 68]
            self.assertEqual((first[36:40], struct.unpack_from("<I", first, 56)[0]), (b"Qlin", 900000))
            c.send(0x251, struct.pack("<BxHH", 1, 0, 50))
            names = await c.expect(0x252)
            self.assertEqual(struct.unpack_from("<H", names, 4)[0], 2)
            self.assertEqual(names[6:10], b"Qlin")
            c.send(0x241, struct.pack("<BxH", 1, 0))                  # the old 60-byte page
            self.assertEqual(len(await c.expect(0x242)), 4 + 5 * 60)

            # boards, ticker
            c.send(0x271)
            b = await c.expect(0x272)
            self.assertEqual((b[4], b[BOARD + 4]), (11, 14))
            c.send(0x273)
            self.assertEqual(len(await c.expect(0x274)), 4 * TICKER)

            # a dragon in song select, hit until caught
            c.send(0x283, b"\0\0")
            d = await c.expect(0x284)
            self.assertEqual(struct.unpack_from("<I", d, 4)[0], 1)
            hp = struct.unpack_from("<I", d, 12)[0]
            for _ in range(200):
                c.send(0x283, b"\0\0")
                d = await c.expect(0x284)
                if not struct.unpack_from("<I", d, 4)[0]:
                    break
                c.send(0x285, struct.pack("<BBxxI", 0, 0, 40000))
                c.send(0x281, b"\0")
                lst = await c.expect(0x282)
                if 8 in [lst[8 + 12 * i] for i in range(50)]:
                    break
            self.assertGreater(hp, 0)
            self.assertIn(8, [lst[8 + 12 * i] for i in range(50)])

            # logout; the saved profiles
            c.send(0x218, b"\0")
            c.send(0x218, b"\1")
            await asyncio.sleep(0.2)
            m = srv.db.member(mid)
            self.assertEqual((m["profile"][25], profile.get(m["profile"], "exp")), (5, 123))
            self.assertEqual(profile.get(srv.db.member_by_login("4321")["profile"], "exp"), 124)
            self.assertEqual(len(srv.db.logs(mid)), 1)
            w.close()
            s.close()
            await s.wait_closed()
        asyncio.run(run())


class PatchServer(unittest.TestCase):
    def test_no_update(self):
        async def run():
            srv = Server(os.path.join(tempfile.mkdtemp(), "p.db"), Config(Events()))
            s = await asyncio.start_server(srv.handle_patch, "127.0.0.1", 0)
            r, w = await asyncio.open_connection("127.0.0.1", s.sockets[0].getsockname()[1])
            c = Cabinet(r, w)
            await c.handshake(0x311)
            self.assertEqual(await c.expect(0x331), struct.pack("<HH", 0, 0))
            w.close()
            s.close()
            await s.wait_closed()
        asyncio.run(run())


class Contests(unittest.TestCase):
    def test_combo_contest(self):
        async def run():
            srv = Server(os.path.join(tempfile.mkdtemp(), "c.db"), Config(Events()))
            srv.db.set_setting("active", [12])
            srv.db.create_member("111111", "2222", b"Dora", 3)
            s = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
            r, w = await asyncio.open_connection("127.0.0.1", s.sockets[0].getsockname()[1])
            c = Cabinet(r, w)
            await c.handshake(0x111)
            sid = struct.unpack("<i", await c.expect(0x114))[0]
            await c.handshake(0x121, sid)
            await c.expect(0x124)
            await c.expect(0x134)
            c.send(0x215, b"\0" + fixed("111111", 16) + fixed("2222", 16))
            await c.expect(0x21B)
            up = bytearray(20)
            struct.pack_into("<BB", up, 0, 0, 4)
            struct.pack_into("<IHB", up, 12, 5000, 321, 1)
            c.send(0x232, bytes(up))
            self.assertEqual(await c.expect(0x233), bytes((0, 4)) + bytes(12))      # no best before
            c.send(0x234, b"\0\4")
            self.assertEqual(await c.expect(0x235), struct.pack("<BBH", 0, 4, 1))
            c.send(0x299, struct.pack("<H", 0))
            page = await c.expect(0x29A)
            self.assertEqual(struct.unpack_from("<H", page, 4 + 60)[0], 321)     # the combo shown
            w.close()
            s.close()
            await s.wait_closed()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

