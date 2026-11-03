"""Percussion Master 3 game server (pm3's NetManager link; docs/PM3.md)."""
import asyncio
import json
import logging
import os
import random
import struct
import time

from ..common.kotcp import KoTCP, FrameError
from ..common.packet import Reader, Writer
from . import message, profile
from .db import DB
from .events import Events, CONTEST_RANK, DRAGON, DRAGON2, HOLIDAYS, big5

log = logging.getLogger("pm3")

NAMES = {
    0x111: "register", 0x113: "register reply", 0x121: "login", 0x123: "login reply",
    0x131: "store info", 0x132: "day book", 0x133: "month book", 0x134: "time", 0x135: "settings reset",
    0x136: "book deleted", 0x137: "word", 0x138: "game level", 0x139: "store info",
    0x211: "ID free?", 0x213: "sign up", 0x215: "member login", 0x217: "profile 150", 0x218: "logout",
    0x219: "sign-up dropped", 0x21A: "continue", 0x21B: "profile", 0x221: "mode", 0x222: "difficulty",
    0x223: "last song", 0x231: "play log", 0x232: "ranking result", 0x234: "ranking place",
    0x241: "rank page 60 class 0", 0x243: "rank page 60 class 1", 0x245: "rank page 2", 0x247: "rank page 3",
    0x249: "rank page 4", 0x251: "rank names 0", 0x253: "rank names 1", 0x255: "rank names 2",
    0x257: "rank names 3", 0x259: "rank names 4", 0x271: "boards", 0x273: "ticker", 0x281: "dragon list",
    0x283: "dragon", 0x285: "dragon damage", 0x286: "dragon nonstop", 0x288: "dragon damage 1p",
    0x289: "dragon again", 0x28B: "dragon 9 damage", 0x28C: "dragon dropped", 0x291: "rank list 0",
    0x293: "rank list 1", 0x295: "rank list 2", 0x297: "rank list 3", 0x299: "rank list 4",
    0x2A1: "dragon 2 page", 0x2A3: "dragon 2", 0x2A5: "dragon 2 damage", 0x2A6: "dragon 2 dropped",
    0x2A7: "dragon 2 entry", 0x411: "online", 0x413: "hot songs done", 0x416: "notice",
}

ENTRY = 68                    # ranking entry (the game's table; 0x29x send it from +4)
LIST = 5                      # entries per ranking page
# ranking kind on the wire -> its list / names / old-page requests
LIST_CMDS = {0x291: 0, 0x293: 1, 0x295: 2, 0x297: 3, 0x299: 4}
NAME_CMDS = {0x251: 0, 0x253: 1, 0x255: 2, 0x257: 3, 0x259: 4}
OLD60_CMDS = {0x241: 0, 0x243: 1}
OLD68_CMDS = {0x245: 2, 0x247: 3, 0x249: 4}
KIND_NAMES = {0: "normal", 1: "nonstop (battle drum)", 2: "Knights' song contest", 3: "score contest",
              4: "combo contest"}
RANK_BOARD = {v: k for k, v in CONTEST_RANK.items()}     # wire kind -> board kind


def cstr(b):
    return bytes(b).split(b"\0", 1)[0].decode("latin-1")


class Config:
    def __init__(self, events=None, store_name="igs-online", time_offset=0):
        self.events = events or Events()
        self.store_name = store_name
        self.time_offset = time_offset


