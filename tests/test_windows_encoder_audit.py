import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_windows_encoder import PEImage, audit


def synthetic_pe():
    data = bytearray(0xA00)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 3)
    struct.pack_into("<H", data, 0x94, 240)
    struct.pack_into("<H", data, 0x98, 0x20B)
    struct.pack_into("<Q", data, 0xB0, 0x180000000)
    for i, (name, rva, offset) in enumerate(
        [(b".text", 0x1000, 0x400), (b".rdata", 0x2000, 0x600), (b".pdata", 0x3000, 0x800)]
    ):
        at = 0x188 + i * 40
        data[at : at + len(name)] = name
        struct.pack_into(
            "<IIII", data, at + 8, 12 if name == b".pdata" else 0x200, rva, 0x200, offset
        )
    # Windows ABI fifth argument then RIP-relative property-name argument.
    code = (
        b"\xc7\x44\x24\x20\x02\x00\x00\x00\x4c\x8d\x05"
        + struct.pack("<i", 0x2000 - 0x100F)
        + b"\xc3"
    )
    data[0x400 : 0x400 + len(code)] = code
    data[0x600:0x60D] = b"QualityPreset"
    struct.pack_into("<III", data, 0x800, 0x1000, 0x1010, 0)
    return data


class WindowsEncoderAuditTests(unittest.TestCase):
    def test_bounded_property_context_is_evidence_not_an_effective_configuration(self):
        report = audit(synthetic_pe())
        self.assertFalse(report["reviewed_module"])
        self.assertFalse(report["runtime_tested"])
        self.assertFalse(report["effective_configuration_verified"])
        refs = report["property_references"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["property"], "QualityPreset")
        self.assertEqual(refs[0]["function_rva"], 0x1000)
        self.assertIn("0x20], 2", refs[0]["context"][0]["instruction"])
        function = report["functions"][str(0x1000)]
        self.assertEqual(function["end_rva"], 0x1010)
        self.assertEqual(function["instructions"][-1]["instruction"], "ret ")

    def test_corrupt_headers_sections_and_unwind_bounds_are_rejected(self):
        for kind in ("signature", "header", "machine", "section", "unwind"):
            data = synthetic_pe()
            if kind == "signature":
                data[0] = 0
            elif kind == "header":
                struct.pack_into("<I", data, 0x3C, 0xFFFFFFFF)
            elif kind == "machine":
                struct.pack_into("<H", data, 0x84, 0x14C)
            elif kind == "section":
                struct.pack_into("<I", data, 0x188 + 20, 0xFFFFFFFF)
            else:
                struct.pack_into("<I", data, 0x804, 0xFFFFFFF0)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                audit(data)

    def test_unmapped_offsets_do_not_become_image_addresses(self):
        image = PEImage(synthetic_pe())
        self.assertIsNone(image.address(0x200))
        self.assertEqual(image.address(0x600), 0x180002000)


if __name__ == "__main__":
    unittest.main()
