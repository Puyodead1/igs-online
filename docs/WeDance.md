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
key length 32 or 16.

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
| 0x11 | → | 3 static | 42: `u32` store id, store name and settings, `u16` net, area and program versions, coin settings, `u16` store channel |
| 0x12 | ← | 1 static | 25: challenge[8], `timeval` (`i32` s, `i32` µs), `u16`, `u16` features (`g_wFeature`), `u16` world seed, `u8` world level, `u8` ping test on, `u8` sign-up reminder |
| 0x13 | → | 1 static | HMAC[16] |
| 0x14 or 0x163 | ← | any, keycpu | 1 byte: 0 = in |

After 0x13 both directions use keycpu.  If the reply's time is between
2008-07-07 and 2023-07-04 the game sets the clock (`settimeofday`,
`hwclock -w`); the server sends the real time, outside that range.

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
| 0x21C / 0x21D | → / ← | account transfer: `u8`, `u64`, `u64` / card id[20], `i32`, `u16` or `i8` |
| 0x21E | ← | QR login: a profile, accepted while the login screen's log-in choice is shown |

Required digits (`PDTLogin::Account` `0x81D28F0`): account 6..11,
password 6..11 for a new account, 4..11 to log in.  A new account:
0x215 → 0x216, password twice (checked locally), then the avatar screens
and 0x217 → 0x218.

User feature bits (`u16` in the profile): 1 plus features, 2 transferred
from another game, 0x10 ask for a new password, 0x40 QR login, 0x80
transfer pending.  uid > 10000 is a member.

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
| | `u32` hidden-song bits, `u32`, `u8`, `u16` strength, `u16` co-op rank, `u16` extreme combos, `u64`, `u16` features, 7 × `u16`, `i32`, 5 × `u16`, `i32`, `i32`, `u16`, `i32`, `i32`, 5 × (`u8`, `u16`) plus items, `u8`, `u8`, `i32`, `i8`, `u8`, `u8` (95 bytes) |
| | 5 × `u16` pets, `u8` current pet |
| | `i32` fight score, `i32`, `i32` co-op score, `i32` |
| | card id[20], `u64` account, `u16` user features, `i32` WeTickets, `i32` |
| | 7 × `u8` wardrobe capacities: head, upper, lower, feet, back, titles, lucky bags |
| | `i32`, `i32`, `u8` login dialog, `u16` activity rate (100..1000), `u16` |
| | `u16` n, n × `u16` songs owned |
| | `i32` group (> 0: 68 more bytes), `i32` mission (> 0: 36 more bytes) |

A wardrobe block is `u8` count, `u16` L, L bytes of text: one `[...]`
per entry (`card_iCmdDataDecode` `0x80D4250`).  Clothes, titles and
elements: `id,ccccc,x` (five colour digits, title colours + 5; `x` a
durability, 1..99 or 0).  The first entry is the item worn.  Talks:
`[id]`; props: `[name]`.

## Results (to the server; none answered)

| Cmd | Mode | Data |
| --- | --- | --- |
| 0x261 | 1 | song: `u16` song id, `i32` score, `u8` difficulty, `u8` full combo, `u8` game mode, `u8` |
| 0x263 | 3 | counters, 124: `u16` level, `u8` title, `i32` experience, `u16` continuous and best continuous combos, `u16` NPC, `i32` external info, then the profile's 95-byte block less the features word, `u16` province bonus, `i32` fight score, `i32`, `i32` co-op score, `i32`, `i32` WeTickets |
| 0x265 | 3 | wardrobe: 6 capacities, head..title blocks, element block (colours + 5), props block |
| 0x267 | 0 | `u16` song id played |
| 0x268 | 1 | fight: 6 × `u16` won combos, wins, losses (single, co-op), 4 × `i32` scores |
| 0x269 | 3 | play log (`PLAYER_LOG`, `SendResultLog` `0x80D7E20`): uid, modes, times, per-stage records as text, then for members the wardrobe and counters; kept raw |
| 0x26A | 1 | combo mode: `i32` total, `i32` best combo, `u8` |
| 0x26B | 3 | props block |
| 0x26D | 0 | pet: `u8`, `u8` slot 1..5, `u8`, `u16` value |
| 0x26E | 0 | mission: `i32` id, `u8` passed, `u16`, `i32`, `i32` |
| 0x271 → 0x272 | 1 | market: `u8` op, `i32`, `i32`, `i8` L, text, then per op blocks / skin and face / capacities / `u16` song; answer `i8` 0 = done |
| 0x273 → 0x274 | 0 | lucky bag: `u8`, `u16` bag; answer `i8` (0: `u16` L, text) |
| 0x275 | 2 | `i32` WeTickets left |

