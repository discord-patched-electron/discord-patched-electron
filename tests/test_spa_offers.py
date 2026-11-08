"""Qualify Rust offer edits against SPA's real negotiation algorithm."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT


class SpaOffers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.oracle = Path(cls.directory.name) / "spa-offer-check"
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "libpipewire-0.3"], text=True
        ).split()
        subprocess.run(
            [
                "cc",
                "-std=gnu11",
                "-Wall",
                "-Wextra",
                "-Werror",
                *flags,
                str(ROOT / "tests/screenshare/spa-offer-check.c"),
                "-o",
                str(cls.oracle),
            ],
            check=True,
        )
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "dpe-pipewire", "--example", "rate-offer"],
            cwd=ROOT,
            check=True,
        )
        cls.rewrite = ROOT / "out/rust-target/debug/examples/rate-offer"

    def source(self, kind):
        return subprocess.check_output([self.oracle, kind])

    def edited(self, source, fps=60, mode="preference"):
        return subprocess.check_output([self.rewrite, str(fps), mode], input=source)

    def test_preference_keeps_fixed_and_variable_producers_compatible(self):
        original = self.source("native")
        candidate = self.edited(original)
        if subprocess.check_output([self.oracle, "supports-drop"]).strip() == b"0":
            self.assertEqual(
                candidate, original, "legacy SPA must retain compatible original offers"
            )
            return
        self.assertEqual(candidate[8 : len(original)], original[8:])
        self.assertEqual(len(candidate), len(original) + 56)
        for producer, maximum, rate in [
            ("range240", 60, 0),
            ("fixed240", 240, 0),
            ("range30", 30, 0),
            ("absent", 0, 0),
            ("fixed-rate", 0, 60),
        ]:
            with self.subTest(producer=producer):
                value = json.loads(
                    subprocess.check_output([self.oracle, "filter", producer], input=candidate)
                )
                self.assertEqual(
                    value,
                    {
                        "maximum": maximum,
                        "rate": rate,
                        "width": 1920,
                        "height": 1080,
                        "modifier": "0200000028a01f04",
                    },
                )

    def test_fixed_rates_existing_maximum_and_unlimited_stay_unchanged(self):
        for kind in ["fixed", "maximum"]:
            original = self.source(kind)
            self.assertEqual(self.edited(original), original)
        original = self.source("native")
        self.assertEqual(self.edited(original, 0), original)

    def test_malformed_lengths_types_and_property_bounds_are_rejected(self):
        original = self.source("native")
        inputs = [original[:length] for length in [0, 4, 8, 15, len(original) - 1]]
        for offset in [0, 4, 8, 24]:
            data = bytearray(original)
            data[offset : offset + 4] = (0xFFFFFFFF).to_bytes(4, sys.byteorder)
            inputs.append(bytes(data))
        for data in inputs:
            self.assertEqual(
                subprocess.run(
                    [self.rewrite, "60", "preference"], input=data, capture_output=True
                ).returncode,
                2,
            )
