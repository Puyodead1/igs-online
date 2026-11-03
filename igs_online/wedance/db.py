"""We Dance storage: stores (cabinet registrations), players, scores, groups,
store bookkeeping, raw results."""
import json

from ..common import store
from . import konet, profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY,
    keycpu BLOB NOT NULL,
    keymac BLOB NOT NULL,
    created INTEGER, seen INTEGER
);
CREATE TABLE IF NOT EXISTS players (
    uid INTEGER PRIMARY KEY,
    account INTEGER UNIQUE NOT NULL,
    password INTEGER NOT NULL,
    profile TEXT NOT NULL,
    created INTEGER, seen INTEGER
);
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY,
    uid INTEGER NOT NULL, store INTEGER, song INTEGER NOT NULL, diff INTEGER,
    score INTEGER NOT NULL, full_combo INTEGER, mode INTEGER, time INTEGER
);
CREATE INDEX IF NOT EXISTS scores_song ON scores (song, score);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY,
    uid INTEGER, store INTEGER, cmd INTEGER NOT NULL, mode INTEGER, time INTEGER, data BLOB
);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY, uid INTEGER, store INTEGER, song INTEGER NOT NULL, time INTEGER
);
CREATE TABLE IF NOT EXISTS coop (
    id INTEGER PRIMARY KEY, song INTEGER NOT NULL, uid1 INTEGER NOT NULL, uid2 INTEGER NOT NULL,
    score INTEGER NOT NULL, diff INTEGER, time INTEGER
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY, name BLOB NOT NULL, leader INTEGER, song INTEGER, max_members INTEGER,
    counters TEXT, created INTEGER
);
CREATE TABLE IF NOT EXISTS group_members (
    uid INTEGER PRIMARY KEY, grp INTEGER NOT NULL, joined INTEGER
);
CREATE TABLE IF NOT EXISTS store_info (
    id INTEGER PRIMARY KEY, name1 BLOB, name2 BLOB, info TEXT, coin TEXT, login TEXT, updated INTEGER
);
CREATE TABLE IF NOT EXISTS book (
    id INTEGER PRIMARY KEY, store INTEGER, cmd INTEGER, time INTEGER, data BLOB
);
CREATE TABLE IF NOT EXISTS pending (
    uid INTEGER NOT NULL, kind TEXT NOT NULL, value TEXT, PRIMARY KEY (uid, kind)
);
"""

FIRST_UID = 10001                       # the game treats uid > 10000 as a member

GROUP_COUNTERS = ("fight_won", "fight_lost", "coop_won", "coop_lost", "single_won", "single_lost")


class DB:
    def __init__(self, path):
        self.db = store.connect(path, SCHEMA)
        self.ensure_store(konet.DEFAULT_STORE, konet.DEFAULT_KEYCPU, konet.DEFAULT_KEYMAC)

    # ---- stores ----
    def ensure_store(self, sid, keycpu, keymac):
        if not self.db.execute("SELECT 1 FROM stores WHERE id=?", (sid,)).fetchone():
            self.db.execute("INSERT INTO stores VALUES (?,?,?,?,?)", (sid, keycpu, keymac, store.now(), None))

    def register_store(self, keycpu, keymac):
        row = self.db.execute("SELECT id FROM stores WHERE keycpu=? AND keymac=? AND id<>?",
                              (keycpu, keymac, konet.DEFAULT_STORE)).fetchone()
        if row:
            return row["id"]
        sid = max(1, (self.db.execute("SELECT MAX(id) FROM stores WHERE id<>?",
                                      (konet.DEFAULT_STORE,)).fetchone()[0] or 0) + 1)
        self.db.execute("INSERT INTO stores VALUES (?,?,?,?,?)", (sid, keycpu, keymac, store.now(), None))
        return sid

    def store_keys(self, sid):
        row = self.db.execute("SELECT keycpu, keymac FROM stores WHERE id=?", (sid,)).fetchone()
        if row:
            self.db.execute("UPDATE stores SET seen=? WHERE id=?", (store.now(), sid))
            return bytes(row["keycpu"]), bytes(row["keymac"])
        return None

    def stores(self):
        return self.db.execute("SELECT id, created, seen FROM stores ORDER BY id").fetchall()

    def set_store_info(self, sid, **kw):
        """name1, name2 (bytes), info (text), coin, login (dicts)"""
        if not self.db.execute("SELECT 1 FROM store_info WHERE id=?", (sid,)).fetchone():
            self.db.execute("INSERT INTO store_info (id) VALUES (?)", (sid,))
        for k, v in kw.items():
            if isinstance(v, dict):
                v = json.dumps(v)
            self.db.execute("UPDATE store_info SET %s=?, updated=? WHERE id=?" % k, (v, store.now(), sid))

    def store_info(self, sid):
        row = self.db.execute("SELECT * FROM store_info WHERE id=?", (sid,)).fetchone()
        if not row:
            return {}
        d = dict(row)
        for k in ("coin", "login"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def add_book(self, sid, cmd, data):
        self.db.execute("INSERT INTO book (store, cmd, time, data) VALUES (?,?,?,?)", (sid, cmd, store.now(), data))

    def book(self, sid, cmd=None, n=1):
        q = "SELECT * FROM book WHERE store=?" + (" AND cmd=?" if cmd else "") + " ORDER BY id DESC LIMIT ?"
        return self.db.execute(q, (sid, cmd, n) if cmd else (sid, n)).fetchall()

    # ---- players ----
    def account_exists(self, account):
        return self.db.execute("SELECT 1 FROM players WHERE account=?", (account,)).fetchone() is not None

    def create_player(self, account, password, name=b"", sex=0, skin=0, face=0):
        uid = max(FIRST_UID, (self.db.execute("SELECT MAX(uid) FROM players").fetchone()[0] or 0) + 1)
        p = profile.new_profile(uid, account, name, sex, skin, face)
        p["card_id"] = str(uid).encode().hex()
        self.db.execute("INSERT INTO players VALUES (?,?,?,?,?,?)",
                        (uid, account, password, json.dumps(p), store.now(), None))
        return p

    def player_by_account(self, account):
        row = self.db.execute("SELECT * FROM players WHERE account=?", (account,)).fetchone()
        return (profile.upgrade(json.loads(row["profile"])), row["password"]) if row else (None, None)

    def player(self, uid):
        row = self.db.execute("SELECT profile FROM players WHERE uid=?", (uid,)).fetchone()
        return profile.upgrade(json.loads(row["profile"])) if row else None

    def save(self, p):
        self.db.execute("UPDATE players SET profile=?, seen=? WHERE uid=?",
                        (json.dumps(p), store.now(), p["uid"]))

    def set_password(self, uid, password):
        self.db.execute("UPDATE players SET password=? WHERE uid=?", (password, uid))

    def set_account(self, uid, account, password):
        self.db.execute("UPDATE players SET account=?, password=? WHERE uid=?", (account, password, uid))

    def delete_player(self, uid):
        self.db.execute("DELETE FROM players WHERE uid=?", (uid,))
        self.db.execute("DELETE FROM group_members WHERE uid=?", (uid,))
        self.db.execute("DELETE FROM pending WHERE uid=?", (uid,))

    def password(self, uid):
        row = self.db.execute("SELECT password FROM players WHERE uid=?", (uid,)).fetchone()
        return row["password"] if row else None

    def players(self):
        return [(r["uid"], r["account"], profile.upgrade(json.loads(r["profile"])))
                for r in self.db.execute("SELECT * FROM players ORDER BY uid")]

    # ---- pending flags (set from the command line, used at the next login) ----
    def set_pending(self, uid, kind, value="1"):
        self.db.execute("INSERT OR REPLACE INTO pending VALUES (?,?,?)", (uid, kind, value))

    def pending(self, uid, kind):
        row = self.db.execute("SELECT value FROM pending WHERE uid=? AND kind=?", (uid, kind)).fetchone()
        return row["value"] if row else None

    def clear_pending(self, uid, kind):
        self.db.execute("DELETE FROM pending WHERE uid=? AND kind=?", (uid, kind))

    # ---- scores, plays, results ----
    def add_score(self, uid, sid, song, diff, score, full_combo, mode):
        self.db.execute("INSERT INTO scores (uid, store, song, diff, score, full_combo, mode, time) "
                        "VALUES (?,?,?,?,?,?,?,?)", (uid, sid, song, diff, score, full_combo, mode, store.now()))

    def song_top(self, song, n, since=0):
        """best score per member on a song (since a time)"""
        return self.db.execute(
            "SELECT uid, MAX(score) AS score FROM scores WHERE song=? AND uid>? AND time>=? "
            "GROUP BY uid ORDER BY score DESC LIMIT ?", (song, FIRST_UID - 1, since, n)).fetchall()

    def song_rank(self, song, uid, since=0):
        """the member's place on a song (1..), 0 if none"""
        rows = self.song_top(song, 1000, since)
        for i, r in enumerate(rows):
            if r["uid"] == uid:
                return i + 1
        return 0

    def add_play(self, uid, sid, song):
        self.db.execute("INSERT INTO plays (uid, store, song, time) VALUES (?,?,?,?)", (uid, sid, song, store.now()))

    def popular(self, since=0, n=200):
        """(song, plays) most played first"""
        return self.db.execute("SELECT song, COUNT(*) AS n FROM plays WHERE time>=? GROUP BY song "
                               "ORDER BY n DESC, song LIMIT ?", (since, n)).fetchall()

    def store_plays(self, since=0):
        return self.db.execute("SELECT store, COUNT(*) AS n FROM plays WHERE time>=? GROUP BY store "
                               "ORDER BY n DESC", (since,)).fetchall()

    def add_coop(self, song, uid1, uid2, score, diff):
        a, b = sorted((uid1, uid2))
        self.db.execute("INSERT INTO coop (song, uid1, uid2, score, diff, time) VALUES (?,?,?,?,?,?)",
                        (song, a, b, score, diff, store.now()))

    def coop_top(self, song, n, since=0):
        return self.db.execute("SELECT uid1, uid2, MAX(score) AS score, diff FROM coop WHERE song=? AND time>=? "
                               "GROUP BY uid1, uid2 ORDER BY score DESC LIMIT ?", (song, since, n)).fetchall()

    def add_result(self, uid, sid, cmd, mode, data):
        self.db.execute("INSERT INTO results (uid, store, cmd, mode, time, data) VALUES (?,?,?,?,?,?)",
                        (uid, sid, cmd, mode, store.now(), data))

    # ---- groups ----
    def create_group(self, name, leader, song=0, max_members=30):
        cur = self.db.execute("INSERT INTO groups (name, leader, song, max_members, counters, created) "
                              "VALUES (?,?,?,?,?,?)", (name, leader, song, max_members,
                                                       json.dumps(dict.fromkeys(GROUP_COUNTERS, 0)), store.now()))
        gid = cur.lastrowid
        if leader:
            self.join_group(leader, gid)
        return gid

    def group(self, gid):
        row = self.db.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
        if not row:
            return None
        g = dict(row)
        g["name"] = bytes(g["name"])
        g["counters"] = json.loads(g["counters"] or "{}")
        g["members"] = [r["uid"] for r in self.db.execute(
            "SELECT uid FROM group_members WHERE grp=? ORDER BY joined, uid", (gid,))]
        return g

    def groups(self):
        return [self.group(r["id"]) for r in self.db.execute("SELECT id FROM groups ORDER BY id")]

    def group_of(self, uid):
        row = self.db.execute("SELECT grp FROM group_members WHERE uid=?", (uid,)).fetchone()
        return row["grp"] if row else 0

    def join_group(self, uid, gid):
        self.db.execute("INSERT OR REPLACE INTO group_members VALUES (?,?,?)", (uid, gid, store.now()))

    def leave_group(self, uid):
        gid = self.group_of(uid)
        self.db.execute("DELETE FROM group_members WHERE uid=?", (uid,))
        g = self.group(gid) if gid else None
        if g and g["leader"] == uid:
            self.db.execute("UPDATE groups SET leader=? WHERE id=?", (g["members"][0] if g["members"] else 0, gid))
        return gid

    def update_group(self, gid, **kw):
        for k, v in kw.items():
            if k == "counters":
                v = json.dumps(v)
            self.db.execute("UPDATE groups SET %s=? WHERE id=?" % k, (v, gid))

    def delete_group(self, gid):
        self.db.execute("DELETE FROM group_members WHERE grp=?", (gid,))
        self.db.execute("DELETE FROM groups WHERE id=?", (gid,))

    def count_group(self, gid, counter):
        g = self.group(gid)
        if not g or counter not in GROUP_COUNTERS:
            return
        g["counters"][counter] = g["counters"].get(counter, 0) + 1
        self.update_group(gid, counters=g["counters"])
