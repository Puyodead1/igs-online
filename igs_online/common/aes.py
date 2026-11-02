"""AES-ECB (FIPS-197), pure Python.  KoNet uses 128- and 256-bit keys, no IV."""


def _xtime(a):
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _make_tables():
    sbox = [0] * 256
    inv = [0] * 256
    p = q = 1
    while True:                       # generator 3, multiplicative inverse via q = 1/p
        p = p ^ _xtime(p)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        x = (x ^ 0x63) & 0xFF
        sbox[p] = x
        inv[x] = p
        if p == 1:
            break
    sbox[0] = 0x63
    inv[0x63] = 0
    return sbox, inv


SBOX, INV_SBOX = _make_tables()
RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


_M2 = [_mul(i, 2) for i in range(256)]
_M3 = [_mul(i, 3) for i in range(256)]
_M9 = [_mul(i, 9) for i in range(256)]
_M11 = [_mul(i, 11) for i in range(256)]
_M13 = [_mul(i, 13) for i in range(256)]
_M14 = [_mul(i, 14) for i in range(256)]


def expand_key(key):
    nk = len(key) // 4
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 16, 24 or 32 bytes")
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= RCON[i // nk]
        elif nk > 6 and i % nk == 4:
            t = [SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(nr + 1)]


def _encrypt_block(rk, s):
    s = [s[i] ^ rk[0][i] for i in range(16)]
    nr = len(rk) - 1
    for r in range(1, nr + 1):
        s = [SBOX[b] for b in s]
        s = [s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3],
             s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]]
        if r != nr:
            m = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                m += [_M2[a0] ^ _M3[a1] ^ a2 ^ a3, a0 ^ _M2[a1] ^ _M3[a2] ^ a3,
                      a0 ^ a1 ^ _M2[a2] ^ _M3[a3], _M3[a0] ^ a1 ^ a2 ^ _M2[a3]]
            s = m
        k = rk[r]
        s = [s[i] ^ k[i] for i in range(16)]
    return s


def _decrypt_block(rk, s):
    nr = len(rk) - 1
    s = [s[i] ^ rk[nr][i] for i in range(16)]
    for r in range(nr - 1, -1, -1):
        s = [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11],
             s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]]
        s = [INV_SBOX[b] for b in s]
        k = rk[r]
        s = [s[i] ^ k[i] for i in range(16)]
        if r:
            m = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                m += [_M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3], _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3],
                      _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3], _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3]]
            s = m
    return s


class AES:
    def __init__(self, key):
        self.key = bytes(key)
        self._rk = expand_key(self.key)

    def encrypt(self, data):
        if len(data) % 16:
            raise ValueError("AES-ECB data must be a multiple of 16 bytes")
        out = bytearray()
        for i in range(0, len(data), 16):
            out += bytes(_encrypt_block(self._rk, list(data[i:i + 16])))
        return bytes(out)

    def decrypt(self, data):
        if len(data) % 16:
            raise ValueError("AES-ECB data must be a multiple of 16 bytes")
        out = bytearray()
        for i in range(0, len(data), 16):
            out += bytes(_decrypt_block(self._rk, list(data[i:i + 16])))
        return bytes(out)
