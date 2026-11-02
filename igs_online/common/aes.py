"""AES-ECB (FIPS-197) through `cryptography`.  KoNet uses 128- and 256-bit
keys, no IV; the binary's S-box and key schedule are the standard ones."""
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class AES:
    def __init__(self, key):
        self._cipher = Cipher(algorithms.AES(bytes(key)), modes.ECB())

    def encrypt(self, data):
        e = self._cipher.encryptor()
        return e.update(bytes(data)) + e.finalize()

    def decrypt(self, data):
        d = self._cipher.decryptor()
        return d.update(bytes(data)) + d.finalize()
