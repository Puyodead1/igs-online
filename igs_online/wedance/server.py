"""We Dance Online 2 game server (KoNet)."""
import asyncio
import json
import logging
import os
import struct
import time

from ..common.kotcp import KoTCP, FrameError
from ..common.packet import Reader, Writer
from . import konet, profile
from .db import DB, FIRST_UID
from .konet import PLAIN, AES_MODE, ZLIB, ZLIB_AES

log = logging.getLogger("wedance")

NAMES = {
    0x11: "login", 0x13: "login hmac", 0x21: "register", 0x23: "register hmac",
    0x131: "rank fight", 0x134: "rank store", 0x137: "rank song", 0x13D: "rank won",
    0x141: "rank coop", 0x143: "rank combo", 0x145: "rank song coop", 0x147: "rank exp",
    0x149: "rank cont combo",
    0x151: "store coin", 0x152: "cable broken", 0x153: "store info", 0x154: "ping data",
    0x161: "game value", 0x164: "csvlist info", 0x165: "ticker", 0x167: "notice",
    0x169: "conditions", 0x16B: "csvlist", 0x16D: "mini update done", 0x16E: "mini update",
    0x172: "profit",
    0x211: "player login", 0x213: "logout", 0x214: "worn clothes", 0x215: "create account",
    0x217: "create info", 0x219: "update info", 0x21B: "password", 0x21C: "transfer",
    0x222: "search fighters local", 0x223: "search fighters", 0x225: "vs info",
    0x231: "contest open", 0x232: "contest close", 0x233: "challenge", 0x234: "accept",
    0x235: "reject", 0x236: "continue", 0x241: "song cursor", 0x242: "song decide",
    0x243: "third song", 0x250: "fight relay", 0x251: "ready", 0x252: "start", 0x253: "end",
    0x255: "vs clothes",
    0x261: "song result", 0x263: "count", 0x265: "clothes", 0x267: "song played",
    0x268: "fight result", 0x269: "play log", 0x26A: "combo result", 0x26B: "items",
    0x26D: "pet", 0x26E: "mission", 0x271: "market", 0x273: "lucky bag", 0x275: "weticket",
}

# cmds relayed to the versus peer as they are
RELAY = {0x236, 0x241, 0x242, 0x243, 0x250, 0x251, 0x253, 0x255}


def worn(p, key):
    """(id, 5 colour bytes) of the item worn in a block"""
    es = profile.entries(p["blocks"].get(key, ""))
    if not es:
        return 0, b"\0" * 5
    f = es[0].split(",")
    try:
        item = int(f[0])
    except ValueError:
        item = 0
    digits = f[1] if len(f) > 1 else "0"
    digits = digits.rjust(5, "0")[-5:]
    cols = bytes(max(0, int(c) - (5 if key == "title" else 0)) if c.isdigit() else 0 for c in digits)
    return item, cols


class Config:
    def __init__(self, store_name=("IGS", "Online"), ticker="", notice="", time_offset=0):
        self.store_name = store_name
        self.ticker = ticker
        self.notice = notice
        self.time_offset = time_offset


