import ctypes
import ctypes.util
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extract_screenshare_av1 import decryptor, frame_obus, leb128


class SyntheticAV1Packets(unittest.TestCase):
    def test_fragmented_obu_and_aggregation_restore_low_overhead_bitstream(self):
        sequence = bytes([1 << 3, 5, 6])
        frame = bytes([6 << 3, 7, 8, 9])
        packets = [
            bytes([0x60]) + leb128(len(sequence)) + sequence + frame[:2],
            bytes([0x90]) + frame[2:],
        ]
        self.assertEqual(frame_obus(packets), b"\x0a\x02\x05\x06\x32\x03\x07\x08\x09")

    def test_missing_fragments_and_corrupt_lengths_fail_instead_of_repairing_content(self):
        for packets in ([b"\x90\x05"], [b"\x50\x30\x07"], [b"\x00\xff"]):
            with self.subTest(packets=packets), self.assertRaises(ValueError):
                frame_obus(packets)

    @unittest.skipUnless(ctypes.util.find_library("sodium"), "system libsodium unavailable")
    def test_rtp_extension_authentication_and_fixed_fixture_key(self):
        sodium = ctypes.CDLL(ctypes.util.find_library("sodium"))
        sodium.sodium_init()
        encrypt = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt
        encrypt.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        encrypt.restype = ctypes.c_int
        header = struct.pack("!BBHIIHH", 0x90, 0xE5, 123, 456, 44, 0xBEDE, 1)
        nonce = struct.pack("!I", 19)
        media = b"\x10\x30\x07"
        plaintext = b"\x10\x01\x00\x00" + media
        ciphertext = ctypes.create_string_buffer(len(plaintext) + 16)
        length = ctypes.c_ulonglong()
        self.assertEqual(
            encrypt(
                ciphertext,
                ctypes.byref(length),
                plaintext,
                len(plaintext),
                header,
                len(header),
                None,
                nonce + bytes(20),
                bytes([1] * 32),
            ),
            0,
        )
        packet = header + ciphertext.raw[: length.value] + nonce
        self.assertEqual(decryptor()(packet), media)
        with self.assertRaisesRegex(ValueError, "authenticate"):
            decryptor()(packet[:-5] + bytes([packet[-5] ^ 1]) + packet[-4:])


if __name__ == "__main__":
    unittest.main()
