"""CRC-16/ARC (poly 0xA001 reflected, init 0), the IGS Ko library's hash_CRC16."""
import crcmod.predefined

_crc = crcmod.predefined.mkCrcFun("crc-16")


def crc16(data, crc=0):
    return _crc(bytes(data), crc)
