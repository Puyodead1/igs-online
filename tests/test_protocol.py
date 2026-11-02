"""Protocol-layer tests.  Constants marked "binary" were read from the
unpacked We Dance executable (wedo, symbols kept)."""
import asyncio
import hashlib
import hmac
import struct
import unittest
import zlib

from igs_online.common.aes import AES
from igs_online.common.crc16 import crc16
from igs_online.common.kotcp import KoTCP, PING, PONG
from igs_online.common.packet import Reader, Writer
from igs_online.wedance import konet, profile


class Tables(unittest.TestCase):
    def test_crc_table_binary(self):
        # binary: _crc16Table 0x865B2A0
        # (entry i is the CRC of the single byte i)
        raw = bytes.fromhex("0000c1c081c1400101c3c003800241c2")
        self.assertEqual([crc16(bytes([i])) for i in range(8)], list(struct.unpack("<8H", raw)))

    def test_crc_arc_check(self):
        self.assertEqual(crc16(b"123456789"), 0xBB3D)          # CRC-16/ARC check value


class AesVectors(unittest.TestCase):
    PT = bytes.fromhex("00112233445566778899aabbccddeeff")

    def test_fips197_128(self):
        a = AES(bytes(range(16)))
        ct = a.encrypt(self.PT)
        self.assertEqual(ct.hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")
        self.assertEqual(a.decrypt(ct), self.PT)

    def test_fips197_256(self):
        a = AES(bytes(range(32)))
        ct = a.encrypt(self.PT)
        self.assertEqual(ct.hex(), "8ea2b7ca516745bfeafc49904b496089")
        self.assertEqual(a.decrypt(ct), self.PT)

    def test_static_key_binary(self):
        # binary: net_iConnect stores "KONETv0.1IGS2008" then dwords
        # 1388044208, 1286887359, 1094931009, 808535364 and key length 32
        tail = struct.pack("<4I", 1388044208, 1286887359, 1094931009, 808535364)
        self.assertEqual(konet.STATIC_KEY, b"KONETv0.1IGS2008" + tail)
        self.assertEqual(len(konet.STATIC_KEY), 32)

    def test_default_keys_binary(self):
        # binary: net_iInit defaults m_caKeyCPU.. and m_caKeyMAC, word_8EF4564
        self.assertEqual(konet.DEFAULT_KEYCPU, struct.pack("<4i", 3914, 100796416, 25757, -1075053569))
        self.assertEqual(konet.DEFAULT_KEYMAC, struct.pack("<IH", 2120094720, 9384))


class Modes(unittest.TestCase):
    KEY = konet.DEFAULT_KEYCPU

    def test_blob_crc(self):
        data = b"\x01\x02\x03"
        self.assertEqual(konet.blob_crc(data), crc16(data) ^ 3)

    def test_aes_layout(self):
        data = b"hello world"
        body = konet.en_aes(data, self.KEY)
        self.assertEqual(struct.unpack_from("<I", body)[0], len(data))
        self.assertEqual(len(body), 4 + 16)                      # n + 2 rounded up to 16
        plain = AES(self.KEY).decrypt(body[4:])
        self.assertEqual(plain[:11], data)
        self.assertEqual(struct.unpack_from("<H", plain, 11)[0], crc16(data) ^ 11)
        self.assertEqual(plain[13:], b"\0\0\0")
        self.assertEqual(konet.de_aes(body, self.KEY), data)

    def test_aes_block_boundary(self):
        for n in (0, 13, 14, 15, 30, 31, 100):
            data = bytes(range(n))
            body = konet.en_aes(data, konet.STATIC_KEY)
            self.assertEqual((len(body) - 4) % 16, 0)
            self.assertGreaterEqual(len(body) - 4, n + 2)
            self.assertEqual(konet.de_aes(body, konet.STATIC_KEY), data)

    def test_zlib_layout(self):
        data = b"abc" * 50
        body = konet.en_z(data)
        self.assertEqual(struct.unpack_from("<I", body)[0], 150)
        self.assertEqual(zlib.decompress(body[4:-2]), data)
        self.assertEqual(struct.unpack_from("<H", body, len(body) - 2)[0], crc16(data) ^ 150)
        self.assertEqual(konet.de_z(body), data)

    def test_mode3(self):
        data = bytes(range(256)) * 3
        body = konet.encode_body(konet.ZLIB_AES, data, self.KEY)
        inner = konet.de_aes(body, self.KEY)
        self.assertEqual(inner, konet.en_z(data))
        self.assertEqual(konet.decode_body(3, body, self.KEY), data)

    def test_bad_crc(self):
        body = bytearray(konet.en_z(b"xyz"))
        body[-1] ^= 1
        with self.assertRaises(konet.CodecError):
            konet.de_z(bytes(body))


class Framing(unittest.TestCase):
    def test_header(self):
        frames = konet.encode(0x212, konet.AES_MODE, b"\xff", konet.STATIC_KEY)
        self.assertEqual(len(frames), 1)
        self.assertEqual(struct.unpack_from("<H", frames[0])[0], 0x1212)

    def test_fragments(self):
        data = bytes(40000)
        frames = konet.encode(0x16C, konet.PLAIN, data, None)
        self.assertEqual([len(f) for f in frames], [18000, 18000, 2 + 40000 - 2 * 17998])
        hdrs = [struct.unpack_from("<H", f)[0] for f in frames]
        self.assertEqual(hdrs, [0x416C, 0x416C, 0x016C])
        d = konet.Decoder()
        self.assertIsNone(d.feed(frames[0]))
        self.assertIsNone(d.feed(frames[1]))
        self.assertEqual(d.feed(frames[2]), (0x16C, 0, data))

    def test_kotcp_ping(self):
        async def run():
            r = asyncio.StreamReader()
            r.feed_data(struct.pack("<H", PING) + struct.pack("<H", 3) + b"abc")

            class W:
                out = bytearray()
                def write(self, b): self.out += b
                def get_extra_info(self, k): return None
            w = W()
            link = KoTCP(r, w)
            frame = await link.recv()
            return frame, bytes(w.out)
        frame, out = asyncio.run(run())
        self.assertEqual(frame, b"abc")
        self.assertEqual(out, struct.pack("<H", PONG))


class Handshake(unittest.TestCase):
    def test_challenge_order(self):
        self.assertEqual(konet.challenge_order(bytes(range(8))), bytes([0, 2, 4, 6, 1, 3, 5, 7]))

    def test_hmac(self):
        c = bytes.fromhex("0102030405060708")
        key = konet.DEFAULT_KEYMAC + konet.DEFAULT_KEYCPU          # 22 bytes, MAC first
        ref = hmac.new(key, bytes.fromhex("0103050702040608"), hashlib.md5).digest()
        self.assertEqual(konet.login_hmac(c, konet.DEFAULT_KEYCPU, konet.DEFAULT_KEYMAC), ref)

    def test_hmac_md5_rfc2104(self):
        # hash_HMAC_MD5 (0x8097B70) is textbook HMAC: ipad 0x36, opad 0x5C, 64-byte block
        k = bytes([0x0b]) * 16
        self.assertEqual(hmac.new(k, b"Hi There", hashlib.md5).hexdigest(), "9294727a3638bb1c13f48ef8158bfc9d")

    def test_keypad_numbers(self):
        v = konet.account_value("0123456")
        self.assertEqual(v, 123456 | 7 << 56)
        self.assertEqual(konet.account_digits(v), "0123456")


class Profile(unittest.TestCase):
    def test_roundtrip_and_length(self):
        p = profile.new_profile(10001, konet.account_value("123456"), b"DANCER", 1, 2, 3)
        data = profile.build(p)
        # 59 + blocks (3 + text each) + 95 + 10 + 70 + 3 + 4 + 2*songs + 8
        blocks = sum(3 + len(t) for t in profile.DEFAULT_BLOCKS.values())
        self.assertEqual(len(data), 59 + blocks + 95 + 10 + 70 + 3 + 4 + 8)
        q = profile.parse(data)
        self.assertEqual(q["uid"], 10001)
        self.assertEqual(q["level"], 1)
        self.assertEqual(bytes.fromhex(q["name"]).rstrip(b"\0"), b"DANCER")

    def test_recolor_and_worn(self):
        self.assertEqual(profile.recolor("[7,56789,0]", -5), "[7,01234,0]")
        self.assertEqual(profile.put_first("[1,0,0][2,0,0][3,0,0]", 3), "[3,0,0][1,0,0][2,0,0]")

    def test_count_message(self):
        w = Writer().u16(12).u8(40).i32(5000)
        w.raw(bytes(124 - len(w)))
        p = profile.new_profile(10001)
        profile.apply_count(p, w.bytes())
        self.assertEqual((p["level"], p["title"], p["exp"]), (12, 40, 5000))


if __name__ == "__main__":
    unittest.main()
