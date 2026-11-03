# igs-online

Local stand-in servers for IGS online arcade games, for use with
[igs-loader](https://github.com/Puyodead1/igs-loader).  Members, saved
progress, rankings, events and versus play without the original servers.

| Game | Package | Status |
| --- | --- | --- |
| We Dance Online 2 (E2000) | `igs_online.wedance` | every online message the game has (login, members, account transfer, QR login, market, lucky bags, missions, groups, world tour settings, rankings, store bookkeeping, versus) |
| Percussion Master 3 (E2200) | `igs_online.pm3` | every online message the game has (registration, login, members and sign-up, profiles, play logs, the five ranking boards, event boards and ticker, the notice, hot songs, dragons and dragon 2, contests, store bookkeeping and info, time, game level; a "no update" patch server) |

Python 3.8+ with `cryptography` (AES) and `crcmod` (CRC-16):

```sh
python -m pip install -r requirements.txt
```

`import-data` also needs `lupa` (Lua 5.1, to read the game's lucky bag
script).

## Running

```sh
python -m igs_online wedance                 # listens on 127.0.0.1:10000, database wedance.db
python -m igs_online wedance --listen 0.0.0.0:10000 --db /path/wedance.db serve
python -m igs_online pm3                     # listens on 127.0.0.1:9999, database pm3.db, admin 9997
```

Settings: `--listen` (`IGS_ONLINE_LISTEN`), `--db` (`IGS_ONLINE_DB`),
`--admin` port on 127.0.0.1 (`IGS_ONLINE_ADMIN`, default 10002, 0 = off),
`-v` logs every message (`IGS_ONLINE_LOG=1`).  `serve` also takes
`--store-name first,second` (for cabinets that send no name), `--ticker
text`, and data files replacing the defaults below: `--world`
(`IGS_ONLINE_WORLD`), `--luckybag` (`IGS_ONLINE_LUCKYBAG`), `--missions`
(`IGS_ONLINE_MISSIONS`).

Commands (`qr`, `profit` and `online` talk to a running server):

| Command | |
| --- | --- |
| `serve` | the server (default) |
| `add <account> <password>` | a member account (keypad digits, 6..11 each) |
| `rename <account> <name>` | a member's name |
| `list` | registered stores and members |
| `show <account>` | a member's stored profile (JSON) |
| `give <account> [--crystal n] [--weticket n] [--vip-days n]` | balances, VIP |
| `transfer <account> [--off]` | ask for an account transfer at the next login |
| `group create <name> <leader> [--song id] [--max n]` | a dancer group |
| `group join <group> <account>`, `group leave <account>` | membership |
| `group list`, `group members <group>` | groups, their levels and counters |
| `group set <group> [--name] [--song] [--max] [--leader]`, `group delete <group>` | |
| `store [store]` | stores; with an id its coin settings, text and bookkeeping |
| `market [--class n]` | the market catalogue with prices |
| `missions` | the mission set |
| `world` | the world settings in force now |
| `luckybag [-n n]` | sample lucky bag draws |
| `online` | connected cabinets and who plays on them |
| `qr <account> [--store id]` | QR login: log a member in on a cabinet showing the QR code |
| `profit [--store id]` | ask a cabinet for its bookkeeping now |
| `import-data <WeDo/linux>` | rebuild `data/market.json` and `data/luckybag.json` from a rom set |

## Data files (`igs_online/wedance/data`)

| File | |
| --- | --- |
| `market.json` | the market catalogue: the game's `script/market.db3` (items, currencies, prices, lucky bags, tickets) |
| `luckybag.json` | lucky bag weights and item lists: the game's `script/luckybag.lua.out` (IGS's server-side draw, shipped in the game); how attributes and uses are given is this server's |
| `missions.json` | **invented**: eight missions (song, conditions, bonuses, days) |
| `world.json` | **invented**: `g_wFeature`, world tour seed ("weekly", "daily" or a number) and table, EXP rate and its windows, conditions, ticker, notice |

## Pointing the loader at it

```sh
IGS_NET=127.0.0.1:10000 ./play.ps1 wedanceonline2        # or play-win.ps1, run.sh
```

`IGS_NET=host[:port]` sends the game's server connection there and refuses
every other connection and name lookup.  Without it the game stays
offline.  Details: the loader's `docs/WeDanceOnline2.md`.

Versus needs two cabinets: two Windows loader instances can share one
cache (each gets its own `/tmp`); in WSL give each its own `IGS_CACHE`.
One opens a stage (on the opponent screen, OK on 开新舞台 and a mode),
the other picks it from the list after the stage is open.

The first connection registers the cabinet: the server gives it a store id
and keeps its keys; the cabinet keeps the id in its IGS027A settings
(`info/igs027a.nv`).  A new database does not know that store any more:
delete the cabinet's `info/igs027a.nv` (this also resets its coin settings
and operator password) to register again.

## In the game

On the login screen (after a coin and OK):

