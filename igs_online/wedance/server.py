"""We Dance Online 2 game server (KoNet)."""
import asyncio
import datetime
import json
import logging
import os
import struct
import time

from ..common.kotcp import KoTCP, FrameError
from ..common.packet import Reader, Writer
from . import konet, profile, playlog
from . import market as market_mod
from .db import DB, FIRST_UID
from .konet import PLAIN, AES_MODE, ZLIB
from .luckybag import LuckyBag
from .missions import Missions
from .world import World

log = logging.getLogger("wedance")

NAMES = {
    0x11: "login", 0x13: "login hmac", 0x21: "register", 0x23: "register hmac",
    0x131: "rank fight", 0x134: "rank store", 0x137: "rank song", 0x13B: "rank popular", 0x13D: "rank won",
    0x141: "rank coop", 0x143: "rank combo", 0x145: "rank song coop", 0x147: "rank exp",
    0x149: "rank cont combo",
    0x151: "store coin", 0x152: "cable broken", 0x153: "store info", 0x154: "ping data",
    0x161: "game value", 0x162: "game value", 0x163: "csvlist info query", 0x164: "csvlist info",
    0x165: "ticker", 0x167: "notice", 0x169: "conditions", 0x16B: "csvlist", 0x16D: "mini update done",
    0x16E: "mini update", 0x16F: "file", 0x171: "profit query", 0x172: "profit",
    0x211: "player login", 0x213: "logout", 0x214: "worn clothes", 0x215: "create account",
    0x217: "create info", 0x219: "update info", 0x21B: "password", 0x21C: "transfer", 0x21E: "qr login",
    0x222: "search fighters local", 0x223: "search fighters", 0x225: "vs info",
    0x231: "contest open", 0x232: "contest close", 0x233: "challenge", 0x234: "accept",
    0x235: "reject", 0x236: "continue", 0x237: "challenged", 0x241: "song cursor", 0x242: "song decide",
    0x243: "third song", 0x250: "fight relay", 0x251: "ready", 0x252: "start", 0x253: "end",
    0x255: "vs clothes",
    0x261: "song result", 0x262: "song rank", 0x263: "count", 0x264: "world rank", 0x265: "clothes",
    0x267: "song played", 0x268: "fight result", 0x269: "play log", 0x26A: "combo result", 0x26B: "items",
    0x26C: "combo rank", 0x26D: "pet", 0x26E: "mission", 0x271: "market", 0x273: "lucky bag",
    0x275: "weticket",
}

# cmds relayed to the versus peer as they are
RELAY = {0x236, 0x241, 0x242, 0x243, 0x250, 0x251, 0x253, 0x255}

# player boards: query -> (profile key, this month only, entry layout).
# PDTShowRanking pages 5..8 show 0x132 对战总排行 (fight, all time),
# 0x13E 对战月排行 (fight, this month), 0x142 合作总排行 (co-op, all time),
# 0x144 合作月排行 (co-op, this month), with 52-byte entries; pages 3 and 4
# 0x148 组曲月排行 (suite score, this month) and 0x14A 组曲连段月排行 (suite
# best combo, this month), 49-byte entries.
BOARDS = {
    0x131: ("fight_score", False, 52), 0x13D: ("fight_score", True, 52),
    0x141: ("coop_score", False, 52), 0x143: ("coop_score", True, 52),
    0x147: ("combo_total", True, 49), 0x149: ("combo_best", True, 49),
}


def month_key(t=None):
    return time.strftime("%Y-%m", time.localtime(t))


def month_start():
    d = datetime.date.today().replace(day=1)
    return int(time.mktime(d.timetuple()))


def week_start(weeks_ago=0):
    d = datetime.date.today()
    d -= datetime.timedelta(days=d.weekday() + 7 * weeks_ago)
    return int(time.mktime(d.timetuple()))


