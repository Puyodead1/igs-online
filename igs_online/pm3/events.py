"""Event boards (0x272), the ticker (0x274), the coin page notice (0x416)
and the hot songs (0x412).

Board, 460 bytes (BoardInfo::SetBoardDataFromNet, UpdateBoardData; House):
  +4 u8 kind, +6 u16 / +8 u8 / +9 u8 first day, +10 / +12 / +13 last day,
  +14 char[256] text (Big5), +270..272 three bytes (the game's samples: 1, 2, 0),
  +276 10 x {u8, u8, pad, u32 value}: value[0] the EXP bonus of a new member (18)
       or the EXP rate in percent on the event's songs (15..17, 21; at most 300);
       kind 20: value[i] the EXP reward for reaching level[i],
  +356 10 x {u32 level, u32}: kind 20's levels,
  +436 u8[10] songs (19, 21, 24; 0xFF ends) or, kind 13, the contest song,
  +448 u32, +452 u32 (copied, never read).
Kinds: 1..10 holidays (the newest that runs is shown), 11 score contest, 12
combo contest, 13 the Knights' song contest, 14 dragons, 15..17 classical / pop
/ original song festivals, 18 sign-up bonus, 19 new songs, 20 level rewards,
21 song festival, 22 dragon 2, 23 / 24 PIU / ULTRAWAVE songs.  The ticker is
the board's first 270 bytes (272 a slot, 4 slots).
"""
import calendar
import datetime
import json
import os
import struct

DATA = os.path.join(os.path.dirname(__file__), "data")
BOARD = 460
TICKER = 272
HOLIDAYS = range(1, 11)
CONTESTS = (11, 12, 13)
DRAGON, DRAGON2 = 14, 22
NAMES = {1: "New Year", 2: "Labour Day", 3: "Dragon Boat", 4: "Valentine's", 5: "Mid-Autumn",
         6: "National Day", 7: "Singles' Day", 8: "Christmas", 9: "Children's Day", 10: "Women's Day",
         11: "score contest", 12: "combo contest", 13: "Knights' song contest", 14: "dragons",
         15: "classical festival", 16: "pop festival", 17: "original festival", 18: "sign-up bonus",
         19: "new songs", 20: "level rewards", 21: "song festival", 22: "dragon 2", 23: "PIU songs",
         24: "ULTRAWAVE songs"}
# ranking kind on the wire (0x232 .. 0x235) of each contest
CONTEST_RANK = {13: 2, 11: 3, 12: 4}


def big5(s, n):
    return s.encode("big5", "replace")[:n]


def _load(name, path=None):
    with open(path or os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


class Events:
    def __init__(self, path=None, boards_path=None):
        self.data = _load("events.json", path)
        self.texts = _load("boards.json", boards_path)

    # ---- which boards run ----
    def active(self, override=None):
        return [int(k) for k in (override if override is not None else self.data["active"])][:2]

    def dates(self, kind, today=None):
        today = today or datetime.date.today()
        d = self.data.get("dates", {}).get(str(kind))
        if d:
            return tuple(datetime.date.fromisoformat(x) for x in d)
        last = calendar.monthrange(today.year, today.month)[1]
        return today.replace(day=1), today.replace(day=last)

    def period(self, kind):
        """a contest's or event's id: its first day"""
        return self.dates(kind)[0].isoformat()

    def running(self, kind, override=None, today=None):
        today = today or datetime.date.today()
        if kind not in self.active(override):
            return False
        a, b = self.dates(kind, today)
        return a <= today <= b

    def param(self, kind, key, default=0):
        return self.data.get("params", {}).get(str(kind), {}).get(key, default)

    def contest_song(self):
        return self.param(13, "song", 1), self.param(13, "class", 0)

    # ---- messages ----
    def board(self, kind):
        b = bytearray(BOARD)
        if not kind:
            return bytes(b)
        a, z = self.dates(kind)
        struct.pack_into("<BxHBBHBB", b, 4, kind, a.year, a.month, a.day, z.year, z.month, z.day)
        text = self.texts["boards"].get(str(kind), NAMES.get(kind, ""))
        if kind == 13:
            text = text.replace("%d", str(self.contest_song()[0]))
        b[14:14 + 256] = big5(text, 255).ljust(256, b"\0")
        b[270:273] = bytes((1, 2, 0))
        if kind in (15, 16, 17, 21):
            struct.pack_into("<I", b, 276 + 4, self.param(kind, "rate", 100))
        elif kind == 18:
            struct.pack_into("<I", b, 276 + 4, self.param(kind, "exp", 0))
        elif kind == 20:
            for i, (lv, exp) in enumerate(self.param(kind, "levels", [])[:10]):
                struct.pack_into("<I", b, 276 + 8 * i + 4, exp)
                struct.pack_into("<I", b, 356 + 8 * i, lv)
        songs = self.texts.get("songs", {}).get(str(kind)) or self.param(kind, "songs", [])
        if kind == 13:
            b[436] = self.contest_song()[0] & 0xFF
        elif songs:
            s = bytes(x & 0xFF for x in songs[:10])
            b[436:446] = s + b"\xff" * (10 - len(s))
        return bytes(b)

    def boards(self, override=None):
        """0x272: the two boards (kind 0: none)"""
        kinds = [k for k in self.active(override) if self.running(k, override)]
        kinds = (kinds + [0, 0])[:2]
        return b"".join(self.board(k) for k in kinds)

    def tickers(self, override=None):
        """0x274: four ticker lines, the running boards' first"""
        out = []
        for k in self.active(override):
            if self.running(k, override):
                t = bytearray(self.board(k)[:TICKER])
                text = self.texts["tickers"].get(str(k))
                if text:
                    t[14:270] = big5(text, 255).ljust(256, b"\0")
                out.append(bytes(t))
        if not out:
            t = bytearray(TICKER)
            a, z = self.dates(0)
            struct.pack_into("<BxHBBHBB", t, 4, 21, a.year, a.month, a.day, z.year, z.month, z.day)
            t[14:270] = big5(self.texts.get("no_event_ticker", ""), 255).ljust(256, b"\0")
            out.append(bytes(t))
        return b"".join((out + [bytes(TICKER)] * 4)[:4])

    def notice(self, text=None):
        """0x416: u8 n, n bytes (Big5)"""
        b = big5(self.data.get("notice", "") if text is None else text, 255)
        return bytes((len(b),)) + b

    def hot_songs(self, popular=()):
        """0x412: u8 n, u8 song ids[256] (select type 4's list)"""
        ids = [s for s in popular if 0 < s < 256][:20] or self.data.get("hot_songs", [])
        b = bytearray(257)
        b[0] = len(ids)
        b[1:1 + len(ids)] = bytes(ids)
        return bytes(b)
