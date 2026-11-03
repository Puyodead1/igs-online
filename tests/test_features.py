"""Market, lucky bags, missions, groups, world events, transfer, bookkeeping,
rankings: units and a scripted cabinet against the server."""
import asyncio
import datetime
import os
import random
import struct
import tempfile
import unittest

from igs_online.common.packet import Reader, Writer
from igs_online.wedance import konet, luckybag, market, missions, playlog, profile
from igs_online.wedance.server import Server
from igs_online.wedance.world import World

from test_server import Client, KEYCPU, KEYMAC, session

# a member's play log as a cabinet sent it (mission credit, group 1)
PLAY_LOG = bytes.fromhex(
    "11270000280700c910e96a00000000000000000000017f11e96a0100001600302c302c302c312c372c302c302c35382c3235"
    "372c30125b302c54303032385d5b322c54303032385d0f5b322c302c302c30303030302c305d000005050505050500ca00be"
    "00be00bc002101210000000000000001000000000000000000000000000000000000000000000000010000000000d5990000"
    "00000f00000002000000000000000000000000000000000000000000000000000072000000000100000000000100")


def tmpdb():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def trade(op, weticket, crystal, code, extra=b""):
    w = Writer().u8(op).i32(weticket).i32(crystal)
    c = code.encode()
    w.i8(-len(c) if op == 8 else len(c)).raw(c)
    return w.raw(extra).bytes()


def block(text):
    b = text.encode()
    return Writer().u8(len(profile.entries(text))).u16(len(b)).raw(b).bytes()


class ProfileBlocks(unittest.TestCase):
    def test_group_and_mission_blocks(self):
        p = profile.new_profile(10001)
        base = len(profile.build(p))
        p.update(group=3, group_block={"name": b"STARS", "level": 2, "members": 2, "max_members": 30},
                 mission=1, mission_block={"song": 58, "cond_a": 3, "target_a": 1})
        data = profile.build(p)
        self.assertEqual(len(data), base + 68 + 36)
        q = profile.parse(data)
        self.assertEqual((q["group"], q["group_block"]["name"], q["group_block"]["level"]), (3, b"STARS".hex(), 2))
        self.assertEqual((q["mission"], q["mission_block"]["song"], q["mission_block"]["target_a"]), (1, 58, 1))

    def test_old_field_names(self):
        p = profile.upgrade({"v87903B8": 500, "v8790584": 7})
        self.assertEqual((p["crystal"], p["vip_until"]), (500, 7))

    def test_pet(self):
        p = profile.new_profile(10001)
        profile.apply_pet(p, bytes([1, 2]) + struct.pack("<H", 1234))
        self.assertEqual((p["pet_current"], p["pet1"]), (2, 1234))


class LuckyBags(unittest.TestCase):
    def test_draws_decode(self):
        bag = luckybag.LuckyBag(rng=random.Random(1))
        kinds = set()
        for _ in range(300):
            d = luckybag.decode(bag.draw())
            kinds.add(d["kind"])
            if d["kind"] == "cloth":
                self.assertIn(d["part"], (0, 1, 3, 4, 6))
                self.assertIn(sum(d["attributes"]), (1, 2))
                self.assertTrue(d["uses"] == 0 or 5 <= d["uses"] <= 20)
        self.assertEqual(kinds, {"song", "cloth", "box"})        # crystals: weight 0 in the game's table

    def test_full_wardrobe_no_box(self):
        bag = luckybag.LuckyBag(rng=random.Random(2))
        self.assertFalse(any(bag.draw(luckybag_cap=30).startswith("2,") for _ in range(200)))

    def test_owned_week_song_skipped(self):
        bag = luckybag.LuckyBag(rng=random.Random(3))
        today = datetime.date(2026, 11, 2)
        week = bag.week_song(today)["id"]
        songs = [bag.song(1 | 2, today) for _ in range(300)]
        self.assertNotIn(week, songs)


