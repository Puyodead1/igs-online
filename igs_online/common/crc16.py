"""CRC-16/ARC (poly 0xA001 reflected, init 0), the IGS Ko library's hash_CRC16."""


def _table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
        t.append(c)
    return t


TABLE = _table()


def crc16(data, crc=0):
    for b in data:
        crc = (crc >> 8) ^ TABLE[(crc ^ b) & 0xFF]
    return crc
