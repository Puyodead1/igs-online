# We Dance Online 2: the KoNet protocol

From the unpacked executable (`wedo`, symbols kept; addresses are its).
Little-endian; strings fixed-length, zero-padded; `u64` keypad numbers are
`value | digits << 56` (Up on the keypad sends `0x4000000000000000`,
cancel).

## Transport

**KoTCP** (`KoTCP_SendMsg` `0x809A300`, `KoTCP_RecvMsg` `0x8099FC0`):
frames of `u16 len` (1..18000) and `len` bytes.  `len` = `0x4BFF` alone is
a ping, answered `0x4FFF`.  The game pings after 10 s without data and
drops the link after 60 s (`net_iSetKeepalive`, `net_iSetTimeout`).  Its
connect is non-blocking (`select`, then `SO_ERROR`).

**KoNetAP** (`KoNetAP_MakeSend` `0x8077210`, `KoNetAP_Recv` `0x80773B0`):
each frame starts with `u16 cmd & 0xFFF | mode << 12`; `0x4000` (or
`0x8000`) = more fragments follow.  A message's body is the fragments'
bytes after their headers, concatenated; the last header gives cmd and
mode.  The sender cuts bodies into 17998-byte pieces.

| Mode | Body |
| --- | --- |
| 0 | the data |
| 1 | `u32 n`, AES-ECB of (data, `u16 crc`, zeros to a multiple of 16) |
| 2 | `u32 n`, zlib (`compress`), `u16 crc` |
| 3 | the mode-2 body wrapped as mode 1 |

`crc` = CRC-16/ARC (`hash_CRC16`, table `0x865B2A0`) of the data XOR
`lo16(n)` XOR `hi16(n)`.  AES is standard (`aes_Encrypt` `0x808CD40`),
key length 32 or 16.  An empty message is never delivered.

The game's messages go out through `NetworkManager::SendCmd` (`0x80D9DB0`,
72 call sites) and, for 0x164 and 0x172, `net_vSend` directly.  Answers
reach `NetworkManager::ShareCmdProcess` (`0x80DCEE0`, any screen), the
current screen's `OnNetEvent` (login, account, market, versus, play) and
`net_vRecv` (`0x817FBB0`, the login).

## Keys

| | |
| --- | --- |
| Static AES-256 | `KONETv0.1IGS2008`, `B0 DF BB 52 BF 57 B4 4C`, `ARCADE10` (login and registration) |
| Session AES-128 | the cabinet's keycpu (16 bytes) |
| keycpu | CPUID leaf 1 (`eax ebx ecx edx`), bytes 0, 3, 4, 8, 12, 15 replaced by `eth0` MAC bytes |
| keymac | `eth0` MAC (6 bytes) |
| HMAC | HMAC-MD5, key keymac ‖ keycpu (22 bytes), data the 8 challenge bytes in the order `c0 c2 c4 c6 c1 c3 c5 c7` |

`net_iInit` (`0x817F3E0`) reads the 80-byte registration from the IGS027A:
`u32` store id, keycpu, keymac, `u16` net version 1, program version.
Before that, and when the read fails: store 10000000, keycpu
`4a0f0000000802069d640000fffbebbf`, keymac `00145e7ea824`.

## Registration (store id 0; `net_iReg` `0x8181260`)

| Cmd | Dir | Mode, key | Data |
| --- | --- | --- | --- |
| 0x21 | → | 1 static | `u16` net version, `u16` program version |
| 0x22 | ← | 1 static | 16 bytes; the first 8 are the challenge |
| 0x23 | → | 3 static | HMAC[16], keycpu[16], keymac[6] |
| 0x24 | ← | 1 keycpu | `i32` store id (> 0: written to the IGS027A) |

The cabinet then closes the link and logs in.

## Login (`net_iConnect` `0x817F820`, `net_vRecv` `0x817FBB0`)

| Cmd | Dir | Mode, key | Data |
| --- | --- | --- | --- |
| 0x11 | → | 3 static | 42: `u32` store id, `u8` province, area[10], store name[16] (Big5, `info/storename.dat`), `u16` net, area and program versions, `u8` coins, `u8` credits, `u16` store channel, `u8` |
| 0x12 | ← | 1 static | 25: challenge[8], `timeval` (`i32` s, `i32` µs), `u16`, `u16` features (`g_wFeature`), `u16` world seed, `u8` world level, `u8` ping test on, `u8` sign-up reminder |
| 0x13 | → | 1 static | HMAC[16] |
| 0x14 or 0x163 | ← | any, keycpu | 1 byte: 0 = in |

