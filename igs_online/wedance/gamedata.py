"""Import the game's own data into the server's data files.

    python -m igs_online wedance import-data <WeDo/linux>

reads script/market.db3 (the market's catalogue, SQLite) and
script/luckybag.lua.out (IGS's lucky bag script, Lua 5.1 bytecode; needs
the `lupa` package) and writes data/market.json and data/luckybag.json.
"""
import json
import os
import sqlite3
import struct

DATA = os.path.join(os.path.dirname(__file__), "data")

# Items columns -> market.json keys (W2Market::DbSelectTotalItem)
ITEM_COLUMNS = {
    "name": "group", "kclassmode": "class", "kbuymode": "currency", "descript": "name",
    "getcryval": "crystal", "getcredval": "credit", "selcryval": "sell", "getweval": "weticket",
    "getvipcryval": "vip_crystal", "Itemidx": "index", "usemode": "use",
}


def import_market(path):
    """market.db3 -> {"items": {code: {...}}, "lucky_bags": {...}, "tickets": {...}}"""
    db = sqlite3.connect(path)
    db.text_factory = lambda b: b.decode("utf-8", "replace")
    db.row_factory = sqlite3.Row
    items = {}
    for r in db.execute("SELECT * FROM Items ORDER BY pkey"):
        it = {new: r[old] for old, new in ITEM_COLUMNS.items()}
        it["name"] = (it["name"] or "").strip()
        items[r["pkey"]] = it
    bags = {str(r["pkey"]): r["name"] for r in db.execute("SELECT * FROM LuckyBagTable")}
    tickets = {str(r["pkey"]): {"name": r["name"], "type": r["type"], "level": r["level"], "stage": r["stage"]}
               for r in db.execute("SELECT * FROM TickerTable")}
    return {"source": "script/market.db3", "items": items, "lucky_bags": bags, "tickets": tickets}


# ---- Lua 5.1 bytecode: the game's luac is 32-bit (size_t 4); a 64-bit Lua wants 8 ----

def _lua_size_t_8(b):
    pos = [12]

    def take(n):
        v = b[pos[0]:pos[0] + n]
        pos[0] += n
        return v

    def i32():
        return struct.unpack("<i", take(4))[0]

    def string(out):
        n = struct.unpack("<I", take(4))[0]
        out += struct.pack("<Q", n) + take(n)

    def function(out):
        string(out)
        out += take(12)
        n = i32()
        out += struct.pack("<i", n) + take(4 * n)
        n = i32()
        out += struct.pack("<i", n)
        for _ in range(n):
            t = take(1)
            out += t
            if t[0] == 1:
                out += take(1)
            elif t[0] == 3:
                out += take(8)
            elif t[0] == 4:
                string(out)
        n = i32()
        out += struct.pack("<i", n)
        for _ in range(n):
            function(out)
        n = i32()
        out += struct.pack("<i", n) + take(4 * n)
        n = i32()
        out += struct.pack("<i", n)
        for _ in range(n):
            string(out)
            out += take(8)
        n = i32()
        out += struct.pack("<i", n)
        for _ in range(n):
            string(out)

    if b[:5] != b"\x1bLua\x51" or b[8] != 4:
        raise ValueError("not a 32-bit Lua 5.1 chunk")
    out = bytearray(b[:12])
    out[8] = 8
    function(out)
    return bytes(out)


def _lua_tables(path, names):
    import lupa.lua51 as lua51       # only needed here
    lua = lua51.LuaRuntime(unpack_returned_tuples=True)
    chunk = _lua_size_t_8(open(path, "rb").read())
    lua.execute("return function(s) return assert(loadstring(s)) end")(chunk)()
    g = lua.globals()

    def conv(v):
        if lua51.lua_type(v) == "table":
            keys = list(v.keys())
            if keys and sorted(keys) == list(range(1, len(keys) + 1)):
                return [conv(v[k]) for k in sorted(keys)]
            return {str(k): conv(v[k]) for k in keys if lua51.lua_type(v[k]) != "function"}
        return None if lua51.lua_type(v) == "function" else v

    return {n: conv(g[n]) for n in names}


def import_luckybag(path):
    """luckybag.lua.out (RandomLuckyBag) -> the tables data/luckybag.json uses"""
    t = _lua_tables(path, [
        "ItemType", "ItemType_NoBox", "SongType", "Song_Week_Item", "Song_Month_Item", "Song_Limit_Item",
        "Song_Normal_Item", "Cloth_Back_Item", "Cloth_Hair_Item", "Cloth_Up_Item", "Cloth_Down_Item",
        "Cloth_Shoe_Item", "Cloth_Back_Type", "ClothAbtTable", "Crystal_Type", "ClothAbtUseTimeUP",
        "ClothAbtUseTimeDown"])
    kinds = {1: "song", 2: "box", 3: "cloth", 4: "crystal"}
    parts = {0: "back", 1: "hair", 3: "up", 4: "down", 6: "shoe"}
    types = []
    for e in t["ItemType"]:
        k = {"kind": kinds[e[0]], "name": e[1], "weight": e[2]}
        if e[0] == 3:
            k["part"] = e[4]
        types.append(k)
    song_keys = {"AA": "week", "AB": "month", "AC": "limited", "AD": "normal"}
    attr_keys = {"A": "special", "B": "double", "C": "single"}
    return {
        "source": "script/luckybag.lua.out (RandomLuckyBag)",
        "types": types,
        "song_types": [{"key": song_keys[e[0]], "name": e[1], "weight": e[2]} for e in t["SongType"]],
        "songs": {
            "week": [{"id": e[0], "name": e[1], "weight": e[2], "week": e[3]} for e in t["Song_Week_Item"]],
            "month": [{"id": e[0], "name": e[1], "weight": e[2], "from": e[3][:2], "to": e[3][2:]}
                      for e in t["Song_Month_Item"]],
            "limited": [{"id": e[0], "name": e[1], "weight": e[2]} for e in t["Song_Limit_Item"]],
            "normal": [{"id": e[0], "name": e[1], "weight": e[2]} for e in t["Song_Normal_Item"]],
        },
        "cloth": {parts[p]: [{"id": e[0], "name": e[1], "weight": e[2], "id2": e[3]} for e in t[name]]
                  for p, name in ((0, "Cloth_Back_Item"), (1, "Cloth_Hair_Item"), (3, "Cloth_Up_Item"),
                                  (4, "Cloth_Down_Item"), (6, "Cloth_Shoe_Item"))},
        "attribute_types": [{"key": attr_keys[e[0][1]], "name": e[1], "weight": e[2]} for e in t["Cloth_Back_Type"]],
        "attributes": t["ClothAbtTable"],
        "uses": [t["ClothAbtUseTimeDown"], t["ClothAbtUseTimeUP"]],
        "crystal": [{"amount": e["5"], "weight": e["3"]} for e in t["Crystal_Type"]],
        "box": 1,
    }


def write(name, data):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return path


def import_all(root):
    """root: the rom set's WeDo/linux"""
    out = [write("market.json", import_market(os.path.join(root, "script", "market.db3")))]
    out.append(write("luckybag.json", import_luckybag(os.path.join(root, "script", "luckybag.lua.out"))))
    return out


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)
