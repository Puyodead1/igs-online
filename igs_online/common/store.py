"""SQLite helpers shared by the game servers."""
import sqlite3
import time


def connect(path, schema):
    db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(schema)
    return db


def now():
    return int(time.time())