After 0x13 both directions use keycpu.  If the reply's time is between
2008-07-07 and 2023-07-04 the game sets the clock (`settimeofday`,
`hwclock -w`); the server sends the real time, outside that range.

`g_wFeature` bits the game tests: 0x0001 (with the member's user feature 1
clear: no plus items; the group page's missions, achievements and
schedule keys off), 0x0002, 0x0008, 0x0400 (set by the game), 0x1000 and
0x2000 (free play: halved rewards unless VIP), 0x8000 (pets); 0xFFFF:
every online feature closed.  The world seed and level feed
`W2WorldEvt` (`worldrule.lua` `SetSRandSeedAndWorldTable`): the world
tour's rules and stars per province, table 0 easy, 1 normal, 2 hard.

## Players

| Cmd | Dir | Data |
| --- | --- | --- |
| 0x211 | → | guest: `u8` service flag.  Member: card id[20], `u64` account, `u64` password, `u8` service flag |
| 0x212 | ← | profile (below), or 1 byte: `FF` no account, `FE` wrong password, `FD` already in, `FC` database, `FB` state, `FA` version, `F9` account bound |
| 0x213 | → | `u8` 0: log out |
| 0x214 | → | 7 × `u16` items worn: head, upper body, lower body, feet, back, title, element |
| 0x215 | → | `u64` account wanted |
| 0x216 | ← | `i8` 0 free, −1 taken, −2 database, −3 other |
| 0x217 | → | `u64` account, `u64` password, `u8` sex, skin, face, name[14], `u8` |
| 0x218 | ← | 24: `i32` uid, card id[20] (other lengths: failed) |
| 0x219 | → | `u8` sex, skin, face, name[14], `u8` |
| 0x21A | ← | 10: `u16` user features, `i32` uid, `i32` WeTickets; or `i8` error |
| 0x21B | → | `u64` new password |
| 0x21C | → | account transfer (mode 2): `u8` 0 new account / 1 an existing one, `u64` account, `u64` password |
| 0x21D | ← | 26: card id[20], `i32` WeTickets, `u16` user features; or `i8` error (−7 has its own dialog) |
| 0x21E | ← | QR login: a profile, accepted while the login screen's log-in choice is shown |

Required digits (`PDTLogin::Account` `0x81D28F0`): account 6..11,
password 6..11 for a new account, 4..11 to log in.  A new account:
0x215 → 0x216, password twice (checked locally), then the avatar screens
and 0x217 → 0x218.

User feature bits (`u16` in the profile): 1 plus features, 2 transferred
from another game, 0x10 ask for a new password, 0x40 QR login, 0x80
transfer pending.  uid > 10000 is a member.

**Account transfer** (`PDTLogin::login_iArkopaze` `0x81C0160`): after a
login with user feature 0x80 the game asks whether the player already has
an ARKOPAZE (Acropass) account.  Yes: account, password → 0x21C 1.  No: a
new account (0x215 → 0x216), password twice → 0x21C 0.  Success shows
"log in with the ARKOPAZE account next time".

### Profile (0x212, 0x21E; `RecvPlayerLoginAck` `0x81C3F00`)

The length must match the content exactly.