def add_month(p, key, value, best=False):
    """count into this month's board values (p["months"]["YYYY-MM"])"""
    months = p.setdefault("months", {})
    m = months.setdefault(month_key(), {})
    m[key] = max(m.get(key, 0), value) if best else m.get(key, 0) + value
    for k in sorted(months)[:-3]:
        del months[k]

RANK_DAYS = 30                          # store and popularity boards: plays of the last 30 days


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


def name_bytes(p):
    return bytes.fromhex(p.get("name") or "") if p else b""


def _json(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Config:
    def __init__(self, store_name=("IGS", "Online"), ticker="", notice="", time_offset=0, world=None,
                 luckybag=None, missions=None):
        """luckybag, missions: data file paths replacing data/luckybag.json, data/missions.json"""
        self.store_name = store_name
        self.ticker = ticker
        self.notice = notice
        self.time_offset = time_offset
        self.world = world or World()
        self.market = market_mod.Catalogue()
        self.luckybag = LuckyBag(_json(luckybag))
        self.missions = Missions(_json(missions))


class Cabinet:
    """one connected cabinet (one TCP link)"""

    def __init__(self, server, kotcp):
        self.server = server
        self.db = server.db
        self.config = server.config
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
        self.vs_mode = 2                # 1 co-op, 2 fight: the contest's, once two cabinets pair up

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
        if len(data) >= 42:
            # store province, names (ime_rGetStoreName), versions, coin settings, channel
            self.db.set_store_info(sid, name1=data[5:15].split(b"\0")[0], name2=data[15:31].split(b"\0")[0],
                                   login=dict(zip(("province", "net", "area", "soft", "coin", "credit",
                                                   "channel", "port"),
                                                  (data[4],) + struct.unpack_from("<HHHBBHB", data, 31))))
        w = self.config.world
        self.challenge = os.urandom(8)
        now = time.time() + self.config.time_offset
        r = Writer().raw(self.challenge).i32(int(now)).i32(int(now % 1 * 1e6))
        r.u16(0)                        # word_879043C
        r.u16(w["feature"])             # g_wFeature
        r.u16(w.seed()).u8(w.level())   # world tour seed and table
        r.u8(w["ping_test"])
        r.u8(w["signup_reminder"])      # Acropass sign-up reminder
        self.send(0x12, AES_MODE, r.bytes())
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
        self.push_popular()
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

    def store_names(self, sid=None):
        info = self.db.store_info(sid if sid is not None else self.store)
        if info.get("name1") or info.get("name2"):
            return bytes(info.get("name1") or b""), bytes(info.get("name2") or b"")
        a, b = self.config.store_name
        return a.encode("latin-1"), b.encode("latin-1")

    def login_profile(self, p):
        """the profile as sent: world values, ranks, group and mission blocks"""
        q = dict(p)
        q["activity_rate"] = self.config.world.activity_rate()
        q["feature"] = self.config.world["feature"]
        q["world_rank"] = self.server.rank_of(p["uid"], "fight_score")
        q["coop_rank"] = self.server.rank_of(p["uid"], "coop_score")
        gid = self.db.group_of(p["uid"])
        q["group"], q["group_block"] = gid, self.server.group_block(gid)
        if p.get("uid", 0) >= FIRST_UID:
            q["mission"], q["mission_block"] = self.config.missions.block(p)
        return q

    def on_player_login(self, mode, data):
        if len(data) == 1:                       # guest
            p = profile.new_profile(0)
            p["blocks"]["talks"] = ""
            p["activity_rate"] = self.config.world.activity_rate()
            p["feature"] = self.config.world["feature"]
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
        other = self.server.cabinet_of(p["uid"])
        if other and other is not self:
            log.info("%s: login %s: already in at %s", self.name, konet.account_digits(account), other.name)
            self.send(0x212, AES_MODE, b"\xfd")
            return
        self.login(p, 0x212)

    def login(self, p, cmd, feature=0):
        p["logins"] = (p.get("logins", 0) + 1) & 0xFFFF
        p["store"] = self.store
        if self.db.pending(p["uid"], "transfer"):
            feature |= profile.UF_TRANSFER
        p["user_feature"] = feature
        self.config.missions.current(p)
        self.db.save(p)
        self.player = p
        self.last_uid = p["uid"]
        self.send(cmd, AES_MODE, profile.build(self.login_profile(p)))
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
        p["store"] = self.store
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

    def on_transfer(self, mode, data):
        """0x21C (PDTLogin::login_iArkopaze, shown after a login with user feature 0x80):
        u8 0 new account / 1 existing account, u64 account, u64 password -> 0x21D"""
        r = Reader(data)
        kind, account, password = r.u8(), r.u64(), r.u64()
        p = self.current()
        digits = konet.account_digits(account)
        if not p:
            self.send(0x21D, AES_MODE, b"\xfd")
            return
        if kind == 0:
            if self.db.account_exists(account):
                log.info("%s: transfer to new account %s: taken", self.name, digits)
                self.send(0x21D, AES_MODE, b"\xff")
                return
        else:
            old, pw = self.db.player_by_account(account)
            if old is None or old["uid"] == p["uid"]:
                log.info("%s: transfer to account %s: no such account", self.name, digits)
                self.send(0x21D, AES_MODE, b"\xff")
                return
            if pw != password:
                log.info("%s: transfer to account %s: wrong password", self.name, digits)
                self.send(0x21D, AES_MODE, b"\xfe")
                return
            # the old account's balances move over; its number now logs in to this profile
            p["weticket"] = p.get("weticket", 0) + old.get("weticket", 0)
            p["crystal"] = p.get("crystal", 0) + old.get("crystal", 0)
            for s in old.get("songs", []):
                if s not in p["songs"]:
                    p["songs"].append(s)
            self.db.delete_player(old["uid"])
        self.db.set_account(p["uid"], account, password)
        p["account"] = account
        p["user_feature"] = (p.get("user_feature", 0) & ~profile.UF_TRANSFER) | profile.UF_FROM_OTHERS
        self.db.clear_pending(p["uid"], "transfer")
        self.save(p)
        self.send(0x21D, AES_MODE, Writer().str(bytes.fromhex(p["card_id"]), 20).i32(p.get("weticket", 0))
                  .u16(p["user_feature"]).bytes())
        log.info("%s: uid %d transferred to %s account %s", self.name, p["uid"], ("a new", "the")[kind], digits)

    # ---- results ----
    def _update(self, fn, data, what):
        p = self.current()
        if not p:
            log.info("%s: %s without a member", self.name, what)
            return None
        fn(p, data)
        self.save(p)
        log.info("%s: uid %d %s saved", self.name, p["uid"], what)
        return p

    def on_count(self, mode, data): self._update(profile.apply_count, data, "counters")
    def on_clothes(self, mode, data): self._update(profile.apply_clothes, data, "wardrobe")
    def on_worn(self, mode, data): self._update(profile.apply_worn, data, "worn clothes")
    def on_pet(self, mode, data): self._update(profile.apply_pet, data, "pet")

    def on_fight(self, mode, data):
        def f(p, d):
            old = {k: p.get(k, 0) for k in ("fight_score", "coop_score")}
            profile.apply_fight(p, d)
            for k, v in old.items():
                if p.get(k, 0) > v:
                    add_month(p, k, p[k] - v)
        p = self._update(f, data, "fight result")
        if p:
            # 0x264: u32 uid, u16 world rank (co-op rank in co-op)
            key = "coop_score" if self.vs_mode == 1 else "fight_score"
            self.send(0x264, PLAIN, Writer().i32(p["uid"]).u16(min(999, self.server.rank_of(p["uid"], key))).bytes())

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
        if gmode == 1 and self.peer and self.peer.player and uid >= FIRST_UID:
            # co-op pairs: the second of the two results adds the pair's row
            peer_uid = self.peer.player.get("uid", 0)
            peer = self.server.last_scores.pop(peer_uid, None)
            if peer_uid >= FIRST_UID and peer and peer[0] == song:
                self.db.add_coop(song, uid, peer_uid, score + peer[1], diff)
            else:
                self.server.last_scores[uid] = (song, score)
        log.info("%s: uid %d song %d diff %d score %d%s", self.name, uid, song, diff, score, " FC" if fc else "")
        if uid >= FIRST_UID:
            # 0x262: u32 uid, u16 song, u8 place on the song (shown on the result screen)
            rank = self.db.song_rank(song, uid, month_start())
            self.send(0x262, PLAIN, Writer().i32(uid).u16(song).u8(min(rank, 255)).bytes())

    def on_song_played(self, mode, data):
        song = Reader(data).u16()
        p = self.current() or self.player
        self.db.add_play(p["uid"] if p else 0, self.store, song)

    def on_combo(self, mode, data):
        """0x26A: i32 total, i32 best combo, u8 (1: no NPC)"""
        r = Reader(data)
        total, best = r.i32(), r.i32()
        p = self.current()
        if not p:
            return
        p["combo_total"] = max(p.get("combo_total", 0), total)
        p["combo_best"] = max(p.get("combo_best", 0), best)
        add_month(p, "combo_best", best, best=True)
        add_month(p, "combo_total", total, best=True)
        self.save(p)
        ps = [q for _, _, q in self.db.players()]
        place = lambda k: 1 + sum(1 for q in ps if q.get(k, 0) > p.get(k, 0))
        # 0x26C: u32 uid, u16 suite rank, u16 combo rank
        self.send(0x26C, PLAIN, Writer().i32(p["uid"]).u16(min(999, place("combo_total")))
                  .u16(min(999, place("combo_best"))).bytes())
        log.info("%s: uid %d suite %d, best combo %d", self.name, p["uid"], total, best)

    def on_raw(self, mode, data, cmd=None):
        p = self.current()
        self.db.add_result(p["uid"] if p else 0, self.store, cmd, mode, data)

    def on_play_log(self, mode, data):
        uid = struct.unpack_from("<i", data)[0] if len(data) >= 4 else 0
        self.db.add_result(uid, self.store, 0x269, mode, data)
        try:
            entry = playlog.parse(data)
        except Exception as e:
            log.warning("%s: play log of uid %d: %s", self.name, uid, e)
            return
        p = self.db.player(uid) if uid >= FIRST_UID else None
        if p:
            p["last_log"] = {k: v for k, v in entry.items() if not k.startswith(("b", "w", "i"))}
            p["credits"] = p.get("credits", 0) + 1
            self.save(p)
        counter = playlog.group_counter(entry)
        gid = self.db.group_of(uid) if uid >= FIRST_UID else 0
        if counter and gid:
            self.db.count_group(gid, counter)
        log.info("%s: play log of uid %d (%d bytes, mode %d%s)", self.name, uid, len(data), entry["mode"],
                 ", group %d %s" % (gid, counter) if counter and gid else "")

    def on_mission(self, mode, data):
        r = Reader(data)
        mid, passed, song, a, b = r.i32(), r.u8(), r.u16(), r.i32(), r.i32()
        p = self.current()
        if not p:
            return
        text = self.config.missions.result(p, mid, passed, song, a, b)
        self.save(p)
        log.info("%s: uid %d %s", self.name, p["uid"], text)

    def on_game_value(self, mode, data):
        """0x161 (PDTContinue): u8 0, i32 mission -> 0x162: u16 activity rate, u8 mission still open"""
        r = Reader(data)
        r.u8()
        mid = r.i32()
        p = self.current()
        ok = bool(p and mid > 0 and self.config.missions.valid(p, mid))
        self.send(0x162, AES_MODE, Writer().u16(self.config.world.activity_rate()).u8(1 if ok else 0).bytes())

    def on_market(self, mode, data):
        p = self.current()
        if not p:
            self.send(0x272, AES_MODE, b"\xff")
            return
        t = market_mod.parse(data)
        refused, note = market_mod.check(self.config.market, p, t)
        if refused:
            log.info("%s: uid %d market op %d %s refused: %s", self.name, p["uid"], t["op"], t["code"], refused)
            self.send(0x272, AES_MODE, b"\xfe")
            return
        what = market_mod.apply(p, t)
        self.save(p)
        log.info("%s: uid %d market %s %s (%s); crystals %d, WeTickets %d", self.name, p["uid"], what, t["code"],
                 note or "", p["crystal"], p["weticket"])
        self.send(0x272, AES_MODE, b"\0")

    def on_lucky_bag(self, mode, data):
        """0x273: u8 0, u16 flags (bit 0 has this week's song, bit 1 this month's)
        -> 0x274: i8 0, u16 L, the draw (luckybag.py); i8 other: failed"""
        r = Reader(data)
        r.u8()
        flags = r.u16()
        p = self.current()
        if not p:
            self.send(0x274, AES_MODE, b"\xff")
            return
        text = self.config.luckybag.draw(flags, p.get("cap_luckybag", 0))
        p.setdefault("lucky_bags", []).append(text)
        p["lucky_bags"] = p["lucky_bags"][-50:]
        self.save(p)
        self.send(0x274, AES_MODE, Writer().i8(0).u16(len(text)).raw(text.encode()).bytes())
        log.info("%s: uid %d lucky bag: %s", self.name, p["uid"], text)

    def on_weticket(self, mode, data):
        def f(p, d):
            p["weticket"] = Reader(d).i32()
        self._update(f, data, "WeTickets")

    # ---- store bookkeeping ----
    def on_store_coin(self, mode, data):
        r = Reader(data)
        coin = {"coins": r.u8(), "credits": r.u8()}
        self.db.set_store_info(self.store, coin=coin)
        log.info("%s: coin settings %d coin(s) per %d credit(s)", self.name, coin["coins"], coin["credits"])

    def on_store_text(self, mode, data):
        r = Reader(data)
        text = r.raw(r.u16()).decode("gbk", "replace")
        self.db.set_store_info(self.store, info=text)
        log.info("%s: store info %r", self.name, text)

    def on_book(self, mode, data, cmd=None):
        self.db.add_book(self.store, cmd, data)
        log.info("%s: %s (%d bytes) kept", self.name, NAMES.get(cmd, hex(cmd)), len(data))

    def on_mini_update(self, mode, data):
        self.send(0x16D, AES_MODE, b"\0")         # no update (0x16F would write a file)

    # ---- info, rankings ----
    def on_ticker(self, mode, data):
        text = self.config.ticker or self.config.world["ticker"] or " "
        self.send(0x166, AES_MODE, text.encode("gbk", "replace")[:511])

    def on_notice(self, mode, data):
        text = self.config.notice or self.config.world["notice"] or " "
        self.send(0x168, AES_MODE, text.encode("gbk", "replace")[:1023])

    def on_conditions(self, mode, data):
        w = Writer()
        cs = self.config.world.conditions()[:255]
        w.u8(len(cs))
        for t, text in cs:
            b = text.encode("gbk", "replace")[:511]
            w.u8(t).u16(len(b)).raw(b)
        self.send(0x16A, AES_MODE, w.bytes())

    def on_csvlist(self, mode, data):
        self.send(0x16C, AES_MODE, b"\0")         # keep the cabinet's own list
        self.send(0x163, AES_MODE, b"\0")         # and report its songs' flags (0x164)

    def push_popular(self):
        """0x13B: u8 n, n x (u16 song id, bit 15 = falling; i8 change); the game takes it
        only when n equals the entries and its song list has 1..200 songs"""
        rows = self.db.popular(time.time() - RANK_DAYS * 86400, 200)
        w = Writer().u8(len(rows))
        for r in rows:
            w.u16(r["song"]).i8(0)
        self.send(0x13B, ZLIB, w.bytes())

    def on_rank_song(self, mode, data):
        """0x137 -> 0x138 (个人单曲月排行): this month's best members' scores on a song (7 at most)"""
        song = Reader(data).u16()
        rows = self.db.song_top(song, 7, month_start())
        w = Writer().u16(song).u8(0).u16(0).u16(0)
        for row in rows:
            p = self.db.player(row["uid"])
            w.str(name_bytes(p), 14).i32(row["score"])
            a, b = self.store_names(p.get("store", self.store))
            w.str(a, 10).str(b, 16)
        self.send(0x138, ZLIB, w.bytes())

    def on_rank_song_coop(self, mode, data):
        """0x145 -> 0x146: u16 song, u16, u16, [16], [16], pairs (name, name, i32 score, u8, u8)"""
        song = Reader(data).u16()
        w = Writer().u16(song).u16(0).u16(0).str(b"", 16).str(b"", 16)
        for row in self.db.coop_top(song, 7, month_start()):
            a, b = self.db.player(row["uid1"]), self.db.player(row["uid2"])
            w.str(name_bytes(a), 14).str(name_bytes(b), 14).i32(row["score"]).u8(row["diff"] or 0).u8(0)
        self.send(0x146, ZLIB, w.bytes())

    def _avatar(self, w, ps):
        if not ps:
            w.u8(0).u8(0).u8(0).raw(b"\0" * 10)
            return
        p = ps[0]
        w.u8(1).u8(p.get("skin", 0)).u8(p.get("face", 0))
        for k in ("head", "upbody", "downbody", "feet", "back"):
            w.u16(worn(p, k)[0])

    def on_rank_players(self, cmd, data):
        """0x131/0x13D/0x141/0x143 -> +1: 13-byte avatar of #1, 52-byte entries;
        0x147/0x149 -> +1: the avatar, 49-byte entries"""
        key, month, size = BOARDS[cmd]
        rows = self.server.ranked(key, 30, month)
        w = Writer()
        self._avatar(w, [p for p, _ in rows])
        for p, value in rows:
            a, b = self.store_names(p.get("store", self.store))
            w.u8(p.get("sex", 0)).u16(p.get("level", 1)).u16(p.get("title", 33))
            w.str(name_bytes(p), 14)
            if size == 52:
                w.u16(0).u8(0).str(a, 10).str(b, 16).i32(value)
            else:
                w.i32(value).str(a, 10).str(b, 16)
        self.send(cmd + 1, ZLIB, w.bytes())

    def on_rank_store(self, mode, data):
        """0x134 -> 0x135 (店家排行): i32 this store's plays (累计人气), u8 its place last
        week (上週), u8 this week (进榜); stores by this week's plays: u8 province,
        area[10], name[16], u16 plays (本週人气), i8 places gained since last week"""
        this = [r["store"] for r in self.db.store_plays(week_start()) if r["store"]]
        last = [r["store"] for r in self.db.store_plays(week_start(1)) if r["store"]]
        rows = [r for r in self.db.store_plays(week_start()) if r["store"]][:30]
        total = sum(r["n"] for r in self.db.store_plays() if r["store"] == self.store)
        place = lambda lst, s: lst.index(s) + 1 if s in lst else 0
        w = Writer().i32(total).u8(min(255, place(last, self.store))).u8(min(255, place(this, self.store)))
        for i, r in enumerate(rows):
            info = self.db.store_info(r["store"])
            a, b = self.store_names(r["store"])
            before = place(last, r["store"])
            w.u8(((info.get("login") or {}).get("province", 0)) % 36).str(a, 10).str(b, 16)
            w.u16(min(r["n"], 0xFFFF)).i8(max(-99, min(99, before - (i + 1))) if before else 0)
        self.send(0x135, ZLIB, w.bytes())

    # ---- versus ----
    def vs_entry(self):
        """15-byte fighter list entry (0x221 / 0x224)"""
        p = self.player
        info = self.db.store_info(self.store)
        return (Writer().i32(p["uid"]).u16(p.get("level", 1)).u8(0)
                .u8(((info.get("login") or {}).get("province", 0)) % 36)
                .u8(2 if not self.contest or self.contest[1] != 1 else 1)
                .u16(p.get("won_combo", 0)).u16(p.get("strength", 0)).u8(0).u8(0).bytes())

    def vs_info(self, p):
        """144-byte profile (0x226, 0x237)"""
        w = Writer().i32(p["uid"]).u8(p.get("sex", 0)).u8(p.get("skin", 0)).u8(p.get("face", 0))
        w.str(name_bytes(p), 14)
        for k in ("level", "title", "won_combo", "won", "lost"):
            w.u16(p.get(k, 0))
        w.u16(min(999, self.server.rank_of(p["uid"], "fight_score")))
        for k in ("head", "upbody", "downbody", "feet", "back"):
            item, cols = worn(p, k)
            w.u16(item).raw(cols)
        item, cols = worn(p, "element")
        w.raw(cols).u16(item)
        info = self.db.store_info(self.store)
        w.u8(((info.get("login") or {}).get("province", 0)) % 36)
        a, b = self.store_names()
        w.str(a, 10).str(b, 16)
        w.u16(self.store & 0xFFFF).u16(p.get("strength", 0)).u8(p.get("v87903BC", 0))
        w.i8(p.get("v87904A0", 0)).u8(0)
        pet = max(0, p.get("pet_current", 0) - 1)
        w.u8(pet).u16(p.get("pet%d" % pet, 0))
        w.i32(p.get("fight_score", 0)).i32(p.get("coop_score", 0))
        w.u8(1 if p.get("vip_until", 0) > time.time() else 0).u8(1 if p.get("user_feature", 0) & 1 else 0)
        gid = self.db.group_of(p["uid"])
        g = self.server.group_block(gid)
        w.i32(gid if g else 0).str(g["name"] if g else b"", 16).u16(g["level"] if g else 0)
        return w.bytes()

    def on_search(self, cmd, mode, data):
        others = [c for c in self.server.cabinets() if c is not self and c.contest and c.player and not c.peer]
        if cmd == 0x222:
            others = [c for c in others if c.store == self.store]
        body = b"".join(c.vs_entry() for c in others[:8])
        self.send(0x221 if cmd == 0x223 else 0x224, PLAIN, body)     # 0x223 global, 0x222 local
        log.info("%s: fighter search: %d", self.name, len(others))

    def on_vs_info(self, mode, data):
        uid = Reader(data).i32()
        c = self.server.cabinet_of(uid)
        self.send(0x226, ZLIB, c.vs_info(c.player) if c else b"\0")

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
            self.vs_mode = a.vs_mode = self.contest[1] if self.contest else 2
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
    0x21B: Cabinet.on_password, 0x21C: Cabinet.on_transfer,
    0x261: Cabinet.on_song_result, 0x263: Cabinet.on_count, 0x265: Cabinet.on_clothes,
    0x267: Cabinet.on_song_played, 0x268: Cabinet.on_fight, 0x269: Cabinet.on_play_log,
    0x26A: Cabinet.on_combo, 0x26B: Cabinet.on_items, 0x26D: Cabinet.on_pet, 0x26E: Cabinet.on_mission,
    0x271: Cabinet.on_market, 0x273: Cabinet.on_lucky_bag, 0x275: Cabinet.on_weticket,
    0x161: Cabinet.on_game_value,
    0x165: Cabinet.on_ticker, 0x167: Cabinet.on_notice, 0x169: Cabinet.on_conditions,
    0x16B: Cabinet.on_csvlist, 0x16E: Cabinet.on_mini_update,
    0x151: Cabinet.on_store_coin, 0x153: Cabinet.on_store_text,
    0x137: Cabinet.on_rank_song, 0x145: Cabinet.on_rank_song_coop, 0x134: Cabinet.on_rank_store,
    0x222: _bind(0x222, Cabinet.on_search), 0x223: _bind(0x223, Cabinet.on_search),
    0x225: Cabinet.on_vs_info, 0x231: Cabinet.on_contest_open, 0x232: Cabinet.on_contest_close,
    0x233: Cabinet.on_challenge,
    0x234: _bind(0x234, Cabinet.on_contest_answer), 0x235: _bind(0x235, Cabinet.on_contest_answer),
    0x252: Cabinet.on_start,
}
for _c in BOARDS:
    HANDLERS[_c] = (lambda cmd: lambda c, m, d: c.on_rank_players(cmd, d))(_c)