class Market(unittest.TestCase):
    def setUp(self):
        self.cat = market.Catalogue()
        self.p = profile.new_profile(10001)
        self.p.update(crystal=1000, weticket=10)

    def test_catalogue_from_game(self):
        self.assertEqual(self.cat.price("L0145"), ("crystal", 100))
        self.assertEqual(self.cat.price("T0026"), ("weticket", 1))
        self.assertEqual(self.cat.price("W0001"), ("weticket", 5))
        self.assertEqual(self.cat.price("A0001", vip=True), ("crystal", 80))

    def test_talk(self):
        t = market.parse(trade(2, 10, 900, "L0145", block("[1][2][3][145]")))
        self.assertEqual(market.check(self.cat, self.p, t)[0], None)
        market.apply(self.p, t)
        self.assertEqual((self.p["crystal"], self.p["blocks"]["talks"]), (900, "[1][2][3][145]"))

    def test_skin_face_and_song(self):
        market.apply(self.p, market.parse(trade(1, 10, 850, "N0031", bytes([2, 31]))))
        self.assertEqual((self.p["skin"], self.p["face"]), (2, 31))
        market.apply(self.p, market.parse(trade(14, 5, 850, "W0001", struct.pack("<H", 58))))
        self.assertIn(58, self.p["songs"])

    def test_clothes(self):
        blocks = b"".join(block("[%d,00000,0]" % i) for i in (5, 6, 7, 8, 9))
        market.apply(self.p, market.parse(trade(4, 10, 0, "V0003", blocks)))
        self.assertEqual(self.p["blocks"]["feet"], "[8,00000,0]")

    def test_refusals(self):
        self.assertTrue(market.check(self.cat, self.p, market.parse(trade(2, 10, -1, "L0145", block(""))))[0])
        self.assertTrue(market.check(self.cat, self.p, market.parse(trade(2, 10, 0, "XXXX", block(""))))[0])


class MissionSet(unittest.TestCase):
    def test_assign_progress_pass(self):
        m = missions.Missions()
        p = profile.new_profile(10001)
        mid, b = m.block(p, now=1000)
        self.assertEqual((mid, b["song"], b["start"]), (1, 58, 1000))
        self.assertTrue(m.valid(p, 1, now=2000))
        m.result(p, 1, 0, 58, 0, 0)
        m.result(p, 1, 1, 58, 1, 0)
        self.assertIn(58, p["songs"])
        self.assertEqual(m.block(p, now=3000)[0], 2)
        self.assertFalse(m.valid(p, 2, now=3000 + 31 * 86400))


class WorldEvents(unittest.TestCase):
    def test_seed_and_exp_windows(self):
        w = World()
        w.cfg["exp"] = {"base": 100, "windows": [{"days": [5], "from": "18:00", "to": "20:00", "rate": 300}]}
        self.assertEqual(w.activity_rate(datetime.datetime(2026, 11, 7, 19, 0)), 300)      # a Saturday
        self.assertEqual(w.activity_rate(datetime.datetime(2026, 11, 7, 21, 0)), 100)
        w.cfg["world_seed"] = 1234
        self.assertEqual(w.seed(), 1234)
        w.cfg["world_seed"] = "weekly"
        self.assertEqual(w.seed(datetime.datetime(2026, 11, 2)), w.seed(datetime.datetime(2026, 11, 8)))


class PlayLog(unittest.TestCase):
    def test_parse_and_group_counter(self):
        log = playlog.parse(PLAY_LOG)
        self.assertEqual((log["uid"], log["mode"], log["group"], log["weticket"]), (10001, 7, 1, 114))
        self.assertEqual(log["props"], "[0,T0028][2,T0028]")
        self.assertIsNone(playlog.group_counter(log))                    # missions do not count
        self.assertEqual(playlog.group_counter(dict(log, mode=0, won=1)), "single_won")
        self.assertEqual(playlog.group_counter(dict(log, mode=2, fighter=10002, won=0)), "fight_lost")


class Groups(unittest.TestCase):
    def test_block(self):
        srv = Server(tmpdb())
        a = srv.db.create_player(1, 1, b"LEAD")
        b = srv.db.create_player(2, 2, b"TWO")
        gid = srv.db.create_group(b"STARS", a["uid"], 6, 10)
        srv.db.join_group(b["uid"], gid)
        for _ in range(45):
            srv.db.count_group(gid, "single_won")
        g = srv.group_block(gid)
        self.assertEqual((g["members"], g["max_members"], g["level"], g["song"], g["leader"]), (2, 10, 3, 6, b"LEAD"))
        srv.db.leave_group(a["uid"])
        self.assertEqual(srv.db.group(gid)["leader"], b["uid"])