| Off | Field |
| --- | --- |
| 0 | `i32` uid |
| 4 | `u8` sex (0..1), skin (0..3), face |
| 7 | name[14] |
| 21 | `u16` level (1..400), title |
| 25 | `i32` experience |
| 29 | `u16` logins, won combos, wins, losses, co-op won combos, co-op wins, co-op losses, world rank, hard combos, special combos, continuous combos, best continuous combo |
| 53 | `u16` NPC number \| NPC level << 10 |
| 55 | `i32` external info |
| 59 | nine wardrobe blocks: head, upper body, lower body, feet, back, title, talks, props, element |
| | `u32` hidden-song bits, `u32` crystals, `u8`, `u16` strength, `u16` co-op rank, `u16` extreme combos, `u64` market flags (suites), `u16` features (`g_wFeature`), 7 × `u16`, `i32`, 5 × `u16`, `i32`, `i32`, `u16`, `i32`, `i32`, 5 × (`u8`, `u16`) plus items, `u8`, `u8`, `i32`, `i8`, `u8`, `u8` (95 bytes) |
| | 5 × `u16` pets, `u8` current pet |
| | `i32` fight score, `i32`, `i32` co-op score, `i32` |
| | card id[20], `u64` account, `u16` user features, `i32` WeTickets, `i32` VIP end (Unix time; VIP prices, the date on the group page) |
| | 7 × `u8` wardrobe capacities: head, upper, lower, feet, back, titles, lucky bags |
| | `i32`, `i32`, `u8` login dialog, `u16` activity rate (EXP ×1/100, 100..1000), `u16` (> 0: dialog 41) |
| | `u16` n, n × `u16` songs owned |
| | `i32` group; > 0: name[16], `u16` level, leader name[14], `u16` members, `u16` most members, `u16` group song, `u8` song level (0..4), `i32` × 6 fight won, lost, co-op won, lost, single won, lost, `u8`, `i32` |
| | `i32` mission; > 0: `u16` song, `u8` condition A, `u8` condition B, `i32` target A, target B, progress A, progress B, bonus crystals, bonus coins, start, end (Unix times) |

A wardrobe block is `u8` count, `u16` L, L bytes of text: one `[...]`
per entry (`card_iCmdDataDecode` `0x80D4250`).  Clothes, titles and
elements: `id,ccccc,x` (five colour digits, title colours + 5; `x` a
durability, 1..99 or 0).  The first entry is the item worn.  Talks:
`[id]`; props: `[name]`.

**Groups** (舞团, `PDTDancerGroup`, page 9 of the player pages): name,
leader, group song, level, members; the six counters drawn as VS / X /
SOLO / FAIL bars and "Rounds" (their sum).  The game adds to its copy of
the counters as it sends each play log (`groupGradeUpdate` `0x80D7D80`:
flags & 0x1F clear and a group; mode 0..2, no fighter = single; won or
lost) and has no screen that makes or changes a group.

**Missions** (`PDTResultMission`): played with a mission ticket
(任務門票, market ticket type 4, `T0026`..`T0030`), which picks the
mission's song.  Conditions: 1 perfects, 2 perfect + great + cool, 3
plays, 4 max combo; progress A and B are counted by the game and sent with
0x26E.  Passing unlocks the song (or, when the song is already owned,
pays the bonus crystals and coins); the market shows the mission, its
reward, progress and dates.  0x161 (the continue screen) asks whether the
mission still stands; 0x162 with 0 clears it.

## Results (to the server)

| Cmd | Mode | Data | Answer |
| --- | --- | --- | --- |
| 0x261 | 1 | song: `u16` song id, `i32` score, `u8` difficulty, `u8` full combo, `u8` game mode (1 co-op), `u8` | 0x262 |
| 0x263 | 3 | counters, 124: `u16` level, `u8` title, `i32` experience, `u16` continuous and best continuous combos, `u16` NPC, `i32` external info, then the profile's 95-byte block less the features word, `u16` province bonus, `i32` fight score, `i32`, `i32` co-op score, `i32`, `i32` WeTickets | |
| 0x265 | 3 | wardrobe: 6 capacities, head..title blocks, element block (colours + 5), props block | |
| 0x267 | 0 | `u16` song id played | |
| 0x268 | 1 | versus: 6 × `u16` won combos, wins, losses (fight, co-op), 4 × `i32` fight score, `i32`, co-op score, `i32` | 0x264 |
| 0x269 | 3 | play log (below) | |
| 0x26A | 1 | suite (组曲): `i32` total score, `i32` best combo, `u8` | 0x26C |
| 0x26B | 3 | props block | |
| 0x26D | 0 | pet: `u8` 0 chosen / 1 after a song, `u8` current pet (0, 1..5), `u16` its value (0..17999) | |
| 0x26E | 0 | mission: `i32` id, `u8` passed, `u16` song, `i32` progress A, `i32` progress B | |
| 0x271 | 1 | market trade (below) | 0x272 `i8` 0 done, else undone |
| 0x273 | 0 | lucky bag: `u8` 0, `u16` bit 0 has this week's song, bit 1 this month's | 0x274 (below) |
| 0x275 | 2 | `i32` WeTickets left | |

