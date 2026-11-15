"""Pinned ASAR packaging must not launch an unrelated system Node executable."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from asar_tool import command
from common import ROOT


class AsarToolTests(unittest.TestCase):
    def test_pack_extract_roundtrip_with_a_broken_node_on_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "bin"
            binary.mkdir()
            node = binary / "node"
            node.write_text("#!/bin/sh\necho unexpected-system-node >&2\nexit 127\n")
            node.chmod(0o700)
            (binary / "bun").symlink_to(shutil.which("bun"))
            environment = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
            source = root / "source"
            source.mkdir()
            (source / "main.cjs").write_text("module.exports = 42;\n")
            archive = root / "app.asar"
            extracted = root / "extracted"
            asar = str(ROOT / "node_modules/.bin/asar")
            subprocess.run(
                command(asar, "pack", str(source), str(archive)),
                env=environment,
                check=True,
                capture_output=True,
                timeout=20,
            )
            subprocess.run(
                command(asar, "extract", str(archive), str(extracted)),
                env=environment,
                check=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(
                (extracted / "main.cjs").read_bytes(), (source / "main.cjs").read_bytes()
            )

    def test_explicit_external_tool_is_preserved(self):
        self.assertEqual(
            command("/custom/asar-wrapper", "pack", "source", "dest"),
            ["/custom/asar-wrapper", "pack", "source", "dest"],
        )
