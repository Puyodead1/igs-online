"""The play log 0x269 (PLAYER_LOG, NetworkManager::SendResultLog 0x80D7E20).

Sent after each credit (and at the next connection for a credit cut off
by a lost link, from info/result_log.dat).  Layout (offsets: PLAYER_LOG):

  i32 uid (0), u8 (4), u8 mode (5: 0 single, 1 co-op, 2 fight, 3 suite,
  4 synthesis, 5, 7 mission), u8 (6), i32 start (8), i32 (12), i32 fighter
  uid (16), u8 (20), u8 (21), u8 won (22), i32 end (24), u8 (28), u16 flags
  (30: low 5 bits errors, high byte connection count), u16 L + the stages
  as text
  fighter > 0: u8 L + the fighter's stages
  member (uid > 10000):
    u8 L + props bought / used ("[n,code]..."), u8 L + text (894),
    u16 L + the lucky bags drawn ("[draw]..."), 7 x u8 wardrobe capacities (head, upper, feet,
    lower, back, titles, lucky bags), 7 x u16 items worn (head, upper,
    lower, feet, back, title, -), i32 experience, u16 level, u16, u16, u16,
    i16, u16, u16, i16, u16 won combos, i16, i32 external info, u8, u16
    strength, i32, i32 crystals, u16 NPC, u16 coins from the mission, i32,
    u8, i32, i8, u8, u16, i32 fight score, i32, i32 co-op score, i32,
    i32 WeTickets, u8, i32 group, u16 user features,
    mode 5: 2 x 4 x u8, i32;
    u16
"""
from ..common.packet import Reader


def _text(r, n):
    return r.raw(n).decode("gbk", "replace")


def parse(data):
    r = Reader(data)
    log = {"uid": r.i32(), "b4": r.u8(), "mode": r.u8(), "b6": r.u8(), "start": r.i32(), "end": r.i32(),
           "fighter": r.i32(), "b20": r.u8(), "b21": r.u8(), "won": r.u8(), "i24": r.i32(), "b28": r.u8(),
           "flags": r.u16()}
    log["stages"] = _text(r, r.u16())
    if log["fighter"] > 0:
        log["fighter_stages"] = _text(r, r.u8())
    if log["uid"] <= 10000:
        return log
    log["props"] = _text(r, r.u8())
    log["text894"] = _text(r, r.u8())
    log["lucky_bags"] = _text(r, r.u16())
    log["caps"] = [r.u8() for _ in range(7)]
    log["worn"] = [r.u16() for _ in range(7)]
    log["exp"] = r.i32()
    log["level"] = r.u16()
    log["w1158"] = r.u16()
    r.u16(), r.u16()
    log["w1168"] = r.i16()
    r.u16(), r.u16()
    log["w1180"] = r.i16()
    log["won_combo"] = r.u16()
    log["w1184"] = r.i16()
    log["ext_info"] = r.i32()
    log["b1192"] = r.u8()
    log["strength"] = r.u16()
    log["i1196"] = r.i32()
    log["crystal"] = r.i32()
    log["npc"] = r.u16()
    log["mission_coins"] = r.u16()
    log["i1208"] = r.i32()
    log["b1212"] = r.u8()
    log["i1220"] = r.i32()
    log["b1224"] = r.i8()
    log["b1233"] = r.u8()
    log["w1234"] = r.u16()
    log["fight_score"] = r.i32()
    log["i1164"] = r.i32()
    log["coop_score"] = r.i32()
    log["i1176"] = r.i32()
    log["weticket"] = r.i32()
    log["b1240"] = r.u8()
    log["group"] = r.i32()
    log["user_feature"] = r.u16()
    if log["mode"] == 5:
        log["mode5"] = list(r.raw(8))
        log["i1216"] = r.i32()
    log["w1242"] = r.u16()
    return log


def group_counter(log):
    """the group counter this credit adds to (groupGradeUpdate 0x80D7D80), or None"""
    if log.get("flags", 0) & 0x1F or log.get("group", 0) <= 0:
        return None
    mode = log["mode"]
    if mode > 2:
        return None
    if not log.get("fighter"):
        mode = 0
    kind = {0: "single", 1: "coop", 2: "fight"}[mode]
    return "%s_%s" % (kind, "won" if log.get("won") else "lost")