Rank answers (mode 0, any screen): 0x262 `i32` uid, `u16` song, `u8`
place on the song (the result screen; the player's or the fighter's);
0x264 `i32` uid, `u16` world rank (co-op rank in co-op), 0..999; 0x26C
`i32` uid, `u16` suite rank, `u16` combo rank.

**Play log** (`PLAYER_LOG` 0x6F4 bytes, `SendResultLog` `0x80D7E20`),
after each credit and at the next connection for a credit cut off:
`i32` uid, `u8`, `u8` mode (0 single, 1 co-op, 2 fight, 3 suite,
4 synthesis, 5, 7 mission), `u8`, `i32` start, `i32`, `i32` fighter uid,
`u8`, `u8`, `u8` won, `i32` end, `u8`, `u16` flags (low 5 bits errors),
`u16` L + the stages as text; a fighter: `u8` L + its stages; a member:
`u8` L + props used (`[n,code]`), `u8` L + text, `u16` L + lucky bags drawn,
7 × `u8` capacities, 7 × `u16` items worn, `i32` experience, `u16` level,
counters, `i32` crystals, `u16` NPC, `u16` mission coins, scores,
`i32` WeTickets, `u8`, `i32` group, `u16` user features, mode 5: 8 bytes
and an `i32`, `u16` (`playlog.py` has every field).

### Market (`PDTMarket`, `script/market.db3`)

The catalogue is the cabinet's own SQLite file (table `Items`: code,
class, currency, prices, the item's row in its class table); the game
prices and pays locally (`PDTMarket::WritePlayerData` `0x825DAB0`) and
sends one 0x271 per trade with the balances after it:

`u8` op, `i32` WeTickets, `i32` crystals, `i8` L (negative for op 8), the
(last) item's code, then:

| Op | | Then |
| --- | --- | --- |
| 0 | card (prop) bought | props block |
| 1 | skin / face | `u8` skin, `u8` face |
| 2 | talk phrase | talks block |
| 3 | ticket (suite, workshop, world tour, mission) | - |
| 4 | clothes | head, upper, lower, feet, back blocks |
| 8 | card sold (key 9, off in this market) | props block |
| 11 | wardrobe card | 6 capacities, `u8` lucky-bag capacity |
| 12 | lucky bag: clothes | as 4 |
| 13 | lucky bag: wardrobe slot | `u8` lucky-bag capacity (≤ 30) |
| 14 | lucky bag: song | `u16` song id |
| 15 | lucky bag: crystals | - |

Item currency (`Items.kbuymode` as `AccumulateCreditCrystal` reads it): 0
credits, 1 crystals (`getvipcryval` while VIP), 2 WeTickets.  Tickets
also have limits (`lmlevel`, `lmstrong`: suites need strength 75 / 150).

**Lucky bags**: 0x274 `i8` 0, `u16` L, a text the game splits at commas
(`W2LuckyBag::DataDecode` `0x82D86B0`): `1,song`; `2,n` lucky-bag
wardrobe slots; `3,part,id,id2,f1..f5,uses` clothes (part: 0 back, 1 hair,
3 upper, 4 lower, 6 shoes; `id` for sex 1, `id2` for sex 0; flags: speed
up, slow down, spin, hide, reverse; uses 0 = no limit); `4,n` crystals.
The game also carries IGS's server-side draw: `script/luckybag.lua.out`
(`RandomLuckyBag`, Lua 5.1, unused by the cabinet).

## Store bookkeeping (no answer)

| Cmd | Mode | Data |
| --- | --- | --- |
| 0x151 | 0 | `u8` coins, `u8` credits |
| 0x152 | 0 | cable broken: `i32`, `u16` |
| 0x153 | 3 | `u16` L, store settings text |
| 0x154 | | ping results (`nettest_iSendPingData`) |
| 0x164 | 2 | `u16` n, n × `u16` song flags (after 0x163) |
| 0x16D | 0 | `u16` minor version (after a 0x16F file) |
| 0x172 | 3 | 1012: 31 days × 28 bytes (players, game seconds, card logins, continues, fights ...), 12 months × 12 bytes (`bookEnc.bin`), after 0x171 and daily |

## Info and rankings (`ShareCmdProcess`)

Queries are 1 byte 0 unless noted; ranking answers must be mode 2.

