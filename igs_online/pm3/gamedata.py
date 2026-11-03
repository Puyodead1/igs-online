"""data/boards.json from the game's executable (the decrypted /PM3.bin/pm3).

pm3 carries a full sample of IGS's event boards and tickers for its own tests
(BoardInfo::SetFakeData, RunWordInfo::SetFakeData): the 19 board kinds with
their texts, the contest and dragon texts, the new-song lists.  These are the
server's board texts; dates and parameters come from events.json.
"""
import json
import os
import struct

DATA = os.path.join(os.path.dirname(__file__), "data")

# virtual addresses in pm3 M107FA
BOARD_KINDS = 0x9540510          # u8[19], BoardInfo::SetFakeData's kinds
BOARD_TEXTS = 0x9540540          # char[19][256], Big5
CONTEST_TEXTS = {11: 0x95402AC, 12: 0x9540310, 13: 0x954036C, 14: 0x95403D8, 22: 0x9540444}
BOARD_SONGS = {24: 0x95404D4, 21: 0x95404E8, 19: 0x95404FC}   # u32[5]
TICKERS = 0x9529C80              # char[128] per kind + 2 (RunWordInfo::SetFakeData)


class Elf:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.d = f.read()
        if self.d[:4] != b"\x7fELF":
            raise ValueError("%s: not an ELF file (give the decrypted /PM3.bin/pm3)" % path)
        phoff, = struct.unpack_from("<I", self.d, 28)
        phnum, = struct.unpack_from("<H", self.d, 44)
        self.segs = []
        for i in range(phnum):
            t, off, va, _, fsz, _ = struct.unpack_from("<6I", self.d, phoff + 32 * i)
            if t == 1:
                self.segs.append((va, off, fsz))

    def read(self, va, n):
        for sva, off, fsz in self.segs:
            if sva <= va < sva + fsz:
                return self.d[off + va - sva:off + va - sva + n]
        raise ValueError("0x%x not in a segment" % va)

    def text(self, va, n=256):
        return self.read(va, n).split(b"\0", 1)[0].decode("big5")


def extract(path):
    e = Elf(path)
    kinds = list(e.read(BOARD_KINDS, 19))
    boards = {}
    for i, k in enumerate(kinds):
        boards[str(k)] = e.text(BOARD_TEXTS + 256 * i)
    for k, va in CONTEST_TEXTS.items():
        boards[str(k)] = e.text(va)
    songs = {str(k): [s for s in struct.unpack("<5I", e.read(va, 20)) if s] for k, va in BOARD_SONGS.items()}
    tickers = {}
    for k in range(1, 24):
        t = e.text(TICKERS + 128 * (k + 2), 128)
        if t:
            tickers[str(k)] = t
    return {"source": "pm3 M107FA, BoardInfo::SetFakeData / RunWordInfo::SetFakeData",
            "boards": dict(sorted(boards.items(), key=lambda kv: int(kv[0]))),
            "songs": songs, "tickers": tickers, "no_event_ticker": e.text(TICKERS + 256, 128)}


def import_all(pm3_path):
    out = os.path.join(DATA, "boards.json")
    os.makedirs(DATA, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(extract(pm3_path), f, indent=1, ensure_ascii=False)
        f.write("\n")
    return [out]
