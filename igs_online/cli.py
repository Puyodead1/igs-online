"""python -m igs_online <game> [command] ...

Settings come from flags or the environment (IGS_ONLINE_LISTEN, IGS_ONLINE_DB,
IGS_ONLINE_ADMIN, IGS_ONLINE_LOG).
"""
import argparse
import asyncio
import json
import logging
import os
import socket
import sys


def _hostport(s, default_port):
    host, _, port = s.rpartition(":")
    if not host:
        return s, default_port
    return host, int(port)


def _admin(port, req):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        res = json.loads(s.makefile().readline())
    print(json.dumps(res, indent=1))
    return 1 if "error" in res else 0


def _name(s):
    """a player name as the game stores it (GBK, 14 bytes at most)"""
    b = s.encode("gbk", "replace")[:14]
    return b


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
    s.add_argument("--store-name", default="IGS,Online", help="store name shown in rankings: first,second")
    s.add_argument("--ticker", default="", help="news ticker text (ASCII)")
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
    sub.add_parser("online", help="list connected cabinets")
    sh = sub.add_parser("show", help="print a member's stored profile")
    sh.add_argument("account")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cmd = args.cmd or "serve"
    if cmd == "serve":
        host, port = _hostport(args.listen, 10000)
        names = getattr(args, "store_name", "IGS,Online").split(",", 1) + [""]
        cfg = Config(store_name=(names[0], names[1]), ticker=getattr(args, "ticker", ""))
        try:
            asyncio.run(serve(host, port, args.db, args.admin or None, cfg))
        except KeyboardInterrupt:
            pass
        return 0
    if cmd in ("qr", "online"):
        req = {"cmd": cmd}
        if cmd == "qr":
            req.update(account=args.account, store=args.store)
        return _admin(args.admin, req)
    db = DB(args.db)
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
            print("store %d" % r["id"])
        for uid, acc, p in db.players():
            print("uid %d account %s name %r level %d exp %d" % (uid, konet.account_digits(acc),
                                                                  profile.name_text(p), p.get("level", 1),
                                                                  p.get("exp", 0)))
        return 0
    if cmd == "rename":
        p, _ = db.player_by_account(konet.account_value(args.account))
        if not p:
            print("no such account")
            return 1
        p["name"] = _name(args.name).hex()
        db.save(p)
        return 0
    if cmd == "show":
        p, _ = db.player_by_account(konet.account_value(args.account))
        if not p:
            print("no such account")
            return 1
        print(json.dumps(p, indent=1, sort_keys=True))
        return 0
    return 2


GAMES = {"wedance": wedance}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in GAMES:
        print("usage: python -m igs_online {%s} [command] [options]" % ",".join(GAMES), file=sys.stderr)
        return 2
    return GAMES[argv[0]](argv[1:])
