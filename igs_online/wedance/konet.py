"""KoNet application layer (We Dance Online 2: KoNetAP_MakeSend / KoNetAP_Recv).

A message is u16 header (cmd & 0xFFF | mode << 12, 0x4000 = more fragments)
and a body.  Modes:

  0  plain
  1  AES-ECB:  u32 n, AES(data, u16 crc, zero pad to 16)
  2  zlib:     u32 n, zlib(data), u16 crc
  3  both:     the mode-2 blob wrapped as mode 1

crc = CRC-16/ARC(data) ^ lo16(n) ^ hi16(n).  Bodies over 17998 bytes go in
several KoTCP frames, each with the header; all but the last set 0x4000.
"""
import hmac
import struct
import zlib

from ..common.aes import AES
from ..common.crc16 import crc16

PLAIN, AES_MODE, ZLIB, ZLIB_AES = 0, 1, 2, 3
MORE = 0x4000
FRAGMENT = 18000 - 2

# login 0x11..0x13 and registration 0x21..0x23 (net_iConnect, net_iReg)
STATIC_KEY = b"KONETv0.1IGS2008" + bytes.fromhex("B0DFBB52BF57B44C") + b"ARCADE10"

# net_iInit defaults, used when the IGS027A registration cannot be read
DEFAULT_STORE = 10000000
DEFAULT_KEYCPU = bytes.fromhex("4a0f0000000802069d640000fffbebbf")
DEFAULT_KEYMAC = bytes.fromhex("00145e7ea824")


class CodecError(Exception):
    pass


def blob_crc(data):
    n = len(data)
    return crc16(data) ^ (n & 0xFFFF) ^ (n >> 16 & 0xFFFF)


def en_aes(data, key):
    plain = bytes(data) + struct.pack("<H", blob_crc(data))
    plain += b"\0" * (-len(plain) % 16)
    return struct.pack("<I", len(data)) + AES(key).encrypt(plain)


def de_aes(body, key):
    if len(body) < 20 or (len(body) - 4) % 16:
        raise CodecError("AES body length %d" % len(body))
    n = struct.unpack_from("<I", body)[0]
    plain = AES(key).decrypt(body[4:])
    if n + 2 > len(plain):
        raise CodecError("AES length %d > %d" % (n, len(plain)))
    data = plain[:n]
    if struct.unpack_from("<H", plain, n)[0] != blob_crc(data):
        raise CodecError("AES crc")
    return data


def en_z(data):
    return struct.pack("<I", len(data)) + zlib.compress(bytes(data)) + struct.pack("<H", blob_crc(data))


def de_z(body):
    if len(body) < 6:
        raise CodecError("zlib body length %d" % len(body))
    n = struct.unpack_from("<I", body)[0]
    data = zlib.decompress(body[4:-2])
    if len(data) != n:
        raise CodecError("zlib length %d != %d" % (len(data), n))
    if struct.unpack_from("<H", body, len(body) - 2)[0] != blob_crc(data):
        raise CodecError("zlib crc")
    return data


def encode_body(mode, data, key):
    if mode == PLAIN:
        return bytes(data)
    if mode == AES_MODE:
        return en_aes(data, key)
    if mode == ZLIB:
        return en_z(data)
    if mode == ZLIB_AES:
        return en_aes(en_z(data), key)
    raise CodecError("mode %d" % mode)


def decode_body(mode, body, key):
    if mode == PLAIN:
        return bytes(body)
    if mode == AES_MODE:
        return de_aes(body, key)
    if mode == ZLIB:
        return de_z(body)
    return de_z(de_aes(body, key))


def encode(cmd, mode, data, key):
    """KoTCP frames for one message"""
    body = encode_body(mode, data, key)
    hdr = cmd & 0xFFF | mode << 12
    chunks = [body[i:i + FRAGMENT] for i in range(0, len(body), FRAGMENT)] or [b""]
    return [struct.pack("<H", hdr | (MORE if i < len(chunks) - 1 else 0)) + c for i, c in enumerate(chunks)]


class Decoder:
    """reassembles fragments; feed() returns (cmd, mode, body) or None"""

    def __init__(self):
        self.acc = bytearray()

    def feed(self, frame):
        if len(frame) < 2:
            raise CodecError("short frame")
        hdr = struct.unpack_from("<H", frame)[0]
        self.acc += frame[2:]
        if hdr & 0xC000:
            if len(self.acc) > 6000000:
                raise CodecError("message too long")
            return None
        body, self.acc = bytes(self.acc), bytearray()
        return hdr & 0xFFF, hdr >> 12 & 3, body


def challenge_order(c):
    """the 8 challenge bytes as the HMAC sees them: c0 c2 c4 c6 c1 c3 c5 c7"""
    return bytes([c[0], c[2], c[4], c[6], c[1], c[3], c[5], c[7]])


def login_hmac(challenge, keycpu, keymac):
    """HMAC-MD5 keyed keymac || keycpu (22 bytes) over the reordered challenge"""
    return hmac.new(bytes(keymac) + bytes(keycpu), challenge_order(challenge), "md5").digest()


def account_digits(v):
    """keypad numbers (accounts, passwords): value | digit count << 56"""
    n = v >> 56 & 0xFF
    num = v & ((1 << 56) - 1)
    return str(num).rjust(n, "0") if n else str(num)


def account_value(digits):
    digits = str(digits)
    if not digits.isdigit() or not 1 <= len(digits) <= 16:
        raise ValueError("digits only, 1..16")
    return int(digits) | len(digits) << 56