Store bookkeeping (no answer): 0x151 coin settings, 0x152 cable broken,
0x153 settings text, 0x154 ping results, 0x164 song list flags, 0x172 daily
and monthly book (1012 bytes).

## Rankings and news (`NetworkManager::ShareCmdProcess` `0x80DCEE0`)

Queries are 1 byte 0 unless noted; ranking answers must be mode 2.

| Query | Answer | Data |
| --- | --- | --- |
| 0x137 `u16` song (each song once at boot) | 0x138 | `u16` song, `u8`, `u16`, `u16`, ≤ 7 × (name[14], `i32` score, store[10], store[16]) |
| 0x145 `u16` song | 0x146 | `u16` song, `u16`, `u16`, [16], [16], n × (name[14], name[14], `i32`, `u8`, `u8`) |
| 0x131, 0x13D, 0x141, 0x143 | +1 | 13: `u8` 1, `u8` skin, `u8` face, `u16` head, upper, lower, feet, back of the first; ≤ 30 × 52: `u8` sex, `u16` level, `u16` title, name[14], `u16`, `u8`, store[10], store[16], `i32` value |
| 0x147, 0x149 | +1 | the same 13, then 49-byte entries: `u8` sex, `u16` level, `u16` title, name[14], `i32` value, store[10], store[16] |
| 0x134 | 0x135 | `i32`, `u8`, `u8`, n × 30: `u8`, store[10], store[16], `u16`, `i8` |
| 0x165 | 0x166 | ticker text (1..511 bytes) |
| 0x167 | 0x168 | notice text (1..1023) |
| 0x169 | 0x16A | `u8` n, n × (`u8` type, `u16` L, text) |
| 0x16B MD5[16] of `csvlist.csv` | 0x16C | ≤ 1 byte: keep the list; else MD5[16], `u32` L, the file (written to `sound/music/csvlist.csv`) |
| 0x16E | 0x16F | a file: never sent by this server |

## Versus

The cabinets never talk directly; the server relays.

| Cmd | Dir | Data |
| --- | --- | --- |
| 0x231 | → | open for challengers: `u8` level, `u8` mode, csvlist MD5[5] |
| 0x232 | ↔ | close / abort (any length) |
| 0x223 / 0x222 | → | search all / this store: `u8` level, MD5[5] |
| 0x221 / 0x224 | ← | the answers: fighters, 15 each (the game puts a "no opponent" entry first; any mode other than 1 or 2 empties the list): `i32` uid, `u16` level, `u8`, `u8` province, `u8` mode (1 co-op, 2 fight), `u16` won combos, `u16` strength, `u8`, `u8` |
| 0x225 | → | `i32` uid |
| 0x226 | ← | that player's 144-byte card, or 1 byte (offline) |
| 0x233 | → | challenge `i32` uid |
| 0x237 | ← | to the challenged: the challenger's 144-byte card |
| 0x234 / 0x235 | → then ← | accept / refuse: `i32` uid, `u8` item; relayed to the challenger |
| 0x255 | ↔ | clothes and counters, 67 bytes |
| 0x241 / 0x242 / 0x243 | ↔ | song cursor `u16`; decided `u16`, item[5]; third song `u16` and 3 × `i32` seeds |
| 0x251 / 0x253 | ↔ | ready / end, `u8` 0 |
| 0x252 | → then ←← | start: echoed to both |
| 0x250 | ↔ | in-song stream, mode 0 only: `u8` op; 0 `i32` score, 1 `i32`, 2 `u8` judgement + `u16` combo, 3 pet on, 4 pet off, 7 continue, 8 `u16`, 9 abort, 10 `u16` emote, 11 `u8`, `u8` |
| 0x236 | ↔ | continue: `i8` 0 / −1, `u8` |

144-byte card: `i32` uid, `u8` sex, skin, face, name[14], `u16` level,
title, won combos, wins, losses, world rank, 5 × (`u16` item, colours[5])
for head..back, element colours[5] and `u16` id, `u8` province, store[10],
store[16], `u16` store, `u16` strength, `u8`, `i8`, `u8`, `u8` pet, `u16`
pet value, `i32` fight score, `i32` co-op score, `u8`, `u8`, `i32` group,
group name[16], `u16` group level.

## Stuck-button test

Not network, but it shows on the coin page: each credit adds one to OK's
and each arrow's count in `info/keyerror.dat`; a press seen in 3 frame
reports and a release in 3 more clears it; 4 credits without one show
"button malfunction" until the buttons are pressed
(`Igs036_iKeyTestGameStart` `0x807E080`, `Igs036_iKeyTestRealTime`
`0x807E0F0`).
