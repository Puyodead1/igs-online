"""The market: catalogue (data/market.json, from the game's script/market.db3)
and the trade message 0x271 -> 0x272.

The game prices and pays locally (PDTMarket::WritePlayerData) and sends one
0x271 per trade with the player's WeTickets and crystals after it, the
last item's code and what changed; 0x272 `i8` 0 accepts, anything else
undoes the trade on the cabinet.

0x271: u8 op, i32 WeTickets, i32 crystals, i8 L (negative for op 8), code[L], then
  0, 8   props block (cards; 8 = sold)
  1      u8 skin, u8 face
  2      talks block
  3      -                         (tickets: suites, workshops, world tour, missions)
  4, 12  head, upper, lower, feet, back blocks (12: from a lucky bag)
  11     6 x u8 wardrobe capacities, u8 lucky-bag capacity
  13     u8 lucky-bag capacity
  14     u16 song (from a lucky bag)
  15     -                         (crystals from a lucky bag)

Currency per item (`currency`, the game's kbuymode): 0 credits, 1 crystals
(VIP price while VIP), 2 WeTickets.
"""
from ..common.packet import Reader
from . import gamedata, profile

CURRENCY = {0: "credit", 1: "crystal", 2: "weticket"}
CLASSES = {0: "card", 1: "skin", 2: "talk", 3: "ticket", 4: "clothes", 5: "lucky bag"}

OP_NAMES = {0: "card", 1: "skin/face", 2: "talk", 3: "ticket", 4: "clothes", 8: "card sold",
            11: "wardrobe", 12: "lucky bag clothes", 13: "lucky bag wardrobe", 14: "lucky bag song",
            15: "lucky bag crystals"}


class Catalogue:
    def __init__(self, data=None):
        self.data = data or gamedata.load("market.json")
        self.items = self.data["items"]

    def item(self, code):
        return self.items.get(code)

    def price(self, code, vip=False):
        """(currency, amount) of one item"""
        it = self.items[code]
        cur = it["currency"]
        if cur == 0:
            return "credit", it["credit"]
        if cur == 1:
            return "crystal", it["vip_crystal"] if vip and it["vip_crystal"] else it["crystal"]
        return "weticket", it["weticket"]

    def listing(self, cls=None):
        for code in sorted(self.items):
            it = self.items[code]
            if cls is None or it["class"] == cls:
                yield code, it


def parse(data):
    """0x271 -> dict"""
    r = Reader(data)
    t = {"op": r.u8(), "weticket": r.i32(), "crystal": r.i32()}
    n = r.i8()
    t["code"] = r.raw(abs(n)).decode("latin-1")
    op = t["op"]
    if op in (0, 8):
        t["props"] = profile.read_block(r)
    elif op == 1:
        t["skin"], t["face"] = r.u8(), r.u8()
    elif op == 2:
        t["talks"] = profile.read_block(r)
    elif op in (4, 12):
        t["clothes"] = {k: profile.read_block(r) for k in ("head", "upbody", "downbody", "feet", "back")}
    elif op == 11:
        t["caps"] = [r.u8() for _ in range(7)]
    elif op == 13:
        t["cap_luckybag"] = r.u8()
    elif op == 14:
        t["song"] = r.u16()
    return t


def check(cat, p, t):
    """(why the trade is refused or None, a note for the log or None)"""
    if t["weticket"] < 0 or t["crystal"] < 0:
        return "negative balance", None
    if t["op"] not in OP_NAMES:
        return "op %d" % t["op"], None
    code = t["code"]
    it = cat.item(code)
    if not it:
        return "unknown item %r" % code, None
    note = []
    if t["weticket"] > p.get("weticket", 0):
        note.append("WeTickets %d -> %d" % (p.get("weticket", 0), t["weticket"]))
    if it and t["op"] != 8:
        cur, n = cat.price(code, p.get("vip_until", 0) > 0)
        spent = {"crystal": p.get("crystal", 0) - t["crystal"], "weticket": p.get("weticket", 0) - t["weticket"]}
        note.append("%s %s, %s %d (spent %d crystals, %d WeTickets)" % (code, it["name"], cur, n,
                                                                         spent["crystal"], spent["weticket"]))
    return None, "; ".join(note) or None


def apply(p, t):
    """the trade's changes to the profile"""
    b = p["blocks"]
    op = t["op"]
    p["weticket"] = t["weticket"]
    p["crystal"] = t["crystal"]
    if "props" in t:
        b["props"] = t["props"]
    if "skin" in t:
        p["skin"], p["face"] = t["skin"], t["face"]
    if "talks" in t:
        b["talks"] = t["talks"]
    if "clothes" in t:
        b.update(t["clothes"])
    if "caps" in t:
        for k, v in zip(("cap_head", "cap_upbody", "cap_downbody", "cap_feet", "cap_back", "cap_title",
                         "cap_luckybag"), t["caps"]):
            p[k] = v
    if "cap_luckybag" in t:
        p["cap_luckybag"] = t["cap_luckybag"]
    if "song" in t and t["song"] not in p["songs"]:
        p["songs"].append(t["song"])
    p.setdefault("trades", 0)
    p["trades"] += 1
    return OP_NAMES.get(op, str(op))
