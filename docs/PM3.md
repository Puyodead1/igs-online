# Percussion Master 3

The game server of Percussion Master 3 (E2200, M107FA), as `pm3` speaks
to it (`NetManager`).  Addresses are in the decrypted `pm3`; the loader's
`docs/PercussionMaster3_Online.md` has the client side in full.

## Transport

KoTCP frames (`common/kotcp.py`, at most 4096 bytes), each one message:
`u16 cmd`, `u32 n`, n data bytes, `u16` CRC-16/ARC of the data.  No
encryption, no compression.  Ping `0x4BFF` / `0x4FFF` after 10 s, drop
after 60 s.  Odd commands go to the server, even ones come back; the game
handles one message per frame.

The cabinet's store id is in its IGS036 (chip messages 14 / 15 / 16, the
loader's plugin): 0 means registration first.

| Cmd | Dir | Data |
| --- | --- | --- |
| 0x111 | → | 8: `u16` generation, version, edition, area (`machine.cfg`) |
| 0x112 | ← | `u32 a`, `u32 b` |
| 0x113 | → | `char[16]` `"%08X%08X" % (a & b, a \| b)`, CPUID leaf 1 (16), MAC (6) |
| 0x114 | ← | `u32` store id (≤ 0 refused) |
| 0x121 | → | `u32` store id + 0x111's 8 |
| 0x122 / 0x123 | ↔ | as 0x112 / 0x113 |
| 0x124 | ← | `s8` (≥ 0 in; < 0 the game logs in again) |
| 0x411 → 0x412 | | hot songs: `u8 n`, `u8 ids[256]` (select type 4) |
| 0x413 | → | end of login |

A store id this database never gave (a new database) is taken on.

## Commands

**Machine**

| Cmd | Dir | Data | Server |
| --- | --- | --- | --- |
| 0x131 | ← | 25: province, `u16` region, `u32` store, `char[16]` name, coins per credit | not sent (no request exists; 0x139 does the same) |
| 0x132 / 0x133 | → | 32 / 112: a day's / month's bookkeeping | kept, `store <id>` |
| 0x134 | ← | `u32` s, `u32` µs | pushed after 0x124; the game applies it only in its first 6 s (`settimeofday`, `hwclock --systohc`: no-ops in the loaders) |
| 0x135 / 0x136 | → | 0: settings restored / bookkeeping deleted | kept |
| 0x137 | ← | `u16`, stored, never read | admin `word` |
| 0x138 | ← | `u8` game level (`SettingInfo.dat` +74) | `level` |
| 0x139 | ↔ | 36: country, province, `u16` region, `u32` store, `char[24]` name (Big5), coins | kept (ranking entries); `storeinfo` pushes it |
| 0x416 | ← | `u8 n`, n bytes notice (coin page) | pushed after 0x413; `notice` |

**Members** (`p` = player 0 / 1; both can be members on one cabinet)

