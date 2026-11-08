"""Packet validation and exact synthetic source matching for encoder comparisons."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_encoder_quality import hash32, marker
from extract_screenshare_h264 import frame_nalus


class EncoderQualityTests(unittest.TestCase):
    def test_single_aggregated_and_fragmented_h264(self):
        self.assertEqual(
            frame_nalus(
                [b"\x78\x00\x02\x67\x01\x00\x02\x68\x02", b"\x7c\x85\x03", b"\x7c\x45\x04"]
            ),
            b"\x00\x00\x00\x01\x67\x01\x00\x00\x00\x01\x68\x02\x00\x00\x00\x01\x65\x03\x04",
        )
        self.assertEqual(frame_nalus([b"\x61\x05"]), b"\x00\x00\x00\x01\x61\x05")

    def test_missing_truncated_and_inconsistent_h264_fragments_fail(self):
        for packets in (
            [b""],
            [b"\xe1\x05"],
            [b"\x78\x00"],
            [b"\x78\x00\x00"],
            [b"\x78\x00\x02\x67"],
            [b"\x78\x00\x01\x78"],
            [b"\x7c\x45\x04"],
            [b"\x7c\xc5\x04"],
            [b"\x7c\x85\x03"],
            [b"\x7c\x85\x03", b"\x7c\x46\x04"],
            [b"\x7c\x85\x03", b"\x61\x04"],
        ):
            with self.subTest(packets=packets), self.assertRaises(ValueError):
                frame_nalus(packets)

    def test_hash_matches_gpu_unsigned_shader_operations(self):
        self.assertEqual(hash32(0), 0)
        self.assertEqual(hash32(1), 0x688990C0)
        self.assertEqual(hash32(0xFFFFFFFF), 0x6768824A)

    def test_marker_crc_and_complementary_rows(self):
        width, height, stamp = 400, 100, 0x123456
        values = [0xA7, 0x12, 0x34, 0x56]
        crc = 0
        for value in values:
            crc ^= value
            for _ in range(8):
                crc = ((crc << 1) ^ (7 if crc & 128 else 0)) & 255
        values.append(crc)
        data = bytearray(width * height * 3)
        for bit in range(40):
            x = int((bit + 0.5) * width / 40)
            on = (values[bit // 8] >> (7 - bit % 8)) & 1
            for row in range(2):
                y = int((row + 0.5) * height * 0.06)
                offset = (y * width + x) * 3
                data[offset : offset + 3] = bytes([255 * (on ^ row)]) * 3
        self.assertEqual(marker(data, width, height), stamp)
        data[(3 * width + 5) * 3] ^= 255
        self.assertIsNone(marker(data, width, height))


if __name__ == "__main__":
    unittest.main()