class Cabinet:
    """one connected cabinet (one TCP link)"""

    def __init__(self, server, kotcp):
        self.server = server
        self.db = server.db
        self.link = kotcp
        self.decoder = konet.Decoder()
        self.rx_key = self.tx_key = konet.STATIC_KEY
        self.state = "new"
        self.store = 0
        self.keycpu = self.keymac = None
        self.challenge = b""
        self.player = None              # profile dict of the member playing, or a guest dict
        self.last_uid = 0
        self.new_account = None         # account number reserved by 0x215
        self.contest = None             # (diff, mode, md5) after 0x231
        self.peer = None
        self.pending = None             # challenger waiting for this cabinet's 0x234/0x235

    @property
    def name(self):
        port = (self.link.peer or ("?", "?"))[1]
        return "store %d/%s" % (self.store, port) if self.store else "link %s" % port

    # ---- I/O ----
    def send(self, cmd, mode, data=b""):
        if not data:
            data = b"\0"            # KoNetAP_Recv drops empty messages (0 = nothing received)
        log.debug("%s <- 0x%03X %s mode %d len %d %s", self.name, cmd, NAMES.get(cmd, NAMES.get(cmd - 1, "")),
                  mode, len(data), bytes(data[:48]).hex())
        for frame in konet.encode(cmd, mode, data, self.tx_key):
            self.link.send(frame)

    async def run(self):
        log.info("%s connected", self.name)
        try:
            while True:
                frame = await self.link.recv()
                msg = self.decoder.feed(frame)
                if msg is None:
                    continue
                cmd, mode, body = msg
                try:
                    data = konet.decode_body(mode, body, self.rx_key)
                except Exception as e:
                    log.warning("%s: 0x%03X mode %d: %s", self.name, cmd, mode, e)
                    continue
                log.debug("%s -> 0x%03X %s mode %d len %d %s", self.name, cmd, NAMES.get(cmd, ""), mode,
                          len(data), data[:48].hex())
                if not await self.dispatch(cmd, mode, data):
                    break
                await self.link.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, FrameError) as e:
            log.info("%s: link closed (%s)", self.name, e.__class__.__name__)
        finally:
            self.server.drop(self)
            self.link.close()

    async def dispatch(self, cmd, mode, data):
        if self.state == "new":
            if cmd == 0x21:
                return self.on_register(data)
            if cmd == 0x11:
                return self.on_login(data)
            log.warning("%s: 0x%03X before login", self.name, cmd)
            return False
        if self.state == "register":
            return self.on_register_hmac(cmd, data)
        if self.state == "login":
            return self.on_login_hmac(cmd, data)
        fn = HANDLERS.get(cmd)
        if fn is None:
            log.info("%s: 0x%03X %s (%d bytes) not handled", self.name, cmd, NAMES.get(cmd, "?"), len(data))
            return True
        try:
            fn(self, mode, data)
        except Exception:
            log.exception("%s: 0x%03X failed", self.name, cmd)
        return True

    # ---- registration 0x21..0x24 (net_iReg) ----
    def on_register(self, data):
        r = Reader(data)
        log.info("%s: registration, net %d soft %d", self.name, r.u16(), r.u16())
        self.challenge = os.urandom(16)
        self.send(0x22, AES_MODE, self.challenge)
        self.state = "register"
        return True

    def on_register_hmac(self, cmd, data):
        if cmd != 0x23 or len(data) != 38:
            log.warning("%s: registration: 0x%03X len %d", self.name, cmd, len(data))
            return False
        mac, keycpu, keymac = data[:16], data[16:32], data[32:38]
        if mac != konet.login_hmac(self.challenge[:8], keycpu, keymac):
            log.warning("%s: registration: bad HMAC", self.name)
            return False
        sid = self.db.register_store(keycpu, keymac)
        self.tx_key = self.rx_key = keycpu
        self.store = sid
        self.send(0x24, AES_MODE, struct.pack("<i", sid))
        log.info("%s: registered", self.name)
        return True

    # ---- login 0x11..0x14 (net_iConnect, net_vRecv) ----
    def on_login(self, data):
        r = Reader(data)
        sid = r.u32()
        keys = self.db.store_keys(sid)
        if not keys:
            log.warning("store %d unknown: delete the cabinet's registration (info/igs027a.nv) to register again", sid)
            return False
        self.store = sid
        self.keycpu, self.keymac = keys
        self.challenge = os.urandom(8)
        now = time.time() + self.server.config.time_offset
        w = Writer().raw(self.challenge).i32(int(now)).i32(int(now % 1 * 1e6))
        w.u16(0)                        # word_879043C
        w.u16(0)                        # g_wFeature (per player later)
        w.u16(0).u8(0)                  # world seed, world level
        w.u8(0)                         # ping test off
        w.u8(0)                         # no acropass reminder
        self.send(0x12, AES_MODE, w.bytes())
        self.state = "login"
        return True

    def on_login_hmac(self, cmd, data):
        if cmd != 0x13 or len(data) != 16:
            log.warning("%s: login: 0x%03X len %d", self.name, cmd, len(data))
            return False
        if data != konet.login_hmac(self.challenge, self.keycpu, self.keymac):
            log.warning("%s: login: bad HMAC (registration keys differ)", self.name)
            return False
        self.tx_key = self.rx_key = self.keycpu
        self.state = "online"
        self.server.attach(self)
        self.send(0x14, AES_MODE, b"\0")
        log.info("%s: online", self.name)
        return True

    # ---- players ----
    def current(self):
        if self.player and self.player.get("uid", 0) >= FIRST_UID:
            return self.player
        if self.last_uid:
            return self.db.player(self.last_uid)
        return None

    def save(self, p):
        if p and p.get("uid", 0) >= FIRST_UID:
            self.db.save(p)
            if self.player and self.player.get("uid") == p["uid"]:
                self.player = p

    def on_player_login(self, mode, data):
        if len(data) == 1:                       # guest
            p = profile.new_profile(0)
            p["blocks"]["talks"] = ""
            self.player = p
            self.last_uid = 0
            self.send(0x212, AES_MODE, profile.build(p))
            log.info("%s: guest login", self.name)
            return
        r = Reader(data)
        r.raw(20)
        account, password = r.u64(), r.u64()
        p, pw = self.db.player_by_account(account)
        if p is None:
            log.info("%s: login %s: no such account", self.name, konet.account_digits(account))
            self.send(0x212, AES_MODE, b"\xff")
            return
        if pw != password:
            log.info("%s: login %s: wrong password", self.name, konet.account_digits(account))
            self.send(0x212, AES_MODE, b"\xfe")
            return
        self.login(p, 0x212)

    def login(self, p, cmd, feature=0):
        p["logins"] = (p.get("logins", 0) + 1) & 0xFFFF
        p["user_feature"] = feature
        self.db.save(p)
        self.player = p
        self.last_uid = p["uid"]
        self.send(cmd, AES_MODE, profile.build(p))
        log.info("%s: %s logged in (uid %d, level %d)", self.name, konet.account_digits(p["account"]),
                 p["uid"], p.get("level", 1))

    def on_logout(self, mode, data):
        log.info("%s: logout", self.name)
        self.player = None

    def on_create_account(self, mode, data):
        account = Reader(data).u64()
        if self.db.account_exists(account):
            self.send(0x216, AES_MODE, b"\xff")          # already registered
            return
        self.new_account = account
        self.send(0x216, AES_MODE, b"\0")
        log.info("%s: account %s free", self.name, konet.account_digits(account))

    def on_create_info(self, mode, data):
        r = Reader(data)
        account, password = r.u64(), r.u64()
        info = data[16:]
        if self.db.account_exists(account):
            self.send(0x218, AES_MODE, b"\xff")
            return
        p = self.db.create_player(account, password)
        profile.apply_info(p, info)
        self.db.save(p)
        self.player = p
        self.last_uid = p["uid"]
        self.send(0x218, AES_MODE, Writer().i32(p["uid"]).str(bytes.fromhex(p["card_id"]), 20).bytes())
        log.info("%s: account %s created, uid %d", self.name, konet.account_digits(account), p["uid"])

    def on_update_info(self, mode, data):
        p = self.current()
        if not p:
            self.send(0x21A, AES_MODE, b"\xfd")
            return
        profile.apply_info(p, data)
        self.save(p)
        self.send(0x21A, AES_MODE, Writer().u16(p.get("user_feature", 0)).i32(p["uid"]).i32(p.get("weticket", 0)).bytes())

    def on_password(self, mode, data):
        p = self.current()
        if p:
            self.db.set_password(p["uid"], Reader(data).u64())
            log.info("%s: uid %d changed password", self.name, p["uid"])

    # ---- results ----
    def _update(self, fn, data, what):
        p = self.current()
        if not p:
            log.info("%s: %s without a member", self.name, what)
            return
        fn(p, data)
        self.save(p)
        log.info("%s: uid %d %s saved", self.name, p["uid"], what)

    def on_count(self, mode, data): self._update(profile.apply_count, data, "counters")
    def on_clothes(self, mode, data): self._update(profile.apply_clothes, data, "wardrobe")
    def on_worn(self, mode, data): self._update(profile.apply_worn, data, "worn clothes")
    def on_fight(self, mode, data): self._update(profile.apply_fight, data, "fight result")
    def on_pet(self, mode, data): self._update(profile.apply_pet, data, "pet")

    def on_items(self, mode, data):
        def f(p, d):
            p["blocks"]["props"] = profile.read_block(Reader(d))
        self._update(f, data, "items")

    def on_song_result(self, mode, data):
        r = Reader(data)
        song, score, diff, fc, gmode = r.u16(), r.i32(), r.u8(), r.u8(), r.u8()
        p = self.current() or self.player
        uid = p["uid"] if p else 0
        self.db.add_score(uid, self.store, song, diff, score, fc, gmode)
        log.info("%s: uid %d song %d diff %d score %d%s", self.name, uid, song, diff, score, " FC" if fc else "")

    def on_raw(self, mode, data, cmd=None):
        p = self.current()
        self.db.add_result(p["uid"] if p else 0, self.store, cmd, mode, data)

    def on_play_log(self, mode, data):
        uid = struct.unpack_from("<i", data)[0] if len(data) >= 4 else 0
        self.db.add_result(uid, self.store, 0x269, mode, data)
        log.info("%s: play log of uid %d (%d bytes)", self.name, uid, len(data))

    def on_market(self, mode, data):
        r = Reader(data)
        op = r.u8()
        r.i32(), r.i32()
        n = abs(r.i8())
        r.raw(n)
        p = self.current()
        if p:
            b = p["blocks"]
            if op in (0, 8):
                b["props"] = profile.read_block(r)
            elif op == 1:
                p["skin"], p["face"] = r.u8(), r.u8()
            elif op == 2:
                b["talks"] = profile.read_block(r)
            elif op in (4, 12):
                for k in ("head", "upbody", "downbody", "feet", "back"):
                    b[k] = profile.read_block(r)
            elif op == 11:
                for k in ("cap_head", "cap_upbody", "cap_downbody", "cap_feet", "cap_back", "cap_title",
                          "cap_luckybag"):
                    p[k] = r.u8()
            elif op == 13:
                p["cap_luckybag"] = r.u8()
            elif op == 14:
                s = r.u16()
                if s not in p["songs"]:
                    p["songs"].append(s)
            self.save(p)
            log.info("%s: uid %d market op %d", self.name, p["uid"], op)
        self.send(0x272, AES_MODE, b"\0" if p else b"\xff")

    def on_lucky_bag(self, mode, data):
        self.send(0x274, AES_MODE, b"\xff")      # no lucky bags

    def on_weticket(self, mode, data):
        def f(p, d):
            p["weticket"] = Reader(d).i32()
        self._update(f, data, "WeTickets")

    # ---- info, rankings ----
    def on_ticker(self, mode, data):
        self.send(0x166, AES_MODE, (self.server.config.ticker or " ").encode("latin-1")[:511])

    def on_notice(self, mode, data):
        self.send(0x168, AES_MODE, (self.server.config.notice or " ").encode("latin-1")[:1023])

    def on_conditions(self, mode, data):
        self.send(0x16A, AES_MODE, b"\0")         # no conditions

    def on_csvlist(self, mode, data):
        self.send(0x16C, AES_MODE, b"\0")         # keep the cabinet's own list

    def _store_names(self, w):
        a, b = self.server.config.store_name
        w.str(a, 10).str(b, 16)

    def on_rank_song(self, mode, data):
        """0x137 -> 0x138: best members' scores on a song (7 at most)"""
        song = Reader(data).u16()
        rows = self.db.song_top(song, 7)
        w = Writer().u16(song).u8(0).u16(0).u16(0)
        for row in rows:
            p = self.db.player(row["uid"])
            w.str(bytes.fromhex(p.get("name") or ""), 14).i32(row["score"])
            self._store_names(w)
        self.send(0x138, ZLIB, w.bytes())

    def on_rank_song_coop(self, mode, data):
        song = Reader(data).u16()
        w = Writer().u16(song).u16(0).u16(0).str(b"", 16).str(b"", 16)
        self.send(0x146, ZLIB, w.bytes())

    def _ranked(self, key, n):
        ps = [p for _, _, p in self.db.players()]
        ps.sort(key=lambda p: p.get(key, 0), reverse=True)
        return [p for p in ps if p.get(key, 0) > 0][:n]

    def _avatar(self, w, ps):
        if not ps:
            w.u8(0).u8(0).u8(0).raw(b"\0" * 10)
            return
        p = ps[0]
        w.u8(1).u8(p.get("skin", 0)).u8(p.get("face", 0))
        for k in ("head", "upbody", "downbody", "feet", "back"):
            w.u16(worn(p, k)[0])

    def on_rank_players(self, cmd, key):
        """0x131/0x13D/0x141/0x143 -> +1: 13-byte avatar of #1, 52-byte entries"""
        ps = self._ranked(key, 10)
        w = Writer()
        self._avatar(w, ps)
        for p in ps:
            w.u8(p.get("sex", 0)).u16(p.get("level", 1)).u16(p.get("title", 33))
            w.str(bytes.fromhex(p.get("name") or ""), 14).u16(0).u8(0)
            self._store_names(w)
            w.i32(p.get(key, 0))
        self.send(cmd + 1, ZLIB, w.bytes())

    def on_rank_players2(self, cmd, key):
        """0x147/0x149 -> +1: 13-byte avatar of #1, 49-byte entries"""
        ps = self._ranked(key, 10)
        w = Writer()
        self._avatar(w, ps)
        for p in ps:
            w.u8(p.get("sex", 0)).u16(p.get("level", 1)).u16(p.get("title", 33))
            w.str(bytes.fromhex(p.get("name") or ""), 14).i32(p.get(key, 0))
            self._store_names(w)
        self.send(cmd + 1, ZLIB, w.bytes())

    def on_rank_store(self, mode, data):
        self.send(0x135, ZLIB, Writer().i32(0).u8(0).u8(0).bytes())

    # ---- versus ----
    def vs_entry(self):
        """15-byte fighter list entry (0x221 / 0x224)"""
        p = self.player
        return (Writer().i32(p["uid"]).u16(p.get("level", 1)).u8(0).u8(0)
                .u8(2 if not self.contest or self.contest[1] != 1 else 1)
                .u16(p.get("won_combo", 0)).u16(p.get("strength", 0)).u8(0).u8(0).bytes())

    def vs_info(self, p):
        """144-byte profile (0x226, 0x237)"""
        w = Writer().i32(p["uid"]).u8(p.get("sex", 0)).u8(p.get("skin", 0)).u8(p.get("face", 0))
        w.str(bytes.fromhex(p.get("name") or ""), 14)
        for k in ("level", "title", "won_combo", "won", "lost", "world_rank"):
            w.u16(p.get(k, 0))
        for k in ("head", "upbody", "downbody", "feet", "back"):
            item, cols = worn(p, k)
            w.u16(item).raw(cols)
        item, cols = worn(p, "element")
        w.raw(cols).u16(item)
        w.u8(0)
        self._store_names(w)
        w.u16(self.store & 0xFFFF).u16(p.get("strength", 0)).u8(p.get("v87903BC", 0))
        w.i8(p.get("v87904A0", 0)).u8(0)
        pet = max(0, p.get("pet_current", 0) - 1)
        w.u8(pet).u16(p.get("pet%d" % pet, 0))
        w.i32(p.get("fight_score", 0)).i32(p.get("coop_score", 0)).u8(0).u8(0)
        w.i32(0).str(b"", 16).u16(0)
        return w.bytes()

    def on_search(self, cmd, mode, data):
        others = [c for c in self.server.cabinets() if c is not self and c.contest and c.player and not c.peer]
        body = b"".join(c.vs_entry() for c in others[:8])
        self.send(0x221 if cmd == 0x223 else 0x224, PLAIN, body)     # 0x223 global, 0x222 local
        log.info("%s: fighter search: %d", self.name, len(others))

    def on_vs_info(self, mode, data):
        uid = Reader(data).i32()
        c = self.server.cabinet_of(uid)
        self.send(0x226, ZLIB, self.vs_info(c.player) if c else b"\0")

    def on_contest_open(self, mode, data):
        r = Reader(data)
        self.contest = (r.u8(), r.u8(), r.raw(5))
        log.info("%s: contest open (diff %d, mode %d)", self.name, self.contest[0], self.contest[1])

    def on_contest_close(self, mode, data):
        log.info("%s: contest close", self.name)
        self.contest = None
        peer = self.peer
        self.unlink()
        if peer:
            peer.send(0x232, PLAIN, data)

    def on_challenge(self, mode, data):
        uid = Reader(data).i32()
        c = self.server.cabinet_of(uid)
        if not c or not c.contest or c.peer or not self.player:
            self.send(0x235, PLAIN, Writer().i32(uid).u8(0).bytes())
            log.info("%s: challenge %d: not available", self.name, uid)
            return
        c.pending = self
        c.send(0x237, PLAIN, self.vs_info(self.player))
        log.info("%s: challenges %s", self.name, c.name)

    def on_contest_answer(self, cmd, mode, data):
        a = self.pending
        self.pending = None
        if not a:
            return
        a.send(cmd, PLAIN, data)
        if cmd == 0x234:
            self.link_peer(a)
            log.info("%s: versus with %s", self.name, a.name)

    def link_peer(self, other):
        self.unlink()
        other.unlink()
        self.peer, other.peer = other, self

    def unlink(self):
        if self.peer:
            self.peer.peer = None
            self.peer = None

    def on_relay(self, cmd, mode, data):
        if self.peer:
            self.peer.send(cmd, mode, data)
        if cmd == 0x253 and not self.peer:
            self.send(0x253, PLAIN, b"\0")

    def on_start(self, mode, data):
        self.send(0x252, PLAIN, data)
        if self.peer:
            self.peer.send(0x252, PLAIN, data)

    def on_ignore(self, mode, data):
        pass


