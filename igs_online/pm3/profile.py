"""The PM3 member profile (0x21B, 2748 bytes; GameInfo::vGetPlayerDataFromNet /
vSendPlayerDataToNet) and the play log (0x231, 144 bytes).

Offsets are in the wire message, which starts with the player byte.  The
names in brackets are the game's Lua names (Player, common.lua).
"""
import struct

SIZE = 2748                 # 0x21B: 256 songs per array
SIZE_150 = 2004             # 0x217: the same with 150-entry arrays (never built by the game)
CLASSES = 5                 # Easy, Normal, Hard, Special, Master
SONGS = 256

# field: (offset, struct format, meaning)
FIELDS = {
    "player": (0, "B", "player 0 / 1 on the cabinet"),
    "member_id": (4, "I", "member id (> 0: a member; what rankings need)"),
    "role": (24, "B", "character 0..3 [m_role]"),
    "lv": (25, "B", "level [m_lv]"),
    "exp": (26, "H", "experience [m_exp]"),
    "versus": (28, "H", "versus games [m_versus]"),
    "win": (30, "H", "versus won [m_win]"),
    "max_combo": (32, "H", "best combo [m_maxCombo]"),
    "speed_lv": (1866, "B", "Speed options unlocked, 0..5"),
    "hide_lv": (1867, "B", "Hide options unlocked, 0..5"),
    "sudden_lv": (1868, "B", "Sudden options unlocked, 0..5 (the option is cut from the menus)"),
    "combo100": (1912, "I", "100-combo count [m_100ComboCnt]"),
    "cont_hits": (1916, "I", "roll hits [m_contCnt]"),
    "heavy_hits": (1920, "I", "double hits [m_heavyCnt]"),
    "holiday_cursors": (1924, "I", "holiday cursors hit, all events [m_TotalHolidayCursor]"),
    "class_choice": (1933, "B", "last difficulty chosen 0..4 (as 0x222)"),
    "title": (1939, "B", "title worn: field * ... [m_title]"),
    "speed": (1940, "B", "Speed option in use"),
    "hide": (1941, "B", "Hide option in use"),
    "cursor": (1943, "B", "cursor in use (0..15)"),
    "skill": (1944, "b", "drum skill in use (-1 none)"),
    "sudden": (1945, "B", "Sudden option in use"),
    "flag": (1946, "B", "the game's out-only flag (GameInfo +188436), 0 on receipt"),
    "items": (1947, "B", "item count n (<= 100)"),
}
NAME = (8, 16)              # nickname, Big5 (13 bytes used)
GRADES = 34                 # u8 [5][256]: best grade per class and song (7, 8: full combos [m_bravoNum])
SONGS_GOT = 1314            # u8 [256] by song id: hidden songs unlocked [m_getSong]
CHARTS_GOT = 1570           # u8 [256] by song id: Master charts unlocked [m_getBand]
TITLES = 1826               # s8 [40]: per title field, the best title won (-1 none) [m_getTitle]
CURSORS = 1869              # u8 [16]: cursors unlocked
SKILL_LV = 1885             # s8 [12]: drum skill levels [m_getSkillLv]
SKILL_EXP = 1897            # u8 [12]: drum skill experience [m_getSkillExp]
PER_CLASS = 1928            # u8 [5]: kept as sent (GameInfo Player +7488..7504)
LAST_SONG = 1934            # u8 [5]: last song per difficulty choice [m_selSong]
ITEM_LIST = 1948            # n x 8 bytes (Player +5872)


def new(member_id, name, role=0):
    """a new member: level 1, nothing won"""
    b = bytearray(SIZE)
    struct.pack_into("<I", b, 4, member_id)
    b[8:24] = bytes(name[:13]).ljust(16, b"\0")
    b[24] = role & 3
    b[25] = 1
    b[TITLES:TITLES + 40] = b"\xff" * 40
    b[FIELDS["skill"][0]] = 0xFF
    b[CURSORS] = 1                       # the default cursor
    return bytes(b)


def get(b, field):
    off, fmt, _ = FIELDS[field]
    return struct.unpack_from("<" + fmt, b, off)[0]


def put(b, field, value):
    off, fmt, _ = FIELDS[field]
    struct.pack_into("<" + fmt, b, off, value)


def name(b):
    return bytes(b[NAME[0]:NAME[0] + NAME[1]]).split(b"\0", 1)[0]