for _c in RELAY:
    HANDLERS[_c] = _bind(_c, Cabinet.on_relay)
for _c in (0x152, 0x154, 0x164, 0x16D, 0x172):
    HANDLERS[_c] = (lambda cmd: lambda c, m, d: c.on_book(m, d, cmd))(_c)


class Server:
    def __init__(self, db_path, config=None):
        self.db = DB(db_path)
        self.config = config or Config()
        self._cabinets = set()
        self.last_scores = {}           # uid -> (song, score) of the last 0x261

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

    # ---- rankings, groups ----
    def ranked(self, key, n, month=False):
        """[(profile, value)] best first: all time, or this month's (p["months"])"""
        rows = []
        for _, _, p in self.db.players():
            v = (p.get("months", {}).get(month_key(), {}) if month else p).get(key, 0)
            if v > 0:
                rows.append((p, v))
        rows.sort(key=lambda r: r[1], reverse=True)
        return rows[:n]

    def rank_of(self, uid, key, month=False):
        """place on a board (1..), 0 without a value"""
        for i, (p, _) in enumerate(self.ranked(key, 10000, month)):
            if p["uid"] == uid:
                return i + 1
        return 0

    def group_block(self, gid):
        """the profile's 68-byte group block (invented level rule: 1 + 20 credits a level)"""
        g = self.db.group(gid) if gid else None
        if not g:
            return None
        plays = sum(g["counters"].values())
        level = max(1, min(400, 1 + plays // 20))
        leader = self.db.player(g["leader"]) if g["leader"] else None
        return dict(g["counters"], name=g["name"], level=level, leader=name_bytes(leader),
                    members=len(g["members"]), max_members=g["max_members"] or 30, song=g["song"] or 1,
                    song_level=min(4, level // 10))

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

    def _one_cabinet(self, req):
        cabs = self.cabinets()
        if req.get("store"):
            cabs = [c for c in cabs if c.store == int(req["store"])]
        if len(cabs) != 1:
            return None, {"error": "%d cabinets match; give --store" % len(cabs),
                          "stores": [c.store for c in self.cabinets()]}
        return cabs[0], None

    def admin_cmd(self, req):
        cmd = req.get("cmd")
        if cmd == "qr":
            p, _ = self.db.player_by_account(konet.account_value(req["account"]))
            if not p:
                return {"error": "no such account"}
            c, err = self._one_cabinet(req)
            if err:
                return err
            c.login(p, 0x21E, profile.UF_QR_LOGIN)
            asyncio.ensure_future(c.link.drain())
            return {"ok": "pushed uid %d to store %d" % (p["uid"], c.store)}
        if cmd == "profit":
            c, err = self._one_cabinet(req)
            if err:
                return err
            c.send(0x171, AES_MODE, b"\0")       # the cabinet answers with 0x172
            asyncio.ensure_future(c.link.drain())
            return {"ok": "asked store %d for its bookkeeping" % c.store}
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