def _bind(cmd, fn):
    return lambda c, mode, data: fn(c, cmd, mode, data)


HANDLERS = {
    0x211: Cabinet.on_player_login, 0x213: Cabinet.on_logout, 0x214: Cabinet.on_worn,
    0x215: Cabinet.on_create_account, 0x217: Cabinet.on_create_info, 0x219: Cabinet.on_update_info,
    0x21B: Cabinet.on_password,
    0x261: Cabinet.on_song_result, 0x263: Cabinet.on_count, 0x265: Cabinet.on_clothes,
    0x268: Cabinet.on_fight, 0x269: Cabinet.on_play_log, 0x26B: Cabinet.on_items, 0x26D: Cabinet.on_pet,
    0x271: Cabinet.on_market, 0x273: Cabinet.on_lucky_bag, 0x275: Cabinet.on_weticket,
    0x165: Cabinet.on_ticker, 0x167: Cabinet.on_notice, 0x169: Cabinet.on_conditions,
    0x16B: Cabinet.on_csvlist,
    0x137: Cabinet.on_rank_song, 0x145: Cabinet.on_rank_song_coop, 0x134: Cabinet.on_rank_store,
    0x131: lambda c, m, d: c.on_rank_players(0x131, "fight_score"),
    0x13D: lambda c, m, d: c.on_rank_players(0x13D, "won"),
    0x141: lambda c, m, d: c.on_rank_players(0x141, "coop_score"),
    0x143: lambda c, m, d: c.on_rank_players(0x143, "won_combo"),
    0x147: lambda c, m, d: c.on_rank_players2(0x147, "exp"),
    0x149: lambda c, m, d: c.on_rank_players2(0x149, "max_cont_combo"),
    0x222: _bind(0x222, Cabinet.on_search), 0x223: _bind(0x223, Cabinet.on_search),
    0x225: Cabinet.on_vs_info, 0x231: Cabinet.on_contest_open, 0x232: Cabinet.on_contest_close,
    0x233: Cabinet.on_challenge,
    0x234: _bind(0x234, Cabinet.on_contest_answer), 0x235: _bind(0x235, Cabinet.on_contest_answer),
    0x252: Cabinet.on_start,
}
for _c in RELAY:
    HANDLERS[_c] = _bind(_c, Cabinet.on_relay)