| Query | Answer | Data |
| --- | --- | --- |
| 0x137 `u16` song (each song once at boot) | 0x138 | 个人单曲月排行: `u16` song, `u8`, `u16`, `u16`, ≤ 7 × (name[14], `i32` score, area[10], store[16]) |
| 0x145 `u16` song | 0x146 | 合作单曲月排行: `u16` song, `u16`, `u16`, [16], [16], n × (name[14], name[14], `i32`, `u8`, `u8`) |
| 0x131, 0x13D, 0x141, 0x143 | +1 | 对战总排行, 对战月排行, 合作总排行, 合作月排行 (fight / co-op score, all time / this month): 13: `u8` 1, `u8` skin, `u8` face, `u16` head, upper, lower, feet, back of the first; ≤ 30 × 52: `u8` sex, `u16` level, `u16` title, name[14], `u16`, `u8`, area[10], store[16], `i32` value |
| 0x147, 0x149 | +1 | 组曲月排行 (suite score), 组曲连段月排行 (suite best combo): the same 13, then 49-byte entries: `u8` sex, `u16` level, `u16` title, name[14], `i32` value, area[10], store[16] |
| 0x134 | 0x135 | 店家排行: `i32` this store's plays (累计人气), `u8` its place last week (上週), `u8` this week (进榜); n × 30: `u8` province, area[10], store[16], `u16` this week's plays (本週人气), `i8` places gained |
| - | 0x13B | song popularity: `u8` n, n × (`u16` song id, bit 15 falling; `i8`); taken only when n matches and the cabinet has 1..200 songs (this set: 271) |
| 0x161 `u8` 0, `i32` mission | 0x162 | `u16` activity rate, `u8` 0 = the mission is over |
| - | 0x163 | ask for the song flags (0x164) |
| 0x165 | 0x166 | ticker text (1..511 bytes) |
| 0x167 | 0x168 | notice text (1..1023; the title screen) |
| 0x169 | 0x16A | `u8` n, n × (`u8` type, `u16` L, text); the first is parsed as `[id,start,end,a,b,rate]` EXP bonuses (`Extra_ExpBonusAnalyis`), shown nowhere |
| 0x16B MD5[16] of `csvlist.csv` | 0x16C | ≤ 1 byte: keep the list; else MD5[16], `u32` L, the file (written to `sound/music/csvlist.csv`) |
| 0x16E `u8` 0 | 0x16D / 0x16F | no update / a file: `u16` minor version, `u8`, `u16` L, path, `u32` L, MD5 text[32], `u8`, the file |
| - | 0x171 | `u8`: send 0x172 now |

The ranking screen (`PDTShowRanking::Run`) has nine pages: 0 store, 1
song, 2 co-op song, 3 / 4 suite (0x148 / 0x14A), 5..8 0x132, 0x13E, 0x142,
0x144.  In the attract, holding Left and Right together switches it to
manual browsing (Left / Right).

## Versus

The cabinets never talk directly; the server relays.

| Cmd | Dir | Data |
| --- | --- | --- |
| 0x231 | → | open for challengers: `u8` level, `u8` mode, csvlist MD5[5] |
| 0x232 | ↔ | close / abort (any length) |
| 0x223 / 0x222 | → | search all / this store: `u8` level, MD5[5] |
| 0x221 / 0x224 | ← | the answers: fighters, 15 each (the game puts a "no opponent" entry first; any mode other than 1 or 2 empties the list): `i32` uid, `u16` level, `u8` level warp, `u8` province, `u8` mode (1 co-op, 2 fight), `u16` won combos, `u16` strength, `i8`, `u8` |
| 0x225 | → | `i32` uid |
| 0x226 | ← | that player's 144-byte card, or 1 byte (offline) |
| 0x233 | → | challenge `i32` uid |
| 0x237 | ← | to the challenged: the challenger's 144-byte card (`HandleChanllegeInfo`) |
| 0x234 / 0x235 | → then ← | accept / refuse: `i32` uid, `u8` item; relayed to the challenger |
| 0x255 | ↔ | clothes and counters, 67 bytes |
| 0x241 / 0x242 / 0x243 | ↔ | song cursor `u16`; decided `u16`, item[5]; third song `u16` and 3 × `i32` seeds |
| 0x251 / 0x253 | ↔ | ready / end, `u8` 0 |
| 0x252 | → then ←← | start: echoed to both |
| 0x250 | ↔ | in-song stream, mode 0 only: `u8` op; 0 `i32` score, 1 `i32`, 2 `u8` judgement + `u16` combo, 3 pet on, 4 pet off, 5 five `u8` worn, 6 counters, 7 continue, 8 `u16`, 9 abort, 10 `u16` emote, 11 `u8`, `u8` |
| 0x236 | ↔ | continue: `i8` 0 / −1, `u8` |