class Flow(unittest.TestCase):
    """the server against a scripted cabinet"""

    def test_features(self):
        async def run():
            srv = Server(tmpdb())
            sid = srv.db.register_store(KEYCPU, KEYMAC)
            p = srv.db.create_player(konet.account_value("123456"), konet.account_value("654321"), b"DANCER")
            p.update(crystal=1000, weticket=20, fight_score=500)
            srv.db.save(p)
            q = srv.db.create_player(konet.account_value("223344"), konet.account_value("556677"), b"OTHER")
            q.update(weticket=7, fight_score=900)
            srv.db.save(q)
            gid = srv.db.create_group(b"STARS", p["uid"])
            srv.db.set_pending(p["uid"], "transfer")
            s = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
            port = s.sockets[0].getsockname()[1]

            async def cab(c):
                login = (struct.pack("<I", sid) + b"\x05" + b"FIRST".ljust(10, b"\0") + b"SECOND".ljust(16, b"\0")
                         + struct.pack("<HHHBBHB", 1, 1, 56, 1, 1, 0, 0))
                c.send(0x11, konet.ZLIB_AES, login)
                cmd, d = await c.recv()
                feature, seed, level = struct.unpack_from("<HHB", d, 18)
                self.assertEqual((cmd, level), (0x12, 1))
                c.send(0x13, konet.AES_MODE, konet.login_hmac(d[:8], KEYCPU, KEYMAC))
                c.key = KEYCPU
                self.assertEqual((await c.recv())[0], 0x14)
                self.assertEqual((await c.recv())[0], 0x13B)
                self.assertEqual(srv.db.store_info(sid)["name1"], b"FIRST")

                w = Writer().raw(bytes(20)).u64(konet.account_value("123456")).u64(konet.account_value("654321")).u8(0)
                c.send(0x211, konet.AES_MODE, w.bytes())
                cmd, d = await c.recv()
                pr = profile.parse(d)
                self.assertEqual((pr["group"], pr["mission"], pr["world_rank"]), (gid, 1, 2))
                self.assertTrue(pr["user_feature"] & profile.UF_TRANSFER)

                c.send(0x273, konet.PLAIN, Writer().u8(0).u16(0).bytes())          # lucky bag
                cmd, d = await c.recv()
                r = Reader(d)
                self.assertEqual((cmd, r.i8()), (0x274, 0))
                luckybag.decode(r.raw(r.u16()).decode())

                c.send(0x271, konet.AES_MODE, trade(2, 20, 900, "L0145", block("[1][145]")))
                self.assertEqual(await c.recv(), (0x272, b"\0"))
                c.send(0x271, konet.AES_MODE, trade(2, 20, 900, "NOPE", block("")))
                self.assertEqual((await c.recv())[1], b"\xfe")

                c.send(0x161, konet.AES_MODE, Writer().u8(0).i32(1).bytes())       # mission still open?
                cmd, d = await c.recv()
                self.assertEqual((cmd, d[2]), (0x162, 1))
                c.send(0x26E, konet.PLAIN, Writer().i32(1).u8(1).u16(58).i32(1).i32(0).bytes())

                c.send(0x261, konet.AES_MODE, Writer().u16(6).i32(700000).u8(2).u8(0).u8(0).u8(0).bytes())
                cmd, d = await c.recv()
                self.assertEqual((cmd, d[6]), (0x262, 1))
                c.send(0x267, konet.PLAIN, struct.pack("<H", 6))
                c.send(0x26A, konet.AES_MODE, Writer().i32(1500000).i32(321).u8(0).bytes())
                cmd, d = await c.recv()
                self.assertEqual((cmd, struct.unpack("<iHH", d)), (0x26C, (p["uid"], 1, 1)))
                c.send(0x269, konet.ZLIB_AES, PLAY_LOG)

                c.send(0x131, konet.PLAIN, b"\0")
                cmd, d = await c.recv()
                self.assertEqual((cmd, (len(d) - 13) // 52), (0x132, 2))
                self.assertEqual(d[13 + 5:13 + 10], b"OTHER")
                c.send(0x137, konet.PLAIN, struct.pack("<H", 6))
                cmd, d = await c.recv()
                self.assertEqual((cmd, d[7:13], struct.unpack_from("<i", d, 21)[0]), (0x138, b"DANCER", 700000))
                c.send(0x134, konet.PLAIN, b"\0")
                cmd, d = await c.recv()
                self.assertEqual((cmd, d[6 + 1:6 + 6], struct.unpack_from("<H", d, 6 + 27)[0]), (0x135, b"FIRST", 1))

                c.send(0x151, konet.PLAIN, bytes([1, 2]))
                c.send(0x153, konet.ZLIB_AES, Writer().u16(4).raw(b"shop").bytes())
                c.send(0x172, konet.ZLIB_AES, bytes(1012))
                c.send(0x16E, konet.PLAIN, b"\0")
                self.assertEqual(await c.recv(), (0x16D, b"\0"))

                # transfer to a new account number
                c.send(0x21C, 2, Writer().u8(0).u64(konet.account_value("777777")).u64(konet.account_value("888888")).bytes())
                cmd, d = await c.recv()
                self.assertEqual((cmd, len(d)), (0x21D, 26))
                self.assertFalse(struct.unpack_from("<H", d, 24)[0] & profile.UF_TRANSFER)
                # and bind the other account into it
                c.send(0x21C, 2, Writer().u8(1).u64(konet.account_value("223344")).u64(konet.account_value("000000")).bytes())
                self.assertEqual(await c.recv(), (0x21D, b"\xfe"))
                c.send(0x21C, 2, Writer().u8(1).u64(konet.account_value("223344")).u64(konet.account_value("556677")).bytes())
                cmd, d = await c.recv()
                self.assertEqual(struct.unpack_from("<i", d, 20)[0], 20 + 7)

            await session(port, cab)
            s.close()
            await s.wait_closed()
            return srv

        srv = asyncio.run(run())
        p, pw = srv.db.player_by_account(konet.account_value("223344"))
        self.assertEqual((p["uid"], pw), (10001, konet.account_value("556677")))
        self.assertIn(58, p["songs"])
        self.assertEqual(p["blocks"]["talks"], "[1][145]")
        self.assertEqual(len(p["lucky_bags"]), 1)
        self.assertEqual(srv.db.store_info(1)["coin"], {"coins": 1, "credits": 2})
        self.assertEqual(srv.db.store_info(1)["info"], "shop")
        self.assertEqual(len(srv.db.book(1, 0x172)), 1)
        self.assertEqual(srv.db.popular()[0]["song"], 6)


class Coop(unittest.TestCase):
    """two cabinets: contest, challenge, accept, co-op results -> pair board, co-op rank"""

    def test_pair(self):
        async def run():
            srv = Server(tmpdb())
            sid = srv.db.register_store(KEYCPU, KEYMAC)
            for acc, name in (("111111", b"ONE"), ("222222", b"TWO")):
                srv.db.create_player(konet.account_value(acc), konet.account_value(acc), name)
            s = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
            port = s.sockets[0].getsockname()[1]
            clients = []
            for acc in ("111111", "222222"):
                r, w = await asyncio.open_connection("127.0.0.1", port)
                c = Client(r, w)
                c.send(0x11, konet.ZLIB_AES, struct.pack("<I", sid) + bytes(38))
                _, d = await c.recv()
                c.send(0x13, konet.AES_MODE, konet.login_hmac(d[:8], KEYCPU, KEYMAC))
                c.key = KEYCPU
                await c.recv(), await c.recv()                         # 0x14, 0x13B
                c.send(0x211, konet.AES_MODE, Writer().raw(bytes(20)).u64(konet.account_value(acc))
                       .u64(konet.account_value(acc)).u8(0).bytes())
                await c.recv()
                clients.append((c, w))
            (a, wa), (b, wb) = clients
            a.send(0x231, konet.PLAIN, bytes([1, 1]) + bytes(5))              # co-op stage
            b.send(0x233, konet.PLAIN, struct.pack("<i", 10001))
            self.assertEqual((await a.recv())[0], 0x237)
            a.send(0x234, konet.PLAIN, struct.pack("<iB", 10002, 0))
            self.assertEqual((await b.recv())[0], 0x234)
            for c, score in ((a, 300000), (b, 200000)):
                c.send(0x261, konet.AES_MODE, Writer().u16(6).i32(score).u8(1).u8(0).u8(1).u8(0).bytes())
                self.assertEqual((await c.recv())[0], 0x262)
            b.send(0x268, konet.AES_MODE, Writer().raw(bytes(12)).i32(0).i32(0).i32(77).i32(0).bytes())
            self.assertEqual((await b.recv())[0], 0x264)
            a.send(0x145, konet.PLAIN, struct.pack("<H", 6))
            cmd, d = await a.recv()
            self.assertEqual((cmd, d[38:41], d[52:55], struct.unpack_from("<i", d, 66)[0]), (0x146, b"ONE", b"TWO", 500000))
            a.send(0x143, konet.PLAIN, b"\0")                                 # 合作月排行
            cmd, d = await a.recv()
            self.assertEqual((cmd, d[18:21], struct.unpack_from("<i", d, 13 + 48)[0]), (0x144, b"TWO", 77))
            for _, w in clients:
                w.close()
            s.close()
            await s.wait_closed()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