- **游客** (guest, left): plays without saving.
- **创造角色** (create, middle): agree, type a new account number (6..11
  digits), OK, a password (6..11 digits), OK, the password again, OK; then
  pick sex, skin, face and name.
- **角色登录** (log in, right): account number (6..11 digits), OK,
  password (4..11), OK.  The QR code shown there works with `qr`.

Up cancels an entry, left deletes a digit.  A password equal to the account
number (or under 6 digits) makes the game ask for a new one after login.
The loader takes digits from the numeric keypad or the digit keys (5 is coin
and 9 service: use W and D for those digits).

After a member's login: the play mode (打击), then 是否进入商城 (enter the
market?), the clothes screen, where keypad keys open the player pages (1
data, 2 schedule, 5 hidden songs, 6 achievements, 7 world tour map, 8
pets, 9 group).  In the market: keypad 1 cards, 2 skins and faces, 3
talks, 4 tickets (Up / Down: workshops, missions, suites, world tour), 5
clothes, 6 lucky bags, 0 leave; Left / Right pick, OK buys.

## What the server does

- KoTCP framing and pings; KoNet modes plain / AES / zlib / zlib+AES,
  fragments; registration (0x21..0x24) and login (0x11..0x14) with the
  HMAC challenge; the login reply carries the world settings.
- Accounts: player, guest and QR login, creation, info and password
  updates, account transfer (0x21C: to a new number, or onto an existing
  account, whose balances and songs move over).
- Profiles in SQLite: every field the game sends is kept (counters,
  wardrobe, talks, props, pets, crystals, WeTickets, VIP, songs owned,
  market flags, last play log); group and mission blocks are built at
  login.
- Market: trades checked against the catalogue (known item, no negative
  balance; prices logged), changes saved; lucky bags drawn from the game's
  own tables.
- Missions: one open mission per member from `missions.json`, progress
  and passes from 0x26E, the song unlocked, 0x161 → 0x162.
- Groups: made from the command line, shown on the group page and the
  versus card; counters from the play logs, level = 1 + credits / 20
  (invented).
- Rankings from stored results: fight and co-op (all time, this month),
  suite score and combo, songs and co-op pairs (this month), stores (this
  week); places pushed after results (0x262, 0x264, 0x26C); song
  popularity (0x13B).
- Store bookkeeping kept and shown (`store`); `profit` asks for it.
- Versus: fighter lists, challenge, accept / refuse, relays of the song
  choice, start / end sync and the in-song stream.
- Update query answered "none"; a file push (0x16F) is never sent.

## Percussion Master 3

```sh
python -m igs_online pm3 [--listen host:port] [--db pm3.db] [--admin 9997] [--events file] [-v] [command]
IGS_NET=127.0.0.1:9999 ./play.ps1 percussionmaster3_m107fa        # or play-win.ps1, run.sh
```

| Command | |
| --- | --- |
| `serve [--patch 9998]` | the server (default); `--patch` also answers `pcli` with "no update" |
| `add <id> <password> [--name n] [--role 0..3]` | a member (keypad digits) |
| `rename`, `password`, `delete`, `lock [--off]` `<id> ...` | a member's nickname, password; delete; lock out |
| `list`, `show <id> [--raw]` | stores and members; a profile and the last play logs |
| `give <id> [--level] [--exp] [--title field:index] [--song id] [--chart id] [--cursor n]` | change a profile |
| `ranks [--kind 0..4] [--class 0..4]` | a ranking board |
| `boards [--set 1,14]` | the two boards that run (`default`: `events.json`'s), pushed to cabinets online |
| `hot [--set 23,102]` | the hot songs (default: the most played of 30 days) |
| `dragons` | this month's dragons |
| `store [id]` | stores; with an id its info and bookkeeping |
| `notice [text] [--default]` | the coin page notice, pushed |
| `online`, `time`, `level <n>`, `storeinfo [--name] [--coins]` | cabinets online; push the time, the game level, the store info |
| `import-data <pm3>` | rebuild `data/boards.json` from the decrypted `/PM3.bin/pm3` |

Data files (`igs_online/pm3/data`):

| File | |
| --- | --- |
| `boards.json` | the board and ticker texts, contest and dragon texts, new-song lists: the game's own samples (`BoardInfo::SetFakeData`) |
| `events.json` | **invented**: which boards run and when, contest song, EXP rates, level rewards, dragons, notice, fallback hot songs, event rewards (titles, cursors, songs) |

In the game: a coin, a drum hit; on the login screen the little drums
move (guest, login, register), the surface or `#` confirms.  Login: ID (6..11
digits), `#`, password (4..8), `#`.  Register: yes, a new ID, `#`, a
password twice; then the name keyboard (keypad 2 4 6 8 move, the surface
types, `*` deletes; left of the first column, down to Finish, `#`; Yes).
Two players log in on one cabinet (versus is local); both are saved.
## Tests

```sh
python -m unittest discover -s tests
```

Protocol notes: [docs/WeDance.md](docs/WeDance.md), [docs/PM3.md](docs/PM3.md).