def text(raw):
    """Big5 (the game's keyboard), shown"""
    for enc in ("big5", "gbk", "latin-1"):
        try:
            return bytes(raw).decode(enc)
        except UnicodeDecodeError:
            pass
    return bytes(raw).hex()


def titles(b):
    return list(struct.unpack_from("<40b", b, TITLES))


def summary(b):
    """readable fields for `show`"""
    d = {k: get(b, k) for k in FIELDS if k not in ("player",)}
    d["name"] = text(name(b))
    d["titles"] = {i: t for i, t in enumerate(titles(b)) if t >= 0}
    d["songs_unlocked"] = [i for i in range(SONGS) if b[SONGS_GOT + i]]
    d["charts_unlocked"] = [i for i in range(SONGS) if b[CHARTS_GOT + i]]
    d["cursors"] = [i for i in range(16) if b[CURSORS + i]]
    d["skill_lv"] = list(struct.unpack_from("<12b", b, SKILL_LV))
    d["skill_exp"] = list(b[SKILL_EXP:SKILL_EXP + 12])
    d["full_combos"] = [sum(1 for g in b[GRADES + SONGS * c:GRADES + SONGS * (c + 1)] if g in (7, 8))
                        for c in range(CLASSES)]
    d["last_song"] = list(b[LAST_SONG:LAST_SONG + 5])
    d["per_class"] = list(b[PER_CLASS:PER_CLASS + 5])
    return d


def merge_in(stored, sent):
    """a 0x21B save: the game's copy wins, but the member id stays the server's"""
    b = bytearray(sent[:SIZE].ljust(SIZE, b"\0"))
    b[0] = 0
    b[4:8] = stored[4:8]
    return bytes(b)


def grant_title(b, field, index):
    """keep the better title of a field (titles are per field, best index kept)"""
    b = bytearray(b)
    cur = struct.unpack_from("<b", b, TITLES + field)[0]
    if index > cur:
        struct.pack_into("<b", b, TITLES + field, index)
    return bytes(b)


def grant_song(b, song, chart=False):
    b = bytearray(b)
    b[(CHARTS_GOT if chart else SONGS_GOT) + song] = 1
    return bytes(b)


def grant_cursor(b, cursor):
    b = bytearray(b)
    b[CURSORS + cursor] = 1
    return bytes(b)


# ---- 0x231 play log ------------------------------------------------------------------

MODES = {0: "normal", 1: "nonstop", 2: "versus", 3: "versus 3", 4: "versus nonstop"}


def play_log(data):
    """GameInfo::vSendPlayerLog: mode from m_VSType / m_mode; up to 6 stages"""
    d = bytes(data).ljust(144, b"\0")
    p, mode, b2, b3, other = struct.unpack_from("<BBBBI", d)
    cont, _, w10, b12, nstage = struct.unpack_from("<BBHBB", d, 8)
    stages = []
    for i in range(min(6, nstage + 1)):
        o = 16 + 20 * i
        cls, song, score, combo, hp, grade, passed, speed, hide, sudden, cursor, skill = \
            struct.unpack_from("<BBxxIHBBBBBBBB", d, o)
        stages.append({"class": cls, "song": song, "score": score, "max_combo": combo, "hp": hp,
                       "grade": grade, "passed": passed, "speed": speed, "hide": hide, "sudden": sudden,
                       "cursor": cursor, "skill": skill})
    total, best_combo, won = struct.unpack_from("<IHB", d, 136)
    return {"player": p, "mode": mode, "mode_name": MODES.get(mode, mode), "login_value": b2,
            "best_b3": b3, "opponent": other, "continues": cont, "stages": stages, "total": total,
            "best_combo": best_combo, "won_stages": won}


# ---- 0x232 ranking upload -------------------------------------------------------------

def ranking_upload(data):
    """GameInfo::vSendRankingData: kinds 0, 1, 3, 4 the game's totals; 2 the contest song"""
    d = bytes(data).ljust(20, b"\0")
    p, kind, choice, title, cls, song = struct.unpack_from("<BBBBBB", d)
    best_song_score, total, combo, stages = struct.unpack_from("<IIHB", d, 8)
    return {"player": p, "kind": kind, "class": choice, "title": title, "contest_class": cls,
            "contest_song": song, "song_score": best_song_score, "total": total, "max_combo": combo,
            "stages": stages}
