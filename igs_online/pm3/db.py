"""PM3 storage: stores (cabinet registrations), members and profiles, play
logs, ranking entries, dragons, bookkeeping, settings."""
import json

from ..common import store
from . import profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY, cpuid BLOB, mac BLOB, versions TEXT, info TEXT,
    created INTEGER, seen INTEGER
);
CREATE TABLE IF NOT EXISTS members (
    mid INTEGER PRIMARY KEY, login TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
    profile BLOB NOT NULL, store INTEGER, created INTEGER, seen INTEGER
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY, mid INTEGER, store INTEGER, time INTEGER, mode INTEGER, total INTEGER, data BLOB
);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY, mid INTEGER, store INTEGER, song INTEGER, cls INTEGER, score INTEGER, time INTEGER
);
CREATE TABLE IF NOT EXISTS ranks (
    kind INTEGER NOT NULL, cls INTEGER NOT NULL, period TEXT NOT NULL, mid INTEGER NOT NULL,
    value INTEGER NOT NULL, combo INTEGER, stages INTEGER, song INTEGER, store INTEGER, time INTEGER,
    PRIMARY KEY (kind, cls, period, mid)
);
CREATE TABLE IF NOT EXISTS dragons (
    id INTEGER PRIMARY KEY, board INTEGER NOT NULL, period TEXT NOT NULL, cls INTEGER, kind INTEGER,
    hp INTEGER, max_hp INTEGER, name TEXT, store INTEGER, finder INTEGER, catcher INTEGER, caught INTEGER
);
CREATE TABLE IF NOT EXISTS dragon_marks (
    mid INTEGER NOT NULL, board INTEGER NOT NULL, period TEXT NOT NULL, cls INTEGER NOT NULL,
    kind INTEGER NOT NULL, state INTEGER NOT NULL, dragon INTEGER, time INTEGER,
    PRIMARY KEY (mid, board, period, cls, kind)
);
CREATE TABLE IF NOT EXISTS book (
    id INTEGER PRIMARY KEY, store INTEGER, cmd INTEGER, time INTEGER, data BLOB
);
CREATE TABLE IF NOT EXISTS baselines (
    mid INTEGER NOT NULL, key TEXT NOT NULL, value INTEGER, PRIMARY KEY (mid, key)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT
);
"""


class DB:
    def __init__(self, path):
        self.db = store.connect(path, SCHEMA)

    # ---- settings (live: the CLI changes them for a running server) ----
    def setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        if value is None:
            self.db.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    # ---- stores ----
    def register_store(self, cpuid, mac, versions):
        row = self.db.execute("SELECT id FROM stores WHERE cpuid=? AND mac=?", (cpuid, mac)).fetchone()
        if row:
            return row["id"]
        cur = self.db.execute("INSERT INTO stores (cpuid, mac, versions, created, seen) VALUES (?,?,?,?,?)",
                              (cpuid, mac, json.dumps(versions), store.now(), store.now()))
        return cur.lastrowid

    def store_known(self, sid):
        return self.db.execute("SELECT 1 FROM stores WHERE id=?", (sid,)).fetchone() is not None

    def adopt_store(self, sid, cpuid, mac, versions):
        """a cabinet with a store id this database never gave (a new database)"""
        self.db.execute("INSERT OR IGNORE INTO stores (id, cpuid, mac, versions, created) VALUES (?,?,?,?,?)",
                        (sid, cpuid, mac, json.dumps(versions), store.now()))

    def seen_store(self, sid):
        self.db.execute("UPDATE stores SET seen=? WHERE id=?", (store.now(), sid))

    def stores(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM stores ORDER BY id")]

    def store_info(self, sid):
        row = self.db.execute("SELECT info FROM stores WHERE id=?", (sid,)).fetchone()
        return json.loads(row["info"]) if row and row["info"] else {}

    def set_store_info(self, sid, info):
        self.db.execute("UPDATE stores SET info=? WHERE id=?", (json.dumps(info, ensure_ascii=False), sid))

    def add_book(self, sid, cmd, data):
        self.db.execute("INSERT INTO book (store, cmd, time, data) VALUES (?,?,?,?)",
                        (sid, cmd, store.now(), bytes(data)))

    def book(self, sid, cmd=None, n=1):
        q = "SELECT * FROM book WHERE store=?" + (" AND cmd=?" if cmd else "") + " ORDER BY id DESC LIMIT ?"
        return self.db.execute(q, (sid, cmd, n) if cmd else (sid, n)).fetchall()

    # ---- members ----
    def member_by_login(self, login):
        row = self.db.execute("SELECT * FROM members WHERE login=?", (login,)).fetchone()
        return dict(row) if row else None

    def member(self, mid):
        row = self.db.execute("SELECT * FROM members WHERE mid=?", (mid,)).fetchone()
        return dict(row) if row else None

    def create_member(self, login, password, name, role=0, sid=None):
        cur = self.db.execute("INSERT INTO members (login, password, profile, store, created, seen) "
                              "VALUES (?,?,?,?,?,?)", (login, password, b"", sid, store.now(), store.now()))
        mid = cur.lastrowid
        self.save_profile(mid, profile.new(mid, name, role))
        return self.member(mid)

    def save_profile(self, mid, data, sid=None):
        self.db.execute("UPDATE members SET profile=?, seen=?, store=COALESCE(?, store) WHERE mid=?",
                        (bytes(data), store.now(), sid, mid))

    def set_password(self, mid, password):
        self.db.execute("UPDATE members SET password=? WHERE mid=?", (password, mid))

    def delete_member(self, mid):
        for t in ("members", "ranks", "dragon_marks", "baselines"):
            self.db.execute("DELETE FROM %s WHERE mid=?" % t, (mid,))

    def members(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM members ORDER BY mid")]

    # ---- logs, plays ----
    def add_log(self, mid, sid, mode, total, data):
        self.db.execute("INSERT INTO logs (mid, store, time, mode, total, data) VALUES (?,?,?,?,?,?)",
                        (mid, sid, store.now(), mode, total, bytes(data)))

    def add_play(self, mid, sid, song, cls, score):
        self.db.execute("INSERT INTO plays (mid, store, song, cls, score, time) VALUES (?,?,?,?,?,?)",
                        (mid, sid, song, cls, score, store.now()))

    def popular(self, days=30, n=20):
        rows = self.db.execute("SELECT song, COUNT(*) c FROM plays WHERE time>=? GROUP BY song "
                               "ORDER BY c DESC, song LIMIT ?", (store.now() - 86400 * days, n))
        return [r["song"] for r in rows]

    def logs(self, mid, n=10):
        return self.db.execute("SELECT * FROM logs WHERE mid=? ORDER BY id DESC LIMIT ?", (mid, n)).fetchall()

    # ---- rankings ----
    def rank_entry(self, kind, cls, period, mid):
        row = self.db.execute("SELECT * FROM ranks WHERE kind=? AND cls=? AND period=? AND mid=?",
                              (kind, cls, period, mid)).fetchone()
        return dict(row) if row else None

    def put_rank(self, kind, cls, period, mid, value, combo, stages, song, sid):
        self.db.execute("INSERT OR REPLACE INTO ranks VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (kind, cls, period, mid, value, combo, stages, song, sid, store.now()))

    def board(self, kind, cls, period, start=0, n=200):
        """entries best first (ties: the earlier)"""
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM ranks WHERE kind=? AND cls=? AND period=? ORDER BY value DESC, time LIMIT ? OFFSET ?",
            (kind, cls, period, n, start))]

    def place(self, kind, cls, period, mid):
        e = self.rank_entry(kind, cls, period, mid)
        if not e:
            return 0
        return 1 + self.db.execute(
            "SELECT COUNT(*) FROM ranks WHERE kind=? AND cls=? AND period=? AND "
            "(value>? OR (value=? AND time<?))", (kind, cls, period, e["value"], e["value"], e["time"])).fetchone()[0]

    def entrants(self, kind, cls, period):
        return self.db.execute("SELECT COUNT(*) FROM ranks WHERE kind=? AND cls=? AND period=?",
                               (kind, cls, period)).fetchone()[0]

    # ---- dragons ----
    def dragons(self, board, period, cls=None, alive=True):
        q = "SELECT * FROM dragons WHERE board=? AND period=?"
        args = [board, period]
        if cls is not None:
            q += " AND cls=?"
            args.append(cls)
        if alive:
            q += " AND catcher IS NULL"
        return [dict(r) for r in self.db.execute(q + " ORDER BY id", args)]

    def dragon(self, did):
        row = self.db.execute("SELECT * FROM dragons WHERE id=?", (did,)).fetchone()
        return dict(row) if row else None

    def add_dragon(self, board, period, cls, kind, hp, name, sid, finder):
        return self.db.execute(
            "INSERT INTO dragons (board, period, cls, kind, hp, max_hp, name, store, finder) VALUES (?,?,?,?,?,?,?,?,?)",
            (board, period, cls, kind, hp, hp, name, sid, finder)).lastrowid

    def hit_dragon(self, did, damage):
        self.db.execute("UPDATE dragons SET hp=MAX(0, hp-?) WHERE id=? AND catcher IS NULL", (damage, did))
        return self.dragon(did)

    def catch_dragon(self, did, mid):
        self.db.execute("UPDATE dragons SET catcher=?, caught=? WHERE id=?", (mid, store.now(), did))

    def mark(self, mid, board, period, cls, kind, state, did=None):
        """a member's grade for a dragon: keeps the higher (8 caught beats 5 fought); 9 lost only over 1..7"""
        row = self.db.execute("SELECT state FROM dragon_marks WHERE mid=? AND board=? AND period=? AND cls=? AND kind=?",
                              (mid, board, period, cls, kind)).fetchone()
        if row and (row["state"] == 8 or (state != 8 and row["state"] >= state)):
            return
        self.db.execute("INSERT OR REPLACE INTO dragon_marks VALUES (?,?,?,?,?,?,?,?)",
                        (mid, board, period, cls, kind, state, did, store.now()))

    def marks(self, mid, board, period):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM dragon_marks WHERE mid=? AND board=? AND period=?", (mid, board, period))]

    def fighters(self, did):
        return [r["mid"] for r in self.db.execute("SELECT mid FROM dragon_marks WHERE dragon=?", (did,))]

    # ---- per-member baselines (event counters) ----
    def baseline(self, mid, key, value):
        """the value when first seen under this key"""
        row = self.db.execute("SELECT value FROM baselines WHERE mid=? AND key=?", (mid, key)).fetchone()
        if row:
            return row["value"]
        self.db.execute("INSERT INTO baselines VALUES (?,?,?)", (mid, key, value))
        return value
