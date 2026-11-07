"""Camera mirroring uses synthetic planes; never opens a physical device."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_camera_support import build, node_headers
from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from common import ROOT
from elftools.elf.elffile import ELFFile


class CameraFlip(unittest.TestCase):
    def test_native_adapter_preserves_call_abi_and_scopes_mirroring(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            build(directory)
            fixture = directory / "fixture.node"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O2",
                    "-fPIC",
                    "-shared",
                    "-fno-optimize-sibling-calls",
                    "-fno-ipa-cp",
                    "-I" + str(node_headers()),
                    str(ROOT / "tests/camera/flip-fixture.c"),
                    "-o",
                    str(fixture),
                ],
                check=True,
            )
            with fixture.open("rb") as stream:
                elf = ELFFile(stream)
                table = elf.get_section_by_name(".symtab")
                caller = table.get_symbol_by_name("dpe_test_camera")[0]
                converter = table.get_symbol_by_name("dpe_test_convert")[0]["st_value"]
                section = elf.get_section(caller["st_shndx"])
                offset = caller["st_value"] - section["sh_addr"]
                code = section.data()[offset : offset + caller["st_size"]]
                calls = [
                    i.address
                    for i in Cs(CS_ARCH_X86, CS_MODE_64).disasm(code, caller["st_value"])
                    if i.mnemonic == "call" and i.op_str == hex(converter)
                ]
                self.assertEqual(len(calls), 1)
            subprocess.run(
                [
                    "node",
                    "-e",
                    """
                const assert = require('node:assert/strict');
                const [file, helper, call, target] = process.argv.slice(1);
                const fixture = require(file), addon = {exports:{}};
                process.dlopen(addon,helper);
                assert.throws(()=>addon.exports.setEnabled(true), /not initialized/);
                assert.throws(()=>addon.exports.install(file,Number(call)+1,Number(target)), /does not match/);
                addon.exports.install(file,Number(call),Number(target));
                addon.exports.install(file,Number(call),Number(target));
                assert.equal(fixture.check(true,false), true);
                for(const enabled of [true,false,true,false]) {
                    addon.exports.setEnabled(enabled);
                    assert.equal(fixture.check(true,enabled),true);
                    assert.equal(fixture.check(false,false),true);
                }
                assert.throws(()=>addon.exports.setEnabled('yes'));
                """,
                    str(fixture),
                    str(directory / "libdpe-camera-probe.so"),
                    str(calls[0]),
                    str(converter),
                ],
                check=True,
                timeout=20,
            )
