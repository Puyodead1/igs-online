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
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        res = json.loads(s.makefile().readline())
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


GAMES = {"wedance": wedance}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in GAMES:
        print("usage: python -m igs_online {%s} [command] [options]" % ",".join(GAMES), file=sys.stderr)
        return 2
    return GAMES[argv[0]](argv[1:])
