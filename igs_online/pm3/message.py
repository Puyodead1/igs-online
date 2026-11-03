"""PM3 messages inside a KoTCP frame (MessageSend / MessageRecv).

Frame: u16 cmd, u32 n, n data bytes, u16 CRC-16/ARC of the data; at most
4096 bytes.  No encryption, no compression, no fragments.  Odd commands go
to the server, even ones come back.
"""
import struct

from ..common.crc16 import crc16

MAX_FRAME = 4096
HEADER = 6


class MessageError(Exception):
    pass


def encode(cmd, data=b""):
    data = bytes(data)
    frame = struct.pack("<HI", cmd, len(data)) + data + struct.pack("<H", crc16(data))
    if len(frame) > MAX_FRAME:
        raise MessageError("0x%03X: %d bytes" % (cmd, len(frame)))
    return frame


def decode(frame):
    """(cmd, data); a bad length or CRC raises MessageError (the game drops it, -7)"""
    frame = bytes(frame)
    if len(frame) < HEADER + 2:
        raise MessageError("short frame (%d bytes)" % len(frame))
    cmd, n = struct.unpack_from("<HI", frame)
    if HEADER + n + 2 != len(frame):
        raise MessageError("0x%03X: length %d in a %d-byte frame" % (cmd, n, len(frame)))
    data = frame[HEADER:HEADER + n]
    if struct.unpack_from("<H", frame, HEADER + n)[0] != crc16(data):
        raise MessageError("0x%03X: bad CRC" % cmd)
    return cmd, data


def challenge_reply(a, b):
    """0x113 / 0x123 / pcli 0x313: sprintf("%08X%08X", a & b, a | b), 16 chars, no NUL"""
    return ("%08X%08X" % (a & b & 0xFFFFFFFF, (a | b) & 0xFFFFFFFF)).encode()
