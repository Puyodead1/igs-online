"""The player profile: login reply 0x212 / QR push 0x21E (RecvPlayerLoginAck),
and the result messages that update it.

Profiles are kept as a dict of named fields (stored as JSON) plus the
wardrobe blocks as text.  Field names follow the game's range-check strings
where it has them; vXXXXXXX names are game globals of unknown meaning,
stored and returned unchanged.
"""
import re

from ..common.packet import Reader, Writer

# login reply, before the blocks (59 bytes)
HEAD = [
    ("uid", "i32"), ("sex", "u8"), ("skin", "u8"), ("face", "u8"), ("name", "s14"),
    ("level", "u16"), ("title", "u16"), ("exp", "i32"), ("logins", "u16"),
    ("won_combo", "u16"), ("won", "u16"), ("lost", "u16"),
    ("coop_won_combo", "u16"), ("coop_won", "u16"), ("coop_lost", "u16"),
    ("world_rank", "u16"), ("hard_combo", "u16"), ("special_combo", "u16"),
    ("cont_combo", "u16"), ("max_cont_combo", "u16"),
    ("npc", "u16"),                     # NPC number | NPC level << 10
    ("ext_info", "i32"),
]

# the wardrobe: (key, colour flag in the login reply)
BLOCKS = [("head", 0), ("upbody", 0), ("downbody", 0), ("feet", 0), ("back", 0), ("title", 1),
          ("talks", None), ("props", None), ("element", 0)]

# after the blocks (95 bytes)
MID = [
    ("hide_songs", "u32"), ("v87903B8", "u32"), ("v87903BC", "u8"), ("strength", "u16"),
    ("coop_rank", "u16"), ("extreme_combo", "u16"), ("v87903C3", "u64"), ("feature", "u16"),
] + [("v%X" % (0x8790466 + 2 * i), "u16") for i in range(7)] + [
    ("v8790474", "i32"),
] + [("v%X" % (0x8790478 + 2 * i), "u16") for i in range(5)] + [
    ("v8790484", "i32"), ("v8790488", "i32"), ("v879048C", "u16"), ("v8790490", "i32"), ("v8790494", "i32"),
]
for _i in range(5):
    MID += [("plus_item%d" % _i, "u8"), ("plus_value%d" % _i, "u16")]
MID += [("v8790498", "u8"), ("v8790499", "u8"), ("v879049C", "i32"), ("v87904A0", "i8"),
        ("v87904A1", "u8"), ("v87904A2", "u8")]

TAIL = [("pet%d" % i, "u16") for i in range(5)] + [
    ("pet_current", "u8"),
    ("fight_score", "i32"), ("v87903AC", "i32"), ("coop_score", "i32"), ("v87903B4", "i32"),
    ("card_id", "s20"), ("account", "u64"), ("user_feature", "u16"), ("weticket", "i32"),
    ("v8790584", "i32"),
    ("cap_head", "u8"), ("cap_upbody", "u8"), ("cap_downbody", "u8"), ("cap_feet", "u8"),
    ("cap_back", "u8"), ("cap_title", "u8"), ("cap_luckybag", "u8"),
    ("v_unused", "i32"), ("v8790580", "i32"),
    ("login_dialog", "u8"),             # > 0: a login dialog (35 or 41)
    ("activity_rate", "u16"),           # 100..1000
    ("v_8F275E0", "u16"),
]

DEFAULTS = {
    "level": 1, "title": 33, "weticket": 25, "activity_rate": 100,
    "cap_head": 5, "cap_upbody": 5, "cap_downbody": 5, "cap_feet": 5, "cap_back": 5, "cap_title": 5,
    "songs": [], "group": 0, "mission": 0,
}

# card_iMakeNoCardPlayer's wardrobe: one item each, title 33
DEFAULT_BLOCKS = {
    "head": "[0,0,0]", "upbody": "[1,0,0]", "downbody": "[1,0,0]", "feet": "[1,0,0]",
    "back": "[0,0,0]", "title": "[33,55555,0]", "talks": "[1][2][3]", "props": "", "element": "",
}

# user_feature bits (word_879057A)
UF_PLUS = 0x01
UF_FROM_OTHERS = 0x02
UF_MODIFY_PASSWORD = 0x10
UF_QR_LOGIN = 0x40
UF_TRANSFER = 0x80


def new_profile(uid, account=0, name=b"", sex=0, skin=0, face=0):
    p = dict(DEFAULTS)
    p.update(uid=uid, account=account, name=bytes(name).hex(), sex=sex, skin=skin, face=face)
    p["songs"] = []
    p["blocks"] = dict(DEFAULT_BLOCKS)
    return p


def _put(w, t, v):
    if t[0] == "s":
        w.str(bytes.fromhex(v) if isinstance(v, str) else (v or b""), int(t[1:]))
    else:
        getattr(w, t)(int(v or 0))


def _get(r, t):
    if t[0] == "s":
        return r.str(int(t[1:])).hex()
    return getattr(r, t)()


def entries(text):
    """the [..] entries of a block"""
    return re.findall(r"\[([^\]]*)\]", text or "")


def recolor(text, delta):
    """add delta to each colour digit of a wardrobe block (the element block
    is sent with flag 1, +5, but read back with flag 0)"""
    out = []
    for e in entries(text):
        f = e.split(",")
        if len(f) == 3:
            f[1] = "".join(str(min(9, max(0, int(c) + delta))) if c.isdigit() else c for c in f[1])
        out.append("[" + ",".join(f) + "]")
    return "".join(out)


