"""Lucky bags (0x273 -> 0x274): draw an item for the market's W2LuckyBag.

The answer is a text the game splits at commas (W2LuckyBag::DataDecode):

  1,song                                      a song (id), unlocked
  2,n                                         n more lucky-bag wardrobe slots
  3,part,id,id2,a1,a2,a3,a4,a5,uses           clothes: part (cardinfo: 0 back, 1 hair,
                                              3 upper, 4 lower, 6 shoes), id for
                                              sex 1, id2 for sex 0, five attribute
                                              flags (speed up, slow down, spin, hide,
                                              reverse), uses (0: no limit)
  4,n                                         n crystals

Weights and item lists come from data/luckybag.json, read from the game's
own script/luckybag.lua.out (IGS's RandomLuckyBag).  How attributes and
uses are given is this server's (see docs).
"""
import datetime
import random

from . import gamedata

LUCKYBAG_CAP = 30                       # PDTMarket::WritePlayerData asserts luckybagWardrobeCap <= 30


def pick(rng, entries):
    """weighted choice over dicts with a "weight" (entries of weight 0 never come up)"""
    entries = [e for e in entries if e.get("weight", 0) > 0]
    if not entries:
        return None
    return rng.choices(entries, weights=[e["weight"] for e in entries])[0]


class LuckyBag:
    def __init__(self, data=None, rng=None):
        self.data = data or gamedata.load("luckybag.json")
        self.rng = rng or random.Random()

    def week_song(self, today):
        week = min(52, today.isocalendar()[1])
        for e in self.data["songs"]["week"]:
            if e.get("week") == week:
                return e
        return None

    def month_song(self, today):
        md = (today.month, today.day)
        for e in self.data["songs"]["month"]:
            a, b = tuple(e["from"]), tuple(e["to"])
            if (a <= md <= b) if a <= b else (md >= a or md <= b):
                return e
        return None

    def song(self, flags, today):
        """flags: bit 0 the player has this week's song, bit 1 this month's (0x273)"""
        kinds = []
        for t in self.data["song_types"]:
            if t["key"] == "week" and (flags & 1 or not self.week_song(today)):
                continue
            if t["key"] == "month" and (flags & 2 or not self.month_song(today)):
                continue
            kinds.append(t)
        k = pick(self.rng, kinds)["key"]
        if k == "week":
            return self.week_song(today)["id"]
        if k == "month":
            return self.month_song(today)["id"]
        return pick(self.rng, self.data["songs"][k])["id"]

    def cloth(self, part):
        names = {0: "back", 1: "hair", 3: "up", 4: "down", 6: "shoe"}
        item = pick(self.rng, self.data["cloth"][names[part]])
        kind = pick(self.rng, self.data["attribute_types"])["key"]
        n = len(self.data["attributes"])
        flags = [0] * n
        for i in self.rng.sample(range(n), 2 if kind == "double" else 1):
            flags[i] = 1
        lo, hi = self.data["uses"]
        uses = 0 if kind == "special" else self.rng.randint(lo, hi)
        return [3, part, item["id"], item["id2"]] + flags + [uses]

    def draw(self, flags=0, luckybag_cap=0, today=None):
        """the 0x274 text for one bag"""
        today = today or datetime.date.today()
        types = [t for t in self.data["types"]
                 if not (t["kind"] == "box" and luckybag_cap >= LUCKYBAG_CAP)]
        t = pick(self.rng, types)
        if t["kind"] == "song":
            out = [1, self.song(flags, today)]
        elif t["kind"] == "box":
            out = [2, self.data.get("box", 1)]
        elif t["kind"] == "crystal":
            out = [4, pick(self.rng, self.data["crystal"])["amount"]]
        else:
            out = self.cloth(t["part"])
        return ",".join(str(v) for v in out)


def decode(text):
    """the fields the game takes from a draw (W2LuckyBag::DataDecode)"""
    v = [int(x) for x in text.split(",")]
    if v[0] == 1:
        return {"kind": "song", "song": v[1]}
    if v[0] == 2:
        return {"kind": "box", "slots": v[1]}
    if v[0] == 3:
        return {"kind": "cloth", "part": v[1], "id": v[2], "id2": v[3], "attributes": v[4:9], "uses": v[9]}
    if v[0] == 4:
        return {"kind": "crystal", "crystal": v[1]}
    raise ValueError(text)
