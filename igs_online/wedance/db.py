"""We Dance storage: stores (cabinet registrations), players, scores, raw results."""
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
"""

FIRST_UID = 10001                       # the game treats uid > 10000 as a member


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
        return (json.loads(row["profile"]), row["password"]) if row else (None, None)

    def player(self, uid):
        row = self.db.execute("SELECT profile FROM players WHERE uid=?", (uid,)).fetchone()
        return json.loads(row["profile"]) if row else None

    def save(self, p):
        self.db.execute("UPDATE players SET profile=?, seen=? WHERE uid=?",
                        (json.dumps(p), store.now(), p["uid"]))

    def set_password(self, uid, password):
        self.db.execute("UPDATE players SET password=? WHERE uid=?", (password, uid))

    def players(self):
        return [(r["uid"], r["account"], json.loads(r["profile"]))
                for r in self.db.execute("SELECT * FROM players ORDER BY uid")]

    # ---- scores, results ----
    def add_score(self, uid, sid, song, diff, score, full_combo, mode):
        self.db.execute("INSERT INTO scores (uid, store, song, diff, score, full_combo, mode, time) "
                        "VALUES (?,?,?,?,?,?,?,?)", (uid, sid, song, diff, score, full_combo, mode, store.now()))

    def song_top(self, song, n):
        """best score per member on a song"""
        return self.db.execute(
            "SELECT uid, MAX(score) AS score FROM scores WHERE song=? AND uid>? "
            "GROUP BY uid ORDER BY score DESC LIMIT ?", (song, FIRST_UID - 1, n)).fetchall()

    def add_result(self, uid, sid, cmd, mode, data):
        self.db.execute("INSERT INTO results (uid, store, cmd, mode, time, data) VALUES (?,?,?,?,?,?)",
                        (uid, sid, cmd, mode, store.now(), data))