def put_first(text, item_id):
    """move the entry with this id to the front (the login reply's first
    entry is the item worn)"""
    es = entries(text)
    for i, e in enumerate(es):
        if e.split(",")[0].strip() == str(item_id):
            es.insert(0, es.pop(i))
            break
    return "".join("[%s]" % e for e in es)


def build(p):
    """0x212 payload"""
    w = Writer()
    for k, t in HEAD:
        _put(w, t, p.get(k, 0))
    for k, _ in BLOCKS:
        text = p["blocks"].get(k, DEFAULT_BLOCKS[k]).encode("latin-1")
        n = len(entries(text.decode("latin-1")))
        w.u8(min(n, 80)).u16(len(text)).raw(text)
    for k, t in MID:
        _put(w, t, p.get(k, 0))
    for k, t in TAIL:
        _put(w, t, p.get(k, 0))
    songs = p.get("songs") or []
    w.u16(len(songs))
    for s in songs:
        w.u16(s)
    w.i32(0)                            # group: none (> 0 adds 68 bytes)
    w.i32(0)                            # mission: none (> 0 adds 36 bytes)
    return w.bytes()


def parse(data):
    """inverse of build(), for tests and the admin tool"""
    r = Reader(data)
    p = {}
    for k, t in HEAD:
        p[k] = _get(r, t)
    p["blocks"] = {}
    for k, _ in BLOCKS:
        r.u8()
        p["blocks"][k] = r.raw(r.u16()).decode("latin-1")
    for k, t in MID + TAIL:
        p[k] = _get(r, t)
    p["songs"] = [r.u16() for _ in range(r.u16())]
    p["group"] = r.i32()
    p["mission"] = r.i32()
    if r.left():
        raise ValueError("%d bytes left" % r.left())
    return p


# ---- updates from the game's result messages --------------------------------------------

def read_block(r):
    r.u8()
    return r.raw(r.u16()).decode("latin-1")


def apply_count(p, data):
    """0x263 SendResultCount (124 bytes)"""
    r = Reader(data)
    p["level"] = r.u16()
    p["title"] = r.u8() or p.get("title", 33)
    p["exp"] = r.i32()
    p["cont_combo"] = r.u16()
    p["max_cont_combo"] = r.u16()
    p["npc"] = r.u16()
    p["ext_info"] = r.i32()
    p["hide_songs"] = r.u32()
    p["v87903B8"] = r.u32()
    p["v87903BC"] = r.u8()
    p["strength"] = r.u16()
    p["v87903C3"] = r.u64()
    for i in range(7):
        p["v%X" % (0x8790466 + 2 * i)] = r.u16()
    for i in range(5):
        p["v%X" % (0x8790478 + 2 * i)] = r.u16()
    p["v8790484"] = r.i32()
    p["v8790488"] = r.i32()
    p["v879048C"] = r.u16()
    p["v8790490"] = r.i32()
    p["v8790494"] = r.i32()
    for i in range(5):
        p["plus_item%d" % i] = r.u8()
        p["plus_value%d" % i] = r.u16()
    p["v8790498"] = r.u8()
    p["v8790499"] = r.u8()
    p["v879049C"] = r.i32()
    p["v87904A0"] = r.i8()
    p["v87904A1"] = r.u8()
    p["v87904A2"] = r.u8()
    r.u16()                             # province level bonus
    p["fight_score"] = r.i32()
    p["v87903AC"] = r.i32()
    p["coop_score"] = r.i32()
    p["v87903B4"] = r.i32()
    p["weticket"] = r.i32()


def apply_clothes(p, data):
    """0x265 SendResultClothes: 6 capacities, head..element blocks, props"""
    r = Reader(data)
    for k in ("cap_head", "cap_upbody", "cap_downbody", "cap_feet", "cap_back", "cap_title"):
        p[k] = r.u8()
    b = p["blocks"]
    for k in ("head", "upbody", "downbody", "feet", "back", "title"):
        b[k] = read_block(r)
    b["element"] = recolor(read_block(r), -5)
    b["props"] = read_block(r)
    _wear(p)


def apply_worn(p, data):
    """0x214 SendPlayerClothes: 7 x u16 worn ids"""
    r = Reader(data)
    p["worn"] = [r.u16() for _ in range(7)]
    _wear(p)


def _wear(p):
    worn = p.get("worn")
    if not worn:
        return
    for k, i in zip(("head", "upbody", "downbody", "feet", "back", "title", "element"), worn):
        p["blocks"][k] = put_first(p["blocks"].get(k, ""), i)


def apply_fight(p, data):
    """0x268 SendResultFight"""
    r = Reader(data)
    for k in ("won_combo", "won", "lost", "coop_won_combo", "coop_won", "coop_lost"):
        p[k] = r.u16()
    for k in ("fight_score", "v87903AC", "coop_score", "v87903B4"):
        p[k] = r.i32()


def apply_pet(p, data):
    """0x26D SendResultPet: u8, u8 slot (1..5), u8, u16 value"""
    if len(data) < 5:
        return
    slot = data[1]
    if 1 <= slot <= 5:
        p["pet_current"] = slot
        p["pet%d" % (slot - 1)] = data[3] | data[4] << 8


def apply_info(p, data):
    """0x219 SendPlayerUpdateInfo / tail of 0x217: sex, skin, face, name[14]"""
    r = Reader(data)
    p["sex"] = r.u8()
    p["skin"] = r.u8()
    p["face"] = r.u8()
    p["name"] = r.str(14).hex()


def name_text(p):
    raw = bytes.fromhex(p.get("name") or "")
    for enc in ("gbk", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.hex()