for _c in (0x26A, 0x26E, 0x267):
    HANDLERS[_c] = (lambda cmd: lambda c, m, d: c.on_raw(m, d, cmd))(_c)
for _c in (0x151, 0x152, 0x153, 0x154, 0x161, 0x164, 0x16D, 0x16E, 0x172):
    HANDLERS[_c] = Cabinet.on_ignore         # store bookkeeping; 0x16E would want 0x16F (never sent)


class Server:
    def __init__(self, db_path, config=None):
        self.db = DB(db_path)
        self.config = config or Config()
        self._cabinets = set()

    def cabinets(self):
        return [c for c in self._cabinets if c.state == "online"]

    def cabinet_of(self, uid):
        for c in self.cabinets():
            if c.player and c.player.get("uid") == uid:
                return c
        return None

    def attach(self, cab):
        # several links may share a store (two loader instances on one set);
        # a dead one goes when its 90 s idle timeout runs out
        self._cabinets.add(cab)

    def drop(self, cab):
        if cab.peer:
            peer = cab.peer
            cab.unlink()
            peer.send(0x232, PLAIN, b"\0")
        self._cabinets.discard(cab)

    async def handle(self, reader, writer):
        await Cabinet(self, KoTCP(reader, writer)).run()

    # ---- admin (JSON lines on a local port) ----
    async def admin(self, reader, writer):
        try:
            line = await reader.readline()
            req = json.loads(line or b"{}")
            res = self.admin_cmd(req)
        except Exception as e:
            res = {"error": str(e)}
        writer.write((json.dumps(res) + "\n").encode())
        await writer.drain()
        writer.close()

    def admin_cmd(self, req):
        cmd = req.get("cmd")
        if cmd == "qr":
            p, _ = self.db.player_by_account(konet.account_value(req["account"]))
            if not p:
                return {"error": "no such account"}
            cabs = self.cabinets()
            if req.get("store"):
                cabs = [c for c in cabs if c.store == int(req["store"])]
            if len(cabs) != 1:
                return {"error": "%d cabinets match; give --store" % len(cabs),
                        "stores": [c.store for c in self.cabinets()]}
            c = cabs[0]
            c.login(p, 0x21E, profile.UF_QR_LOGIN)
            asyncio.ensure_future(c.link.drain())
            return {"ok": "pushed uid %d to store %d" % (p["uid"], c.store)}
        if cmd == "online":
            return {"cabinets": [{"store": c.store, "uid": c.player.get("uid") if c.player else None}
                                 for c in self.cabinets()]}
        return {"error": "unknown command"}


async def serve(host, port, db_path, admin_port=None, config=None):
    srv = Server(db_path, config)
    s = await asyncio.start_server(srv.handle, host, port)
    log.info("We Dance server on %s:%d, database %s", host, port, db_path)
    servers = [s]
    if admin_port:
        servers.append(await asyncio.start_server(srv.admin, "127.0.0.1", admin_port))
        log.info("admin on 127.0.0.1:%d", admin_port)
    await asyncio.gather(*(x.serve_forever() for x in servers))
