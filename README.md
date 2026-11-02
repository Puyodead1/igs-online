# igs-online

Local stand-in servers for IGS online arcade games, for use with
[igs-loader](https://github.com/Puyodead1/igs-loader).  Members, saved
progress, rankings and versus play without the original servers.

| Game | Package | Status |
| --- | --- | --- |
| We Dance Online 2 (E2000) | `igs_online.wedance` | login, members, saved progress, rankings, QR login, versus relay |
| Percussion Master 3 (E2200) | | planned |

Python 3.8+ with `cryptography` (AES) and `crcmod` (CRC-16):

```sh
python -m pip install -r requirements.txt
```

## Running

```sh
python -m igs_online wedance                 # listens on 127.0.0.1:10000, database wedance.db
python -m igs_online wedance --listen 0.0.0.0:10000 --db /path/wedance.db serve
```

Settings: `--listen` (`IGS_ONLINE_LISTEN`), `--db` (`IGS_ONLINE_DB`),
`--admin` port on 127.0.0.1 (`IGS_ONLINE_ADMIN`, default 10002, 0 = off),
`-v` logs every message (`IGS_ONLINE_LOG=1`).  `serve` also takes
`--store-name first,second` (shown in rankings) and `--ticker text`.

Commands (run next to a running server for `qr` and `online`):

| Command | |
| --- | --- |
| `serve` | the server (default) |
| `add <account> <password>` | a member account (keypad digits, 6..11 each) |
| `list` | registered cabinets and members |
| `show <account>` | a member's stored profile (JSON) |
| `online` | connected cabinets and who plays on them |
| `qr <account> [--store id]` | QR login: log a member in on a cabinet showing the QR code |

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
the other picks it from the list.

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

## Implemented

- KoTCP framing and pings; KoNet modes plain / AES / zlib / zlib+AES,
  fragments; registration (0x21..0x24) and login (0x11..0x14) with the
  HMAC challenge; the reply's time is real time.
- Player login, guest login, account creation, info and password updates.
- Profiles in SQLite: counters (0x263), wardrobe (0x265, 0x214), fight
  results (0x268), pets, items, market purchases, WeTickets; raw play logs
  (0x269) and other results kept for later decoding.
- Rankings from stored results: per-song high scores (0x137); player
  rankings by fight score, wins, co-op score, won combos, experience and
  combo (invented mapping); store, co-op and song-pair rankings empty.
- News ticker, notice, conditions: short defaults.  The csvlist query is
  answered so the cabinet keeps its own list; file pushes (0x16F) are never
  sent.
- QR login push (0x21E) from the admin command.
- Versus: fighter lists, challenge, accept / reject, and relays of the song
  choice, start / end sync and the in-song 0x250 stream between two
  cabinets.

## Not implemented

- The market's item catalogue, lucky bags (always "failed"), groups,
  missions, world events, account transfer, store bookkeeping.
- Ranking meanings beyond per-song scores are guesses.

## Tests

```sh
python -m unittest discover -s tests
```

Protocol notes: [docs/WeDance.md](docs/WeDance.md).
