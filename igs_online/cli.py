"""python -m igs_online <game> [command] ...

Settings come from flags or the environment (IGS_ONLINE_LISTEN, IGS_ONLINE_DB,
IGS_ONLINE_ADMIN, IGS_ONLINE_LOG).
"""
import argparse
import asyncio
import datetime
import json
import logging
import os
import socket
import struct
import sys
import time


def _hostport(s, default_port):
    host, _, port = s.rpartition(":")
    if not host:
        return s, default_port
    return host, int(port)


def _admin(port, req):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall((json.dumps(req) + "\n").encode())
            res = json.loads(s.makefile(encoding="utf-8").readline())
    except OSError as e:
        print("no server on admin port %d (%s)" % (port, e))
        return 1
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 1 if "error" in res else 0


def _name(s):
    """a player name as the game stores it (GBK, 14 bytes at most)"""
    b = s.encode("gbk", "replace")[:14]
    return b


def _text(b):
    """store names (Big5, the cabinet's storename.dat)"""
    b = bytes(b or b"")
    for enc in ("big5", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.hex()


def _date(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "-"


def _book(cmd, data):
    """0x172 (31 days x 28 bytes, 12 months x 12 bytes) and other bookkeeping, readable"""
    data = bytes(data)
    if cmd == 0x172 and len(data) == 1012:
        days = []
        for d in range(31):
            v = struct.unpack_from("<7I", data, 28 * d)
            if not any(v):
                continue
            days.append({"day": d + 1, "players": v[3] >> 16, "game_seconds": v[4], "card_logins": v[5] & 0xFFFF,
                         "simple_players": v[6] >> 16, "continues": v[2] >> 11 & 0x3FF,
                         "fights": v[5] >> 17 & 0xFF,
                         "raw": ["%08x" % x for x in v]})
        months = []
        for m in range(12):
            v = data[868 + 12 * m:880 + 12 * m]
            if any(v):
                months.append({"month": m + 1, "players": struct.unpack_from("<H", v, 6)[0],
                               "simple_players": struct.unpack_from("<H", v, 10)[0], "raw": v.hex()})
        return {"days": days, "months": months}
    if cmd == 0x152 and len(data) >= 6:
        a, b = struct.unpack_from("<iH", data)
        return {"value": a, "word": b}
    if cmd == 0x164 and len(data) >= 2:
        n = struct.unpack_from("<H", data)[0]
        flags = struct.unpack_from("<%dH" % n, data, 2) if len(data) >= 2 + 2 * n else ()
        return {"songs": n, "flagged": [i for i, f in enumerate(flags) if f]}
    if cmd == 0x16D and len(data) >= 2:
        return {"minor_version": struct.unpack_from("<H", data)[0]}
    return {"hex": data.hex()}


def wedance(argv):
    from .wedance import konet, profile
    from .wedance.db import DB
    from .wedance.server import Config, serve

    env = os.environ.get
    ap = argparse.ArgumentParser(prog="python -m igs_online wedance",
                                 description="We Dance Online 2 server")
    ap.add_argument("--listen", default=env("IGS_ONLINE_LISTEN", "127.0.0.1:10000"),
                    help="host:port to listen on (default 127.0.0.1:10000)")
    ap.add_argument("--db", default=env("IGS_ONLINE_DB", "wedance.db"), help="SQLite file (default wedance.db)")
    ap.add_argument("--admin", type=int, default=int(env("IGS_ONLINE_ADMIN", "10002")),
                    help="local admin port, 0 = none (default 10002)")
    ap.add_argument("-v", "--verbose", action="count", default=int(env("IGS_ONLINE_LOG", "0")))
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the server (default)")
    s.add_argument("--store-name", default="IGS,Online",
                   help="store name for cabinets that send none: first,second")
    s.add_argument("--ticker", default="", help="news ticker text (default: the world file's)")
    s.add_argument("--world", default=env("IGS_ONLINE_WORLD", ""), help="world events file (default data/world.json)")
    s.add_argument("--luckybag", default=env("IGS_ONLINE_LUCKYBAG", ""),
                   help="lucky bag table (default data/luckybag.json)")
    s.add_argument("--missions", default=env("IGS_ONLINE_MISSIONS", ""),
                   help="mission set (default data/missions.json)")
    a = sub.add_parser("add", help="create a member account")
    a.add_argument("account", help="keypad account number, 6..11 digits")
    a.add_argument("password", help="keypad password, 6..11 digits")
    a.add_argument("--name", default="", help="player name (up to 14 bytes)")
    rn = sub.add_parser("rename", help="set a member's name")
    rn.add_argument("account")
    rn.add_argument("name")
    sub.add_parser("list", help="list stores and members")
    q = sub.add_parser("qr", help="QR login: push a member to a cabinet waiting on the QR screen")
    q.add_argument("account")
    q.add_argument("--store", type=int, help="store id (needed when several cabinets are online)")
    pr = sub.add_parser("profit", help="ask a connected cabinet for its bookkeeping (0x171 -> 0x172)")
    pr.add_argument("--store", type=int)
    sub.add_parser("online", help="list connected cabinets")
    sh = sub.add_parser("show", help="print a member's stored profile")
    sh.add_argument("account")
    gv = sub.add_parser("give", help="add crystals / WeTickets, set VIP days")
    gv.add_argument("account")
    gv.add_argument("--crystal", type=int, default=0)
    gv.add_argument("--weticket", type=int, default=0)
    gv.add_argument("--vip-days", type=int, help="VIP for this many days from now (0 = off)")
    tr = sub.add_parser("transfer", help="ask a member to transfer the account at the next login (0x21C)")
    tr.add_argument("account")
    tr.add_argument("--off", action="store_true")
    gr = sub.add_parser("group", help="dancer groups")
    gs = gr.add_subparsers(dest="gcmd")
    g = gs.add_parser("create")
    g.add_argument("name")
    g.add_argument("leader", help="leader's account")
    g.add_argument("--song", type=int, default=1, help="group song id")
    g.add_argument("--max", type=int, default=30, help="most members")
    g = gs.add_parser("join")
    g.add_argument("group", type=int)
    g.add_argument("account")
    g = gs.add_parser("leave")
    g.add_argument("account")
    gs.add_parser("list")
    g = gs.add_parser("members")
    g.add_argument("group", type=int)
    g = gs.add_parser("set")
    g.add_argument("group", type=int)
    g.add_argument("--name")
    g.add_argument("--song", type=int)
    g.add_argument("--max", type=int)
    g.add_argument("--leader", help="leader's account")
    g = gs.add_parser("delete")
    g.add_argument("group", type=int)
    st = sub.add_parser("store", help="registered stores and their bookkeeping")
    st.add_argument("store", type=int, nargs="?")
    mk = sub.add_parser("market", help="the market catalogue")
    mk.add_argument("--class", dest="cls", type=int, help="0 cards, 1 skins/faces, 2 talks, 3 tickets, 4 clothes, 5 lucky bags")
    sub.add_parser("missions", help="the mission set")
    sub.add_parser("world", help="the world event values in force now")
    lb = sub.add_parser("luckybag", help="draw lucky bags (no player changes)")
    lb.add_argument("-n", type=int, default=10)
    im = sub.add_parser("import-data", help="rebuild data/market.json and data/luckybag.json from the game")
    im.add_argument("root", help="the rom set's WeDo/linux directory")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cmd = args.cmd or "serve"
    if cmd == "serve":
        from .wedance.world import World
        host, port = _hostport(args.listen, 10000)
        names = getattr(args, "store_name", "IGS,Online").split(",", 1) + [""]
        cfg = Config(store_name=(names[0], names[1]), ticker=getattr(args, "ticker", ""),
                     world=World(getattr(args, "world", "") or None),
                     luckybag=getattr(args, "luckybag", "") or env("IGS_ONLINE_LUCKYBAG"),
                     missions=getattr(args, "missions", "") or env("IGS_ONLINE_MISSIONS"))
        try:
            asyncio.run(serve(host, port, args.db, args.admin or None, cfg))
        except KeyboardInterrupt:
            pass
        return 0
    if cmd in ("qr", "online", "profit"):
        req = {"cmd": cmd}
        if cmd in ("qr", "profit"):
            req["store"] = args.store
        if cmd == "qr":
            req["account"] = args.account
        return _admin(args.admin, req)
    if cmd == "import-data":
        from .wedance import gamedata
        for path in gamedata.import_all(args.root):
            print(path)
        return 0
    if cmd == "market":
        from .wedance.market import Catalogue, CLASSES
        cat = Catalogue()
        for code, it in cat.listing(args.cls):
            cur, n = cat.price(code)
            print("%s  %-6s %-10s %6d %-8s  %s / %s" % (code, CLASSES.get(it["class"], it["class"]), cur, n,
                                                       "sell %d" % it["sell"] if it["sell"] else "",
                                                       it["group"], it["name"]))
        return 0
    if cmd == "missions":
        from .wedance.missions import Missions
        conds = {1: "perfects", 2: "perfect+great+cool", 3: "plays", 4: "max combo"}
        for m in Missions().data["missions"]:
            c = ["%s %d" % (conds[x[0]], x[1]) for x in (m["cond_a"], m["cond_b"]) if x]
            print("mission %d: song %d, %s; bonus %d crystals, %d coins, %d days" % (
                m["id"], m["song"], " and ".join(c), m["bonus_crystal"], m["bonus_coin"], m["days"]))
        return 0
    if cmd == "world":
        from .wedance.world import World
        w = World(env("IGS_ONLINE_WORLD") or None)
        print(json.dumps({"feature": w["feature"], "world_seed": w.seed(), "world_level": w.level(),
                          "activity_rate": w.activity_rate(), "conditions": w.conditions(),
                          "ticker": w["ticker"], "notice": w["notice"]}, indent=1, ensure_ascii=False))
        return 0
    if cmd == "luckybag":
        from .wedance.luckybag import LuckyBag, decode
        bag = LuckyBag()
        for _ in range(args.n):
            t = bag.draw()
            print(t, decode(t))
        return 0
    db = DB(args.db)

    def member(account):
        p, _ = db.player_by_account(konet.account_value(account))
        if not p:
            print("no such account %s" % account)
        return p

    if cmd == "add":
        acc, pw = konet.account_value(args.account), konet.account_value(args.password)
        if db.account_exists(acc):
            print("account exists")
            return 1
        p = db.create_player(acc, pw, _name(args.name))
        print("uid %d" % p["uid"])
        return 0
    if cmd == "list":
        for r in db.stores():
            info = db.store_info(r["id"])
            print("store %d %s %s" % (r["id"], _text(info.get("name1")), _text(info.get("name2"))))
        for uid, acc, p in db.players():
            print("uid %d account %s name %r level %d exp %d group %d" % (
                uid, konet.account_digits(acc), profile.name_text(p), p.get("level", 1), p.get("exp", 0),
                db.group_of(uid)))
        return 0
    if cmd == "rename":
        p = member(args.account)
        if not p:
            return 1
        p["name"] = _name(args.name).hex()
        db.save(p)
        return 0
    if cmd == "show":
        p = member(args.account)
        if not p:
            return 1
        print(json.dumps(p, indent=1, sort_keys=True, ensure_ascii=False))
        return 0
    if cmd == "give":
        p = member(args.account)
        if not p:
            return 1
        p["crystal"] = max(0, p.get("crystal", 0) + args.crystal)
        p["weticket"] = max(0, p.get("weticket", 0) + args.weticket)
        if args.vip_days is not None:
            p["vip_until"] = int(time.time()) + args.vip_days * 86400 if args.vip_days else 0
        db.save(p)
        print("crystals %d, WeTickets %d, VIP until %s" % (p["crystal"], p["weticket"], _date(p.get("vip_until"))))
        return 0
    if cmd == "transfer":
        p = member(args.account)
        if not p:
            return 1
        if args.off:
            db.clear_pending(p["uid"], "transfer")
        else:
            db.set_pending(p["uid"], "transfer")
        print("uid %d: transfer %s at the next login" % (p["uid"], "off" if args.off else "asked"))
        return 0
    if cmd == "group":
        from .wedance.server import Server
        if args.gcmd == "create":
            p = member(args.leader)
            if not p:
                return 1
            gid = db.create_group(_name(args.name)[:16], p["uid"], args.song, args.max)
            print("group %d" % gid)
        elif args.gcmd == "join":
            p = member(args.account)
            g = db.group(args.group)
            if not p or not g:
                print("no such group" if p else "")
                return 1
            if len(g["members"]) >= (g["max_members"] or 30):
                print("group full")
                return 1
            db.join_group(p["uid"], args.group)
        elif args.gcmd == "leave":
            p = member(args.account)
            if not p:
                return 1
            print("left group %d" % db.leave_group(p["uid"]))
        elif args.gcmd == "set":
            kw = {}
            if args.name:
                kw["name"] = _name(args.name)[:16]
            if args.song is not None:
                kw["song"] = args.song
            if args.max is not None:
                kw["max_members"] = args.max
            if args.leader:
                p = member(args.leader)
                if not p:
                    return 1
                kw["leader"] = p["uid"]
            db.update_group(args.group, **kw)
        elif args.gcmd == "delete":
            db.delete_group(args.group)
        elif args.gcmd in ("list", "members"):
            srv = Server.__new__(Server)
            srv.db = db
            gs = db.groups() if args.gcmd == "list" else [db.group(args.group)]
            for g in gs:
                if not g:
                    print("no such group")
                    return 1
                b = srv.group_block(g["id"])
                print("group %d %r level %d, %d/%d members, song %d, leader uid %d, %s" % (
                    g["id"], profile.name_text({"name": g["name"].hex()}), b["level"], b["members"], b["max_members"], b["song"], g["leader"],
                    ", ".join("%s %d" % kv for kv in g["counters"].items())))
                if args.gcmd == "members":
                    for uid in g["members"]:
                        p = db.player(uid)
                        print("  uid %d %r level %d" % (uid, profile.name_text(p), p.get("level", 1)))
        else:
            print("group create | join | leave | list | members | set | delete")
            return 2
        return 0
    if cmd == "store":
        rows = db.stores() if args.store is None else [r for r in db.stores() if r["id"] == args.store]
        for r in rows:
            info = db.store_info(r["id"])
            print("store %d %s %s registered %s, seen %s" % (r["id"], _text(info.get("name1")),
                                                            _text(info.get("name2")), _date(r["created"]),
                                                            _date(r["seen"])))
            for k in ("login", "coin", "info"):
                if info.get(k):
                    print("  %s: %s" % (k, info[k]))
            if args.store is not None:
                for c in (0x172, 0x152, 0x154, 0x164, 0x16D):
                    for b in db.book(r["id"], c, 1):
                        print("  0x%03X at %s: %s" % (c, _date(b["time"]),
                                                       json.dumps(_book(c, b["data"]), ensure_ascii=False)))
        return 0
    return 2


def _pm3_book(cmd, data):
    """0x132 a day's / 0x133 a month's bookkeeping (SettingData::vNetSendDayIncome / MonthIncome)"""
    data = bytes(data)
    if cmd in (0x132, 0x133) and len(data) >= 28:
        y, mo, d, n, a, b, c, e, f, total, net, avg = struct.unpack_from("<HBBHHHHHHIIH", data)
        return {"date": "%04d-%02d-%02d" % (y, mo, d) if cmd == 0x132 else "%04d-%02d" % (y, mo),
                "plays": n, "fields": [a, b, c, e, f], "total": total, "total_less": net, "per_play": avg,
                "tables": data[28:].hex() if cmd == 0x133 else None}
    return {"hex": data.hex()} if data else {}


def pm3(argv):
    from .pm3 import profile
    from .pm3.db import DB
    from .pm3.events import Events, NAMES as BOARD_NAMES, CONTEST_RANK
    from .pm3.server import Config, serve, KIND_NAMES

    env = os.environ.get
    ap = argparse.ArgumentParser(prog="python -m igs_online pm3", description="Percussion Master 3 server")
    ap.add_argument("--listen", default=env("IGS_ONLINE_LISTEN", "127.0.0.1:9999"),
                    help="host:port to listen on (default 127.0.0.1:9999)")
    ap.add_argument("--db", default=env("IGS_ONLINE_DB", "pm3.db"), help="SQLite file (default pm3.db)")
    ap.add_argument("--admin", type=int, default=int(env("IGS_ONLINE_ADMIN", "9997")),
                    help="local admin port, 0 = none (default 9997)")
    ap.add_argument("--events", default=env("IGS_ONLINE_EVENTS", ""), help="events file (default data/events.json)")
    ap.add_argument("-v", "--verbose", action="count", default=int(env("IGS_ONLINE_LOG", "0")))
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the server (default)")
    s.add_argument("--patch", type=int, default=int(env("IGS_ONLINE_PATCH", "0")),
                   help="also answer pcli on this port with \"no update\" (0 = off)")
    s.add_argument("--store-name", default="igs-online", help="store name in rankings when a cabinet sent none")
    a = sub.add_parser("add", help="create a member")
    a.add_argument("id", help="keypad ID (digits)")
    a.add_argument("password", help="keypad password (digits)")
    a.add_argument("--name", default="", help="nickname (13 bytes Big5)")
    a.add_argument("--role", type=int, default=0, help="character 0..3")
    rn = sub.add_parser("rename", help="set a member's nickname")
    rn.add_argument("id")
    rn.add_argument("name")
    pw = sub.add_parser("password", help="set a member's password")
    pw.add_argument("id")
    pw.add_argument("password")
    lk = sub.add_parser("lock", help="lock a member out (the game: \"this account has been locked\")")
    lk.add_argument("id")
    lk.add_argument("--off", action="store_true")
    dl = sub.add_parser("delete", help="delete a member and their ranking entries")
    dl.add_argument("id")
    sub.add_parser("list", help="stores and members")
    sh = sub.add_parser("show", help="a member's profile and last play logs")
    sh.add_argument("id")
    sh.add_argument("--raw", action="store_true", help="the 2748 profile bytes in hex")
    gv = sub.add_parser("give", help="change a member's profile")
    gv.add_argument("id")
    gv.add_argument("--level", type=int)
    gv.add_argument("--exp", type=int)
    gv.add_argument("--title", action="append", default=[], metavar="FIELD:INDEX")
    gv.add_argument("--song", action="append", type=int, default=[], help="unlock a hidden song (id)")
    gv.add_argument("--chart", action="append", type=int, default=[], help="unlock a Master chart (song id)")
    gv.add_argument("--cursor", action="append", type=int, default=[])
    rk = sub.add_parser("ranks", help="a ranking board")
    rk.add_argument("--kind", type=int, default=0, help="0 normal, 1 nonstop, 2 song contest, 3 score contest, 4 combo contest")
    rk.add_argument("--class", dest="cls", type=int, default=0, help="difficulty 0..4 (kinds 0, 1)")
    rk.add_argument("-n", type=int, default=20)
    bd = sub.add_parser("boards", help="the event boards (and set which two run)")
    bd.add_argument("--set", help="board kinds, e.g. 1,14 (\"default\": events.json's)")
    sub.add_parser("dragons", help="the dragons of this month")
    ht = sub.add_parser("hot", help="the hot songs list (0x412): set it, or back to the most played")
    ht.add_argument("--set", help="song ids, e.g. 23,102 (\"default\": the most played of 30 days)")
    st = sub.add_parser("store", help="registered stores and their bookkeeping")
    st.add_argument("store", type=int, nargs="?")
    nt = sub.add_parser("notice", help="the coin page notice: set it, and push it to the cabinets online")
    nt.add_argument("text", nargs="?")
    nt.add_argument("--default", action="store_true", help="back to events.json's")
    sub.add_parser("online", help="connected cabinets and their members")
    sub.add_parser("time", help="push the time (the game takes it only in its first 6 s)")
    lv = sub.add_parser("level", help="push a game level (difficulty setting, SettingInfo +74)")
    lv.add_argument("level", type=int)
    si = sub.add_parser("storeinfo", help="push the store info (0x139) to the cabinets online")
    si.add_argument("--name", help="store name (Big5 text)")
    si.add_argument("--coins", type=int, help="coins per credit")
    im = sub.add_parser("import-data", help="rebuild data/boards.json from the game")
    im.add_argument("pm3", help="the decrypted executable, /PM3.bin/pm3")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cmd = args.cmd or "serve"
    events = Events(args.events or None)
    if cmd == "serve":
        host, port = _hostport(args.listen, 9999)
        cfg = Config(events, store_name=getattr(args, "store_name", "igs-online"))
        try:
            asyncio.run(serve(host, port, args.db, args.admin or None, cfg, getattr(args, "patch", 0) or None))
        except KeyboardInterrupt:
            pass
        return 0
    if cmd == "import-data":
        from .pm3 import gamedata
        for path in gamedata.import_all(args.pm3):
            print(path)
        return 0
    if cmd in ("online", "time"):
        return _admin(args.admin, {"cmd": cmd})
    if cmd == "level":
        return _admin(args.admin, {"cmd": "level", "level": args.level})
    if cmd == "storeinfo":
        req = {"cmd": "storeinfo"}
        if args.name:
            req["name"] = args.name.encode("big5", "replace").hex()
        if args.coins is not None:
            req["coins"] = args.coins
        return _admin(args.admin, req)
    db = DB(args.db)

    def member(login):
        m = db.member_by_login(login)
        if not m:
            print("no such member %s" % login)
        return m

    def name_of(m):
        return profile.text(profile.name(bytes(m["profile"]))) if m else "-"

    if cmd == "add":
        if not args.id.isdigit() or not args.password.isdigit():
            print("ID and password are keypad digits")
            return 1
        if db.member_by_login(args.id):
            print("ID exists")
            return 1
        m = db.create_member(args.id, args.password, args.name.encode("big5", "replace")[:13], args.role)
        print("member %d" % m["mid"])
        return 0
    if cmd == "list":
        for r in db.stores():
            info = json.loads(r["info"]) if r["info"] else {}
            print("store %d %s registered %s, seen %s" % (r["id"], _text(bytes.fromhex(info.get("name", ""))),
                                                          _date(r["created"]), _date(r["seen"])))
        for m in db.members():
            b = bytes(m["profile"])
            print("member %d ID %s %r level %d exp %d, store %s, seen %s" % (
                m["mid"], m["login"], name_of(m), b[25], profile.get(b, "exp"), m["store"], _date(m["seen"])))
        return 0
    if cmd in ("rename", "password", "delete", "show", "give", "lock"):
        m = member(args.id)
        if not m:
            return 1
        b = bytes(m["profile"])
        if cmd == "lock":
            locked = set(db.setting("locked") or [])
            (locked.discard if args.off else locked.add)(m["mid"])
            db.set_setting("locked", sorted(locked))
            print("locked: %s" % sorted(locked))
        elif cmd == "rename":
            b = bytearray(b)
            b[8:24] = args.name.encode("big5", "replace")[:13].ljust(16, b"\0")
            db.save_profile(m["mid"], bytes(b))
        elif cmd == "password":
            db.set_password(m["mid"], args.password)
        elif cmd == "delete":
            db.delete_member(m["mid"])
        elif cmd == "show":
            if args.raw:
                print(b.hex())
                return 0
            d = profile.summary(b)
            d.update(member_id=m["mid"], id=m["login"], store=m["store"])
            d["play_logs"] = [dict(profile.play_log(r["data"]), time=_date(r["time"])) for r in db.logs(m["mid"], 5)]
            print(json.dumps(d, indent=1, ensure_ascii=False))
        elif cmd == "give":
            b = bytearray(b)
            if args.level is not None:
                b[25] = max(1, min(50, args.level))
            if args.exp is not None:
                profile.put(b, "exp", args.exp)
            b = bytes(b)
            for t in args.title:
                f, i = (int(x) for x in t.split(":"))
                b = profile.grant_title(b, f, i)
            for s in args.song:
                b = profile.grant_song(b, s)
            for s in args.chart:
                b = profile.grant_song(b, s, chart=True)
            for c in args.cursor:
                b = profile.grant_cursor(b, c)
            db.save_profile(m["mid"], b)
            print("member %d: level %d, exp %d, titles %s" % (m["mid"], b[25], profile.get(b, "exp"),
                                                              profile.summary(b)["titles"]))
        return 0
    if cmd == "ranks":
        board = {v: k for k, v in CONTEST_RANK.items()}.get(args.kind)
        period = events.period(board) if board else ""
        cls = args.cls if args.kind in (0, 1) else 0
        print("%s, %s" % (KIND_NAMES.get(args.kind, args.kind),
                          "difficulty %d" % cls if args.kind in (0, 1) else "period from %s" % period))
        for i, e in enumerate(db.board(args.kind, cls, period, 0, args.n)):
            print("%3d. %-14s %9d  combo %d, %s" % (i + 1, name_of(db.member(e["mid"])), e["value"], e["combo"] or 0,
                                                     _date(e["time"])))
        return 0
    if cmd == "boards":
        if args.set:
            db.set_setting("active", None if args.set == "default" else [int(x) for x in args.set.split(",")])
        active = db.setting("active")
        for k in events.active(active):
            a, z = events.dates(k)
            print("board %d %s (%s .. %s)%s: %s" % (k, BOARD_NAMES.get(k, ""), a, z,
                                                    "" if events.running(k, active) else " not running",
                                                    events.texts["boards"].get(str(k), "")))
        if args.set:
            _admin(args.admin, {"cmd": "boards"})          # cabinets online get them now
        return 0
    if cmd == "hot":
        if args.set:
            db.set_setting("hot", None if args.set == "default" else [int(x) for x in args.set.split(",")])
        print("hot songs: %s" % (db.setting("hot") or "%s (most played)" % db.popular()))
        return 0
    if cmd == "dragons":
        for board in (14, 22):
            for d in db.dragons(board, events.period(board), alive=False):
                print("board %d dragon %d %s, difficulty/group %d kind %d, hp %d/%d, %s" % (
                    board, d["id"], d["name"], d["cls"], d["kind"], d["hp"], d["max_hp"],
                    "caught by %s %s" % (name_of(db.member(d["catcher"])), _date(d["caught"])) if d["catcher"]
                    else "free"))
        return 0
    if cmd == "notice":
        if args.default:
            db.set_setting("notice", None)
        elif args.text is not None:
            db.set_setting("notice", args.text)
        text = db.setting("notice")
        print("notice: %s" % (text if text is not None else events.data.get("notice", "")))
        return _admin(args.admin, {"cmd": "notice", "text": text})
    if cmd == "store":
        rows = db.stores() if args.store is None else [r for r in db.stores() if r["id"] == args.store]
        for r in rows:
            info = json.loads(r["info"]) if r["info"] else {}
            print("store %d %s registered %s, seen %s, versions %s" % (
                r["id"], _text(bytes.fromhex(info.get("name", ""))), _date(r["created"]), _date(r["seen"]),
                r["versions"]))
            if info:
                print("  info: %s" % {k: v for k, v in info.items() if k != "name"})
            if args.store is not None:
                for c in (0x132, 0x133, 0x135, 0x136):
                    for b in db.book(r["id"], c, 3):
                        print("  0x%03X at %s: %s" % (c, _date(b["time"]),
                                                       json.dumps(_pm3_book(c, b["data"]), ensure_ascii=False)))
        return 0
    return 2


GAMES = {"wedance": wedance, "pm3": pm3}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in GAMES:
        print("usage: python -m igs_online {%s} [command] [options]" % ",".join(GAMES), file=sys.stderr)
        return 2
    return GAMES[argv[0]](argv[1:])
