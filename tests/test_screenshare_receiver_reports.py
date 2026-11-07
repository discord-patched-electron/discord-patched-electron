import base64
import ctypes
import ctypes.util
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from screenshare_receiver_reports import receiver_reports


@unittest.skipUnless(ctypes.util.find_library("sodium"), "system libsodium is unavailable")
class SyntheticReceiverReports(unittest.TestCase):
    def test_authenticated_native_fields_and_unique_nonces(self):
        packets = [base64.b64decode(value) for value in receiver_reports(9000000, 2)]
        self.assertNotEqual(packets[0][-4:], packets[1][-4:])
        sodium = ctypes.CDLL(ctypes.util.find_library("sodium"))
        decrypt = sodium.crypto_aead_xchacha20poly1305_ietf_decrypt
        decrypt.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        decrypt.restype = ctypes.c_int
        packet = packets[0]
        output = ctypes.create_string_buffer(32)
        length = ctypes.c_ulonglong()

        def decode(header):
            return decrypt(
                output,
                ctypes.byref(length),
                None,
                packet[8:-4],
                len(packet) - 12,
                header,
                len(header),
                packet[-4:] + bytes(20),
                bytes([1]) * 32,
            )

        self.assertEqual(decode(packet[:8]), 0)
        self.assertEqual(struct.unpack("!BBHI", packet[:8]), (0x81, 204, 5, 44))
        self.assertEqual(length.value, 16)
        payload = output.raw[: length.value]
        self.assertEqual(payload[:4], b"0SID")
        self.assertEqual(struct.unpack("!Q", payload[4:12])[0], 2000)
        packed = int.from_bytes(payload[12:15], "big")
        self.assertEqual((packed & 0x3FFFF) << (packed >> 18), 9000000)
        self.assertEqual(payload[15], 0)
        self.assertNotEqual(decode(bytes([packet[0] ^ 1]) + packet[1:8]), 0)

    def test_bounds(self):
        for bitrate, count in ((0, 1), (100000001, 1), (9000000, 0), (9000000, 10001)):
            with self.assertRaises(ValueError):
                receiver_reports(bitrate, count)