class Cabinet:
    """one connected cabinet; up to two players on it (versus is local)"""

    def __init__(self, server, link):
        self.server = server
        self.db = server.db
        self.ev = server.config.events
        self.link = link
        self.state = "new"
        self.store = 0
        self.ab = (0, 0)
        self.versions = ()
        self.slots = [None, None]          # member ids logged in, per player
        self.reserved = [None, None]       # sign-up: ID kept by 0x211 until 0x213
        self.offered = {}                  # (player, dragon index) -> dragon id (board 14)
        self.dragon2 = None                # the world dragon offered (board 22)

    @property
    def name(self):
        port = (self.link.peer or ("?", "?"))[1]
        return "store %d/%s" % (self.store, port) if self.store else "link %s" % port

    # ---- I/O ----
    def send(self, cmd, data=b""):
        log.debug("%s <- 0x%03X %s len %d %s", self.name, cmd, NAMES.get(cmd, NAMES.get(cmd - 1, "")),
                  len(data), bytes(data[:48]).hex())
        self.link.send(message.encode(cmd, data))

    async def run(self):
        log.info("%s connected", self.name)
        try:
            while True:
                frame = await self.link.recv()
                try:
                    cmd, data = message.decode(frame)
                except message.MessageError as e:
                    log.warning("%s: %s", self.name, e)
                    continue
                log.debug("%s -> 0x%03X %s len %d %s", self.name, cmd, NAMES.get(cmd, ""), len(data),
                          data[:48].hex())
                if not self.dispatch(cmd, data):
                    break
                await self.link.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, FrameError) as e:
            log.info("%s: link closed (%s)", self.name, e.__class__.__name__)
        finally:
            self.server.drop(self)
            self.link.close()

    def dispatch(self, cmd, data):
        if self.state == "new":
            if cmd == 0x111:
                return self.on_register(data)
            if cmd == 0x121:
                return self.on_login(data)
            log.warning("%s: 0x%03X before login", self.name, cmd)
            return False
        if self.state in ("register", "login"):
            return self.on_challenge(cmd, data)
        fn = HANDLERS.get(cmd)
        if fn is None:
            log.info("%s: 0x%03X %s (%d bytes) not handled", self.name, cmd, NAMES.get(cmd, "?"), len(data))
            return True
        try:
            fn(self, cmd, data)
        except Exception:
            log.exception("%s: 0x%03X failed", self.name, cmd)
        return True

    # ---- registration 0x111..0x114, login 0x121..0x124 ----
    def challenge(self, cmd):
        self.ab = (random.getrandbits(32), random.getrandbits(32))
        self.send(cmd, struct.pack("<II", *self.ab))

    def on_register(self, data):
        self.versions = struct.unpack_from("<4H", data.ljust(8, b"\0"))
        log.info("%s: registration, generation %d version %d edition %d area %d", self.name, *self.versions)
        self.challenge(0x112)
        self.state = "register"
        return True

    def on_login(self, data):
        self.store, *v = struct.unpack_from("<I4H", data.ljust(12, b"\0"))
        self.versions = tuple(v)
        self.challenge(0x122)
        self.state = "login"
        return True

    def on_challenge(self, cmd, data):
        want = 0x113 if self.state == "register" else 0x123
        if cmd != want or len(data) < 38:
            log.warning("%s: 0x%03X (%d bytes) instead of 0x%03X", self.name, cmd, len(data), want)
            return False
        ok = data[:16] == message.challenge_reply(*self.ab)
        cpuid, mac = bytes(data[16:32]), bytes(data[32:38])
        if self.state == "register":
            sid = self.db.register_store(cpuid, mac, self.versions) if ok else 0
            log.info("%s: registered as store %d%s", self.name, sid, "" if ok else " (bad reply: refused)")
            self.send(0x114, struct.pack("<i", sid))
            self.state = "new"               # the cabinet logs in next (0x121)
            return True
        if not ok:
            log.warning("%s: bad login reply", self.name)
            self.send(0x124, struct.pack("<b", -1))
            self.state = "new"
            return True
        if not self.db.store_known(self.store):
            log.info("%s: store id unknown here; taken on", self.name)
            self.db.adopt_store(self.store, cpuid, mac, self.versions)
        self.db.seen_store(self.store)
        log.info("%s: logged in (generation %d version %d edition %d area %d)", self.name, *self.versions)
        self.send(0x124, b"\0")
        self.state = "online"
        self.server.attach(self)
        self.push_time()                     # applied only in the first 6 s of the game's start
        return True

    def push_time(self):
        t = time.time() + self.server.config.time_offset
        self.send(0x134, struct.pack("<II", int(t), int(t % 1 * 1e6)))

    def on_online(self, cmd, data):          # 0x411: the hot songs
        self.send(0x412, self.ev.hot_songs(self.db.setting("hot") or self.db.popular()))

    def on_online_done(self, cmd, data):     # 0x413
        self.send(0x416, self.ev.notice(self.db.setting("notice")))

    # ---- machine ----
    def on_book(self, cmd, data):
        self.db.add_book(self.store, cmd, data)
        log.info("%s: bookkeeping 0x%03X %s", self.name, cmd, NAMES.get(cmd, ""))

    def on_store_info(self, cmd, data):      # 0x139: the operator's store choice
        d = bytes(data).ljust(36, b"\0")
        country, province, region, sid = struct.unpack_from("<BBHI", d)
        info = {"country": country, "province": province, "region": region, "store_no": sid,
                "name": d[8:32].split(b"\0", 1)[0].hex(), "coins": d[32]}
        self.db.set_store_info(self.store, info)
        log.info("%s: store info %s", self.name, info)

    # ---- members ----
    def member_profile(self, p):
        mid = self.slots[p]
        m = self.db.member(mid) if mid else None
        return m, (bytes(m["profile"]) if m else None)

    def send_profile(self, p, mid):
        m = self.db.member(mid)
        b = bytearray(self.server.rewards(mid, bytes(m["profile"])))
        b[0] = p
        self.db.save_profile(mid, bytes(b), self.store)
        self.slots[p] = mid
        self.send(0x21B, bytes(b))
        log.info("%s: player %d is member %d %s (lv %d)", self.name, p + 1, mid,
                 profile.text(profile.name(b)), b[25])

    def on_id_query(self, cmd, data):        # 0x211: is this ID free? (sign-up)
        p, login = data[0] & 1, cstr(data[1:17])
        if not login or self.db.member_by_login(login) or self.server.reserved_elsewhere(self, login):
            self.send(0x212, bytes((p,)) + struct.pack("<b", -2))
            return
        self.reserved[p] = login
        self.send(0x212, bytes((p, 0)))
        log.info("%s: player %d signs up as %s", self.name, p + 1, login)

    def on_sign_up(self, cmd, data):         # 0x213: password, nickname, role
        d = bytes(data).ljust(34, b"\0")
        p, pw, nick, role = d[0] & 1, cstr(d[1:17]), d[17:33].split(b"\0", 1)[0], d[33]
        login = self.reserved[p]
        if not login or self.db.member_by_login(login):
            self.send(0x214, bytes((p,)) + struct.pack("<b", -1))
            return
        m = self.db.create_member(login, pw, nick, role, self.store)
        self.reserved[p] = None
        if self.ev.running(18, self.db.setting("active")):
            prof = bytes(m["profile"])
            for c in self.ev.data.get("registration_cursors", {}).get("cursors", []):
                prof = profile.grant_cursor(prof, c)
            self.db.save_profile(m["mid"], prof)
        log.info("%s: new member %d %s %s", self.name, m["mid"], login, profile.text(nick))
        self.send_profile(p, m["mid"])

    def on_sign_up_dropped(self, cmd, data):  # 0x219
        p = data[0] & 1
        if self.reserved[p]:
            log.info("%s: player %d dropped the sign-up of %s", self.name, p + 1, self.reserved[p])
        self.reserved[p] = None

    def on_member_login(self, cmd, data):    # 0x215: ID, password
        d = bytes(data).ljust(33, b"\0")
        p, login, pw = d[0] & 1, cstr(d[1:17]), cstr(d[17:33])
        m = self.db.member_by_login(login)
        # Login::NetUpdata / MsgProc: -2, -4 "wrong ID or password" (asked again once),
        # -5 "this account has been locked", -6 "this account is already logged in"
        if not m:
            err = -2
        elif m["password"] != pw:
            err = -4
        elif m["mid"] in (self.db.setting("locked") or []):
            err = -5
        elif self.server.playing(m["mid"], self, p):
            err = -6                         # the other player, or another cabinet
        else:
            self.send_profile(p, m["mid"])
            return
        log.info("%s: player %d login %s refused (%d)", self.name, p + 1, login, err)
        self.send(0x216, bytes((p,)) + struct.pack("<b", err))

    def on_logout(self, cmd, data):          # 0x218
        p = data[0] & 1
        if self.slots[p]:
            log.info("%s: player %d (member %d) logged out", self.name, p + 1, self.slots[p])
        self.slots[p] = None

    def on_continue(self, cmd, data):        # 0x21A
        log.info("%s: player %d continues", self.name, (data[0] & 1) + 1)

    def on_profile(self, cmd, data):         # 0x21B: save
        p = data[0] & 1
        m, stored = self.member_profile(p)
        if not m:
            log.info("%s: profile of player %d, not logged in: ignored", self.name, p + 1)
            return
        if struct.unpack_from("<I", data, 4)[0] != m["mid"]:
            log.warning("%s: profile of member %d for slot of %d: ignored", self.name,
                        struct.unpack_from("<I", data, 4)[0], m["mid"])
            return
        b = profile.merge_in(stored, data)
        self.db.save_profile(m["mid"], self.server.rewards(m["mid"], b), self.store)
        log.info("%s: member %d saved (lv %d exp %d)", self.name, m["mid"], b[25], profile.get(b, "exp"))

    def on_profile_byte(self, cmd, data):    # 0x221 mode, 0x222 difficulty, 0x223 last song
        p, v = data[0] & 1, data[1] if len(data) > 1 else 0
        m, b = self.member_profile(p)
        if not m:
            return
        b = bytearray(b)
        if cmd == 0x222:
            b[profile.FIELDS["class_choice"][0]] = v
        elif cmd == 0x223:
            b[profile.LAST_SONG + b[profile.FIELDS["class_choice"][0]] % 5] = v
        else:
            log.info("%s: player %d mode %d", self.name, p + 1, v)
            return
        self.db.save_profile(m["mid"], bytes(b))

    def on_play_log(self, cmd, data):        # 0x231
        p = data[0] & 1
        lg = profile.play_log(data)
        mid = self.slots[p]
        if mid:
            self.db.add_log(mid, self.store, lg["mode"], lg["total"], data)
            for s in lg["stages"]:
                self.db.add_play(mid, self.store, s["song"], s["class"], s["score"])
        log.info("%s: play log, player %d (member %s) %s, %d stages, total %d", self.name, p + 1, mid,
                 lg["mode_name"], len(lg["stages"]), lg["total"])

    # ---- rankings ----
    def rank_key(self, kind, cls):
        """(class, period) of a board: kinds 0, 1 per difficulty for all time; contests per period"""
        if kind in (0, 1):
            return cls % 5, ""
        return 0, self.ev.period(RANK_BOARD[kind])

    def on_rank_upload(self, cmd, data):     # 0x232 -> 0x233
        r = profile.ranking_upload(data)
        p, kind = r["player"] & 1, r["kind"]
        mid = self.slots[p]
        old_place, old = 0, None
        if mid and kind in (0, 1, 2, 3, 4):
            cls, period = self.rank_key(kind, r["class"])
            old = self.db.rank_entry(kind, cls, period, mid)
            old_place = self.db.place(kind, cls, period, mid)
            value, song = {2: (r["song_score"], r["contest_song"]), 4: (r["max_combo"], 0)}.get(
                kind, (r["total"], 0))
            if kind == 2 and r["contest_song"] != self.ev.contest_song()[0]:
                value = 0
            if value and (not old or value > old["value"]):
                self.db.put_rank(kind, cls, period, mid, value, r["max_combo"], r["stages"], song, self.store)
            log.info("%s: member %d %s: %d (was %s, place %d)", self.name, mid, KIND_NAMES[kind], value,
                     old and old["value"], old_place)
        w = bytearray(14)
        struct.pack_into("<BBH", w, 0, p, kind, old_place)
        prev = old["value"] if old else 0
        if kind == 2:
            struct.pack_into("<I", w, 4, prev)
        elif kind == 4:
            struct.pack_into("<H", w, 12, min(prev, 0xFFFF))
        else:
            struct.pack_into("<I", w, 8, prev)
        self.send(0x233, bytes(w))

    def on_rank_place(self, cmd, data):      # 0x234 -> 0x235
        p, kind = data[0] & 1, data[1] if len(data) > 1 else 0
        mid, place = self.slots[p], 0
        if mid and kind in KIND_NAMES:
            m, b = self.member_profile(p)
            cls, period = self.rank_key(kind, b[profile.FIELDS["class_choice"][0]])
            place = self.db.place(kind, cls, period, mid)
        self.send(0x235, struct.pack("<BBH", p, kind, place))

    def entry(self, e, place, layout=68):
        """one ranking entry: 68 bytes as the game's table holds it (0x24x), or as 0x29x send it
        (from the table's +4), or 60 bytes (0x242)"""
        m = self.db.member(e["mid"]) if e else None
        t = bytearray(ENTRY + 4)
        if m:
            b = bytes(m["profile"])
            info = self.db.store_info(e["store"] or 0)
            store_name = bytes.fromhex(info.get("name", "")) or big5(self.server.config.store_name, 15)
            struct.pack_into("<IIBBH", t, 4, e["mid"], place, info.get("country", 0), info.get("province", 0),
                             b[25])
            t[16:32] = store_name[:15].ljust(16, b"\0")
            struct.pack_into("<HB", t, 32, e.get("combo") or 0, e.get("stages") or 0)
            t[40:56] = profile.name(b)[:15].ljust(16, b"\0")
            t[56] = b[profile.FIELDS["title"][0]]       # the "Title" column (netrank::DataSet)
            struct.pack_into("<IH", t, 60, e["value"], min(e["value"] if e["kind"] == 4 else e.get("combo") or 0,
                                                           0xFFFF))
            t[66] = e.get("song") or 0
        if layout == 68:
            return bytes(t[:ENTRY])
        if layout == 64:                     # 0x29x: from +4
            return bytes(t[4:4 + ENTRY])
        w = bytearray(60)                    # 0x242 / 0x244 (GetRecvMessage 578 / 580)
        w[4:12] = t[4:12]
        w[12] = t[13]
        w[14:16] = t[14:16]
        w[16:32] = t[16:32]
        w[32:48] = t[40:56]
        w[48] = t[56]
        w[52:56] = t[60:64]
        w[56] = t[64]
        return bytes(w)

    def page(self, kind, cls, start, layout):
        cls, period = self.rank_key(kind, cls)
        rows = self.db.board(kind, cls, period, start, LIST)
        rows = [dict(r, kind=kind) for r in rows]
        return b"".join(self.entry(rows[i] if i < len(rows) else None, start + i + 1, layout) for i in range(LIST))

    def on_rank_list(self, cmd, data):       # 0x291..0x299 -> +1: five entries from a place
        kind = LIST_CMDS[cmd]
        d = bytes(data).ljust(4, b"\0")
        if kind in (0, 1):
            cls, start = d[0], struct.unpack_from("<H", d, 2)[0]
            self.send(cmd + 1, struct.pack("<BxH", cls, start) + self.page(kind, cls, start, 64))
        else:
            start = struct.unpack_from("<H", d)[0]
            self.send(cmd + 1, struct.pack("<Hxx", start) + self.page(kind, 0, start, 64))

    def on_rank_old(self, cmd, data):        # 0x241..0x249 (no caller in the game) -> +1
        d = bytes(data).ljust(4, b"\0")
        if cmd in OLD60_CMDS:
            kind, cls, start = OLD60_CMDS[cmd], d[0], struct.unpack_from("<H", d, 2)[0]
            self.send(cmd + 1, struct.pack("<BxH", cls, start) + self.page(kind, cls, start, 60))
        else:
            kind, start = OLD68_CMDS[cmd], struct.unpack_from("<H", d)[0]
            self.send(cmd + 1, struct.pack("<Hxx", start) + self.page(kind, 0, start, 68))

    def on_rank_names(self, cmd, data):      # 0x251..0x259 -> +1: the names of 50 places (the tower)
        kind = NAME_CMDS[cmd]
        d = bytes(data).ljust(6, b"\0")
        if kind in (0, 1):
            cls, start, n = d[0], *struct.unpack_from("<HH", d, 2)
        else:
            cls, (start, n) = 0, struct.unpack_from("<HH", d)
        c, period = self.rank_key(kind, cls)
        rows = self.db.board(kind, c, period, start, min(n or 50, 50))
        names = b"".join(profile.name(bytes(self.db.member(r["mid"])["profile"]))[:15].ljust(16, b"\0")
                         for r in rows)
        head = struct.pack("<BxHH", cls, start, len(rows)) if kind in (0, 1) else struct.pack("<HH", start, len(rows))
        self.send(cmd + 1, head + names)

    # ---- boards, ticker ----
    def on_boards(self, cmd, data):          # 0x271
        self.send(0x272, self.ev.boards(self.db.setting("active")))

    def on_ticker(self, cmd, data):          # 0x273
        self.send(0x274, self.ev.tickers(self.db.setting("active")))

    # ---- dragons (board 14) ----
    def dragon_class(self, p):
        m, b = self.member_profile(p)
        return b[profile.FIELDS["class_choice"][0]] % 5 if b else 0

    def dragon_record(self, d):
        r = bytearray(80)
        if d:
            info = self.db.store_info(d["store"] or 0)
            finder = self.db.member(d["finder"]) if d["finder"] else None
            struct.pack_into("<IxBxxI", r, 0, 1, d["kind"], d["hp"])
            r[24] = info.get("province", 0)
            r[44:60] = d["name"].encode("big5", "replace")[:15].ljust(16, b"\0")
            if finder:
                r[60:76] = profile.name(bytes(finder["profile"]))[:15].ljust(16, b"\0")
        return bytes(r)

    def offer_dragon(self, p, idx):
        """the idx-th (0..2) living dragon of this player's difficulty; more appear up to three"""
        if not self.ev.running(DRAGON, self.db.setting("active")):
            return None
        cfg, period, cls = self.ev.data["dragons"], self.ev.period(DRAGON), self.dragon_class(p)
        alive = self.db.dragons(DRAGON, period, cls)
        while len(alive) <= idx < 3:
            kind = random.randrange(10)
            hp = cfg["hp_special"] if kind == 9 else cfg["hp"]
            did = self.db.add_dragon(DRAGON, period, cls, kind, hp, cfg["names"][kind], self.store, self.slots[p])
            log.info("%s: dragon %d (%s, difficulty %d) appears", self.name, did, cfg["names"][kind], cls)
            alive = self.db.dragons(DRAGON, period, cls)
        if idx >= len(alive):
            return None
        d = alive[idx]
        self.offered[(p, idx)] = d["id"]
        if self.slots[p]:
            self.db.mark(self.slots[p], DRAGON, period, cls, d["kind"], 1, d["id"])
        return d

    def on_dragon(self, cmd, data):          # 0x283 (p, i) / 0x286 (i) / 0x289 (p, i)
        if cmd == 0x286:
            p, idx = 0, data[0]
        else:
            p, idx = data[0] & 1, data[1] if len(data) > 1 else 0
        rec = self.dragon_record(self.offer_dragon(p, idx % 3))
        if cmd == 0x286:
            self.send(0x287, bytes((idx, 0, 0, 0)) + rec)
        else:
            self.send(cmd + 1, bytes((p, idx, 0, 0)) + rec)

    def damage(self, value, divisor):
        return max(1, -(-value // max(1, divisor))) if value else 0

    def on_dragon_damage(self, cmd, data):   # 0x285 / 0x28B (p, i, u32) / 0x288 (i, u32)
        d = bytes(data).ljust(8, b"\0")
        p, idx = (0, d[0]) if cmd == 0x288 else (d[0] & 1, d[1])
        value = struct.unpack_from("<I", d, 4)[0]
        did = self.offered.pop((p, idx % 3), None)
        mid = self.slots[p]
        if not did:
            log.info("%s: dragon damage %d without a dragon", self.name, value)
            return
        cfg = self.ev.data["dragons"]
        dragon = self.db.hit_dragon(did, self.damage(value, cfg["damage_divisor"]))
        log.info("%s: dragon %d hit for %d (%d left) by member %s", self.name, did, value, dragon["hp"], mid)
        if mid:
            self.db.mark(mid, DRAGON, dragon["period"], dragon["cls"], dragon["kind"], 5, did)
        if dragon["hp"] <= 0 and dragon["catcher"] is None:
            self.server.caught(dragon, mid, DRAGON)

    def on_dragon_drop(self, cmd, data):     # 0x28C (i) / 0x2A6 (i)
        idx = data[0] if data else 0
        if cmd == 0x2A6:
            self.dragon2 = None
        else:
            for p in (0, 1):
                self.offered.pop((p, idx % 3), None)

    def on_dragon_list(self, cmd, data):     # 0x281 -> 0x282: the player's dragons, 5 x 10
        p = data[0] & 1
        mid = self.slots[p]
        w = bytearray(604)
        w[0] = p
        if mid:
            for mk in self.db.marks(mid, DRAGON, self.ev.period(DRAGON)):
                o = 4 + 120 * (mk["cls"] % 5) + 12 * (mk["kind"] % 10)
                struct.pack_into("<IBxxxI", w, o, 1, mk["state"], mk["time"] or 0)
        self.send(0x282, bytes(w))

    # ---- dragon 2 (board 22) ----
    def world_dragon(self):
        if not self.ev.running(DRAGON2, self.db.setting("active")):
            return None
        cfg, period = self.ev.data["dragon2"], self.ev.period(DRAGON2)
        alive = self.db.dragons(DRAGON2, period)
        if not alive:
            n = len(self.db.dragons(DRAGON2, period, alive=False))
            group, slot = 2 + n // 10 % 3, n % 10
            did = self.db.add_dragon(DRAGON2, period, group, slot, cfg["hp"],
                                     cfg["names"][n % len(cfg["names"])], self.store, None)
            log.info("%s: world dragon %d appears", self.name, did)
            alive = self.db.dragons(DRAGON2, period)
        return alive[0]

    def on_dragon2(self, cmd, data):         # 0x2A3 (p, 2) -> 0x2A4
        p = data[0] & 1
        d = self.world_dragon()
        r = bytearray(68)
        if d:
            info = self.db.store_info(d["store"] or 0)
            struct.pack_into("<IxBxxI", r, 0, 1, d["kind"], d["hp"])
            r[20] = info.get("country", 0)
            r[48:64] = d["name"].encode("big5", "replace")[:15].ljust(16, b"\0")
            self.dragon2 = d["id"]
            if self.slots[p]:
                self.db.mark(self.slots[p], DRAGON2, d["period"], d["cls"], d["kind"], 1, d["id"])
        self.send(0x2A4, bytes((p, 0, 0, 0)) + bytes(r))

    def on_dragon2_entry(self, cmd, data):   # 0x2A7 (p, i, u8)
        p = data[0] & 1
        log.info("%s: player %d takes on the world dragon (%s)", self.name, p + 1, bytes(data).hex())

    def on_dragon2_damage(self, cmd, data):  # 0x2A5 (p, u32)
        d = bytes(data).ljust(8, b"\0")
        p, value = d[0] & 1, struct.unpack_from("<I", d, 4)[0]
        if not self.dragon2:
            log.info("%s: world dragon damage %d without a dragon", self.name, value)
            return
        dragon = self.db.hit_dragon(self.dragon2, self.damage(value, self.ev.data["dragon2"]["damage_divisor"]))
        mid = self.slots[p]
        log.info("%s: world dragon %d hit for %d (%d left)", self.name, dragon["id"], value, dragon["hp"])
        if mid:
            self.db.mark(mid, DRAGON2, dragon["period"], dragon["cls"], dragon["kind"], 5, dragon["id"])
        if dragon["hp"] <= 0 and dragon["catcher"] is None:
            self.server.caught(dragon, mid, DRAGON2)
        self.dragon2 = None

    def on_dragon2_page(self, cmd, data):    # 0x2A1 (p, page) -> 0x2A2: three dragons of group `page`
        p, page = data[0] & 1, data[1] if len(data) > 1 else 0
        mid = self.slots[p]
        period = self.ev.period(DRAGON2)
        w = bytearray(208)
        w[0], w[1] = p, page
        mine = {m["dragon"]: m["state"] for m in self.db.marks(mid, DRAGON2, period)} if mid else {}
        done = [d for d in self.db.dragons(DRAGON2, period, page, alive=False) if d["catcher"]][-3:]
        for i, d in enumerate(done):
            o = 4 + 68 * i
            state = 8 if d["catcher"] == mid else 9
            if d["catcher"] != mid and d["id"] not in mine:
                state = 9
            catcher = self.db.member(d["catcher"])
            info = self.db.store_info(catcher["store"] or 0) if catcher else {}
            struct.pack_into("<IxBB", w, o, 1, d["kind"] % 10, state)
            w[o + 20] = info.get("country", 0)
            if catcher:
                w[o + 48:o + 64] = profile.name(bytes(catcher["profile"]))[:15].ljust(16, b"\0")
            struct.pack_into("<I", w, o + 64, d["caught"] or 0)
        self.send(0x2A2, bytes(w))


HANDLERS = {
    0x411: Cabinet.on_online, 0x413: Cabinet.on_online_done,
    0x132: Cabinet.on_book, 0x133: Cabinet.on_book, 0x135: Cabinet.on_book, 0x136: Cabinet.on_book,
    0x139: Cabinet.on_store_info,
    0x211: Cabinet.on_id_query, 0x213: Cabinet.on_sign_up, 0x215: Cabinet.on_member_login,
    0x218: Cabinet.on_logout, 0x219: Cabinet.on_sign_up_dropped, 0x21A: Cabinet.on_continue,
    0x21B: Cabinet.on_profile, 0x221: Cabinet.on_profile_byte, 0x222: Cabinet.on_profile_byte,
    0x223: Cabinet.on_profile_byte, 0x231: Cabinet.on_play_log,
    0x232: Cabinet.on_rank_upload, 0x234: Cabinet.on_rank_place,
    0x271: Cabinet.on_boards, 0x273: Cabinet.on_ticker,
    0x281: Cabinet.on_dragon_list, 0x283: Cabinet.on_dragon, 0x286: Cabinet.on_dragon, 0x289: Cabinet.on_dragon,
    0x285: Cabinet.on_dragon_damage, 0x288: Cabinet.on_dragon_damage, 0x28B: Cabinet.on_dragon_damage,
    0x28C: Cabinet.on_dragon_drop, 0x2A6: Cabinet.on_dragon_drop,
    0x2A1: Cabinet.on_dragon2_page, 0x2A3: Cabinet.on_dragon2, 0x2A5: Cabinet.on_dragon2_damage,
    0x2A7: Cabinet.on_dragon2_entry,
}
for _c in LIST_CMDS:
    HANDLERS[_c] = Cabinet.on_rank_list
for _c in NAME_CMDS:
    HANDLERS[_c] = Cabinet.on_rank_names
for _c in list(OLD60_CMDS) + list(OLD68_CMDS):
    HANDLERS[_c] = Cabinet.on_rank_old


class Patch:
    """pcli's patch server (port 9998): always "no update".  The loaders do
    not run pcli; this answers it when the cabinet's own client is pointed here."""

    def __init__(self, link):
        self.link = link
        self.ab = (0, 0)

    async def run(self):
        try:
            while True:
                cmd, data = message.decode(await self.link.recv())
                log.debug("patch -> 0x%03X %s", cmd, bytes(data).hex())
                if cmd == 0x311:
                    self.ab = (random.getrandbits(32), random.getrandbits(32))
                    self.link.send(message.encode(0x312, struct.pack("<II", *self.ab)))
                elif cmd == 0x313:
                    self.link.send(message.encode(0x331, struct.pack("<HH", 0, 0)))   # nothing newer
                elif cmd == 0x332:
                    self.link.send(message.encode(0x333, struct.pack("<b", -1)))       # no patch to fetch
                await self.link.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, FrameError,
                message.MessageError):
            pass
        finally:
            self.link.close()


class Server:
    def __init__(self, db_path, config=None):
        self.db = DB(db_path)
        self.config = config or Config()
        self._cabinets = set()

    def cabinets(self):
        return [c for c in self._cabinets if c.state == "online"]

    def attach(self, cab):
        self._cabinets.add(cab)

    def drop(self, cab):
        self._cabinets.discard(cab)

    def playing(self, mid, cab, p):
        """logged in anywhere but cab's player p"""
        return any(m == mid and (c is not cab or i != p) for c in self.cabinets() for i, m in enumerate(c.slots))

    def reserved_elsewhere(self, cab, login):
        return any(r == login for c in self.cabinets() if c is not cab for r in c.reserved)

    async def handle(self, reader, writer):
        await Cabinet(self, KoTCP(reader, writer, max_frame=message.MAX_FRAME)).run()

    async def handle_patch(self, reader, writer):
        await Patch(KoTCP(reader, writer, max_frame=message.MAX_FRAME)).run()

    # ---- dragons ----
    def caught(self, dragon, mid, board):
        self.db.catch_dragon(dragon["id"], mid)
        log.info("dragon %d (%s) caught by member %s", dragon["id"], dragon["name"], mid)
        if mid:
            self.db.mark(mid, board, dragon["period"], dragon["cls"], dragon["kind"], 8, dragon["id"])
        for other in self.db.fighters(dragon["id"]):
            if other != mid:
                self.db.mark(other, board, dragon["period"], dragon["cls"], dragon["kind"], 9, dragon["id"])

    # ---- event rewards (invented: events.json) ----
    def rewards(self, mid, b):
        ev, active = self.config.events, self.db.setting("active")
        data = ev.data
        for k in HOLIDAYS:
            if not ev.running(k, active):
                continue
            total = profile.get(b, "holiday_cursors")
            hits = total - self.db.baseline(mid, "holiday:%d:%s" % (k, ev.period(k)), total)
            field = data["holiday_titles"]["fields"][str(k)]
            for i, n in enumerate(data["holiday_titles"]["counts"]):
                if hits >= n:
                    b = profile.grant_title(b, field, i)
        for board, kind in CONTEST_RANK.items():
            if not ev.running(board, active):
                continue
            place = self.db.place(kind, 0, ev.period(board), mid)
            if not place:
                continue
            field = data["contest_titles"]["fields"][str(board)]
            for i, n in enumerate(data["contest_titles"]["places"][str(board)]):
                if place <= n:
                    b = profile.grant_title(b, field, i)
            cursor = data.get("contest_cursor", {}).get(str(board))
            if cursor is not None:
                b = profile.grant_cursor(b, cursor)
        if ev.running(DRAGON, active):
            cfg = data["dragons"]
            caught = [0] * 5
            for mk in self.db.marks(mid, DRAGON, ev.period(DRAGON)):
                caught[mk["cls"] % 5] += mk["state"] == 8
            for i, n in enumerate(cfg["titles"]["caught"]):
                if max(caught) >= n:
                    b = profile.grant_title(b, cfg["titles"]["field"], i)
            full = sum(1 for c in caught if c >= cfg["titles"]["caught"][0])
            for song in cfg["songs"][:full * 2 - (1 if full else 0)]:
                b = profile.grant_song(b, song)
        return b

    # ---- admin (JSON lines on a local port) ----
    async def admin(self, reader, writer):
        try:
            req = json.loads(await reader.readline() or b"{}")
            res = self.admin_cmd(req)
        except Exception as e:
            res = {"error": str(e)}
        writer.write((json.dumps(res, ensure_ascii=False) + "\n").encode())
        await writer.drain()
        writer.close()

    def _targets(self, req):
        cabs = self.cabinets()
        if req.get("store"):
            cabs = [c for c in cabs if c.store == int(req["store"])]
        return cabs

    def admin_cmd(self, req):
        cmd = req.get("cmd")
        if cmd == "online":
            return {"cabinets": [{"store": c.store, "members": c.slots} for c in self.cabinets()]}
        cabs = self._targets(req)
        if not cabs:
            return {"error": "no cabinet online" + (" with that store" if req.get("store") else "")}
        for c in cabs:
            if cmd == "notice":
                c.send(0x416, self.config.events.notice(req.get("text")))
            elif cmd == "time":
                c.push_time()
            elif cmd == "level":
                c.send(0x138, bytes((int(req["level"]) & 0xFF,)))
            elif cmd == "boards":
                c.send(0x272, self.config.events.boards(self.db.setting("active")))
                c.send(0x274, self.config.events.tickers(self.db.setting("active")))
            elif cmd == "storeinfo":
                info = self.db.store_info(c.store)
                d = bytearray(36)
                struct.pack_into("<BBHI", d, 0, info.get("country", 0), info.get("province", 0),
                                 info.get("region", 0), info.get("store_no", c.store))
                name = bytes.fromhex(req["name"]) if req.get("name") else bytes.fromhex(info.get("name", ""))
                d[8:32] = name[:23].ljust(24, b"\0")
                d[32] = int(req.get("coins", info.get("coins", 1)))
                c.send(0x139, bytes(d))
            elif cmd == "word":
                c.send(0x137, struct.pack("<H", int(req["value"])))
            else:
                return {"error": "unknown command"}
            asyncio.ensure_future(c.link.drain())
        return {"ok": "%s sent to store %s" % (cmd, ", ".join(str(c.store) for c in cabs))}


async def serve(host, port, db_path, admin_port=None, config=None, patch_port=None):
    srv = Server(db_path, config)
    servers = [await asyncio.start_server(srv.handle, host, port)]
    log.info("Percussion Master 3 server on %s:%d, database %s", host, port, db_path)
    if patch_port:
        servers.append(await asyncio.start_server(srv.handle_patch, host, patch_port))
        log.info("patch server (no update) on %s:%d", host, patch_port)
    if admin_port:
        servers.append(await asyncio.start_server(srv.admin, "127.0.0.1", admin_port))
        log.info("admin on 127.0.0.1:%d", admin_port)
    await asyncio.gather(*(x.serve_forever() for x in servers))