| Cmd | Dir | Data | Server |
| --- | --- | --- | --- |
| 0x211 → 0x212 | | `p`, `char[16]` ID → `p`, `s8`: 0 free (kept for this player), −2 / −5 taken | |
| 0x213 → 0x21B / 0x214 | | `p`, `char[16]` password, `char[16]` nickname (Big5), `u8` role → the new profile, or `p`, `s8` < 0 | |
| 0x215 → 0x21B / 0x216 | | `p`, ID, password → profile, or `p`, `s8`: −2 / −4 wrong ID or password (asked again once), −5 "this account has been locked", −6 "this account is already logged in" | `lock` |
| 0x217 | ← | the profile with 150-entry arrays (2004) | not sent (0x21B is) |
| 0x218 | → | `p`: logout | |
| 0x219 | → | `p`: sign-up dropped (no nickname) | the ID is freed |
| 0x21A | → | `p`: continue | logged |
| 0x21B | ↔ | 2748: the profile (below) | saved (the member id stays the server's) |
| 0x221 / 0x222 / 0x223 | → | `p`, `u8`: play mode / difficulty choice / last song | 0x222, 0x223 into the profile; 0x221 has no caller |
| 0x231 | → | 144: play log (below) | kept; plays feed the hot songs |

**Rankings** (`netrank`, `InfoMatch`).  Kinds on the wire: 0 normal, 1
battle drum (nonstop), per difficulty; 2 the Knights' song contest (board
13, best score on its song); 3 the score contest (board 11, total); 4 the
combo contest (board 12, best combo).  Contests rank per period (the
board's first day).

| Cmd | Dir | Data | Server |
| --- | --- | --- | --- |
| 0x232 → 0x233 | | 20: `p`, kind, difficulty, title; kinds 0, 1, 3, 4: +12 `u32` total, +16 `u16` combo, +18 stages; kind 2: +4 class, +5 song, +8 `u32` score → 14: `p`, kind, `u16` place before, the best before (kinds 0, 1, 3 +8 `u32`; 2 +4 `u32`; 4 +12 `u16`) | the game shows "new record" from it |
| 0x234 → 0x235 | | `p`, kind → `p`, kind, `u16` place now | |
| 0x291 / 0x293 → +1 | | difficulty, pad, `u16` start → difficulty, pad, start, 5 × 68 | kinds 0 / 1 |
| 0x295 / 0x297 / 0x299 → +1 | | `u16` start → start, pad, 5 × 68 | kinds 2 / 3 / 4 |
| 0x251 / 0x253 → +1 | | difficulty, pad, start, `u16` count (50) → …, `u16` n, n × `char[16]` names | the 200-place tower |
| 0x255 / 0x257 / 0x259 → +1 | | start, count → start, n, names | kinds 2 / 3 / 4 |
| 0x241 / 0x243 → +1 | | difficulty, pad, start → 5 × 60 | no caller; an older page format, answered |
| 0x245 / 0x247 / 0x249 → +1 | | start → 5 × 68 (table layout) | no caller; answered (kinds 2 / 3 / 4) |

Entry (68, as 0x29x send it; the game's table holds it 4 bytes later): +0
`u32` member id, +4 `u32` place, +8 country (`store_list.csv`, else
"OTHER"), +9 province, +10 `u16` level, +12 `char[16]` store name, +28
`u16` combo, +30 stages, +36 `char[16]` nickname, +52 title, +56 `u32`
score (the value shown), +60 `u16` combo (shown for kind 4), +62 song.

**Boards, ticker** (`events.py`)

| Cmd | Dir | Data |
| --- | --- | --- |
| 0x271 → 0x272 | | 2 × 460-byte board |
| 0x273 → 0x274 | | 4 × 272-byte ticker line |

Board: +4 kind, +6 first day, +10 last day, +14 `char[256]` text,
+270..272 three bytes (the game's samples 1, 2, 0), +276 10 × {`u8`, `u8`,
pad, `u32` value}, +356 10 × {`u32` level, `u32`}, +436 `u8[10]` songs
(0xFF ends) or kind 13's song, +448 / +452 `u32` (copied, never read).
The game runs the events itself from these: 18 adds value[0] EXP to a new
member's first game; 15..17, 21 multiply EXP on their songs by value[0] %
(at most 300); 20 gives value[i] EXP on reaching level[i]; holidays put
their cursor into songs and count hits.

| Kind | Board |
| --- | --- |
| 1..10 | holidays: New Year, Labour Day, Dragon Boat, Valentine's, Mid-Autumn, National Day, Singles' Day, Christmas, Children's Day, Women's Day |
| 11 / 12 / 13 | contests: score (Drum Spirit Battle), combo, the Knights' song |
| 14 / 22 | dragons, dragon 2 |
| 15 / 16 / 17 / 21 | classical / pop / original / song festival (EXP rate) |
| 18 / 19 / 20 | sign-up bonus, new songs, level rewards |
| 23 / 24 | PIU / ULTRAWAVE songs |

**Dragons** (board 14; `InfoDragon`, screen 30)

| Cmd | Dir | Data |
| --- | --- | --- |
| 0x281 → 0x282 | | `p` → `p`, pad, 5 difficulties × 10 kinds × {`u32` present, `u8` grade, pad, `u32` time} |
| 0x283 / 0x286 / 0x289 → +1 | | `p`, index 0..2 (0x286: index) → `p`, index, pad, 80-byte dragon |
| 0x285 / 0x28B | → | `p`, index, pad, `u32` result (0x28B: a kind-9 dragon) |
| 0x288 | → | index, pad, `u32` (single-player variant) |
| 0x28C | → | index: the dragon was not fought |

Dragon: +0 `u32` present, +5 kind (0..8: its song 141..149; 9: 20 points),
+8 `u32` points left, +24 province, +44 `char[16]` name, +60 `char[16]`
finder.  Grades: 1..3 seen, 5..7 fought, 8 caught, 9 lost (caught by
another); a difficulty with more than six 8s is complete.  The dragon's
host song is drawn by the game (`SelSong::InitDragonData`): a player who
picks it fights the dragon after the stage.

**Dragon 2** (board 22; `InfoDragon` with `infdragon2`, screen 35)

| Cmd | Dir | Data |
| --- | --- | --- |
| 0x2A1 → 0x2A2 | | `p`, page 2..4 → `p`, page, pad, 3 × 68 {+0 present, +5 slot, +6 grade, +20 country, +48 catcher, +64 `u32` time} ("%s from %s") |
| 0x2A3 → 0x2A4 | | `p`, 2 → `p`, pad, 68 {+0 present, +5 kind, +8 points, +20 country, +48 name} |
| 0x2A5 | → | `p`, pad, `u32` result |
| 0x2A6 / 0x2A7 | → | index: dropped / `p`, index, `u8`: taken on |

## Profile (0x21B, 2748 bytes)

| Off | Size | Field |
| --- | --- | --- |
| 0 | 1 | `p` |
| 4 | 4 | member id |
| 8 | 16 | nickname (13 used) |
| 24 / 25 / 26 | 1 / 1 / 2 | role, level, EXP |
| 28 / 30 / 32 | 2 each | versus games, won, best combo |
| 34 | 5 × 256 | best grade per difficulty and song (7, 8 full combos) |
| 1314 / 1570 | 256 / 256 | hidden songs / Master charts unlocked, by song id |
| 1826 | 40 | `s8` per title field, the best title won (−1 none; `common.lua` `pyTitle`) |
| 1866 / 1867 / 1868 | 1 each | Speed / Hide / Sudden options unlocked (0..5) |
| 1869 | 16 | cursors unlocked |
| 1885 / 1897 | 12 / 12 | drum skill levels (`s8`) / experience |
| 1912..1924 | 4 × 4 | 100-combos, roll hits, double hits, holiday cursors hit (all events) |
| 1928 | 5 | kept as sent (Player +7488..7504) |
| 1933 | 1 | difficulty choice |
| 1934 | 5 | last song per difficulty |
| 1939 | 1 | title worn |
| 1940 / 1941 / 1943 / 1944 / 1945 | 1 each | options in use: Speed, Hide, cursor, skill (`s8`), Sudden |
| 1946 | 1 | out-only flag |
| 1947 / 1948 | 1 / n × 8 | item count, items |

## Play log (0x231, 144 bytes)

+0 `p`, +1 mode (0 normal, 1 nonstop, 2 versus, 3, 4), +2, +3 two counters,
+4 `u32` the other player's member id, +8 continues, +13 stages − 1, +16
up to 6 × 20 {difficulty, song, pad, `u32` score, `u16` combo, HP, grade,
passed, speed, hide, sudden, cursor, skill}, +136 `u32` total, +140 `u16`
best combo, +142 versus: more than one stage won.

## Patch client

`pcli` (not run by the loaders) talks to port 9998 the same way:
0x311 / 0x312 / 0x313 as registration, then 0x331 (newest `u16` version,
edition), 0x332 / 0x333, 0x334 (fetch over FTP), 0x336..0x33A.  `serve
--patch 9998` answers "nothing newer".

## Invented (`data/events.json`)

Which boards run and when, contest song, EXP rates and level rewards,
dragon names and points, the notice text, the fallback hot songs; event
rewards: holiday titles (fields 25..34 at 50 / 300 cursors hit during the
event), contest titles by place (fields 36..38), the Knight cursor for the
Knights' contest, sign-up cursors while board 18 runs, dragon titles
(field 35 at 7 / 10 caught in a difficulty) and dragon songs 150..158.
`data/boards.json` is the game's own (below).

## Shipped data

`pm3` carries IGS's sample boards and tickers (`BoardInfo::SetFakeData`,
`RunWordInfo::SetFakeData`): every board kind's text, the contest and
dragon texts, the new-song lists.  `import-data <pm3>` extracts them to
`data/boards.json`.