144-byte card: `i32` uid, `u8` sex, skin, face, name[14], `u16` level,
title, won combos, wins, losses, world rank, 5 × (`u16` item, colours[5])
for head..back, element colours[5] and `u16` id, `u8` province, area[10],
store[16], `u16` store, `u16` strength, `u8`, `i8`, `u8`, `u8` pet, `u16`
pet value, `i32` fight score, `i32` co-op score, `u8` VIP, `u8` plus,
`i32` group, group name[16], `u16` group level (1..400).

## Commands

Every message the game sends or handles, and what this server does.

| Cmd | Dir | | Server |
| --- | --- | --- | --- |
| 0x11, 0x13, 0x21, 0x23 | → | login, registration | answered |
| 0x12, 0x14, 0x22, 0x24 | ← | | sent |
| 0x131, 0x13D, 0x141, 0x143, 0x147, 0x149 | → | player boards | answered from members' stats |
| 0x134 | → | store board | answered from plays per store |
| 0x137, 0x145 | → | song, co-op song boards | answered from scores (this month) |
| 0x13B | ← | song popularity | sent after login (ignored by a cabinet with > 200 songs) |
| 0x151, 0x153 | → | coin settings, store text | kept per store |
| 0x152, 0x154, 0x164, 0x16D, 0x172 | → | bookkeeping | kept |
| 0x161 | → | mission check | 0x162 |
| 0x162 | ← | activity rate, mission | sent |
| 0x163 | ← | song flags query | sent after 0x16C |
| 0x165, 0x167, 0x169, 0x16B | → | ticker, notice, conditions, song list | 0x166, 0x168, 0x16A, 0x16C |
| 0x16E | → | update query | 0x16D: no update |
| 0x16F | ← | a file | never sent |
| 0x171 | ← | bookkeeping query | `profit` command |
| 0x211..0x21C | → | accounts | answered (0x212, 0x216, 0x218, 0x21A, 0x21D) |
| 0x21E | ← | QR login | `qr` command |
| 0x222, 0x223, 0x225 | → | fighter search, card | 0x224, 0x221, 0x226 |
| 0x231, 0x232, 0x233, 0x234, 0x235 | → | contests | kept; 0x237, 0x232, 0x234, 0x235 relayed |
| 0x236, 0x241..0x243, 0x250..0x253, 0x255 | ↔ | versus | relayed (0x252 echoed) |
| 0x261, 0x268, 0x26A | → | results | kept; 0x262, 0x264, 0x26C |
| 0x263, 0x265, 0x267, 0x269, 0x26B, 0x26D, 0x26E, 0x275 | → | results | kept |
| 0x271, 0x273 | → | market, lucky bag | 0x272, 0x274 |

`SendCmd` sites without a constant command: `0x80E1B05` 0x222 / 0x223
(`SendSearchFighter`), `0x81246E1` 0x236 (`SendContestContinue`),
`0x8129EBE` 0x26A, `0x8137AF1` 0x26E, `0x81CC8C6` 0x219, `0x81CCA6B` 0x211
guest, `0x81CCFD4` 0x217 (`PDTCreateName`), `0x8260BFD` 0x271
(`PDTMarket::SendToServer`), `0x82EFF1C` 0x243, `0x80DB577` 0x250
(`NetworkManager::SendFightPass`, ops 0..9); `0x8124884` sends 0x161.

## Stuck-button test

Not network, but it shows on the coin page: each credit adds one to OK's
and each arrow's count in `info/keyerror.dat`; a press seen in 3 frame
reports and a release in 3 more clears it; 4 credits without one show
"button malfunction" until the buttons are pressed
(`Igs036_iKeyTestGameStart` `0x807E080`, `Igs036_iKeyTestRealTime`
`0x807E0F0`).
