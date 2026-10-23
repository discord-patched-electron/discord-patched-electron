"""Exercise the generated recipes as shell and Arch metadata, without launching clients."""
import copy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, read_json
from generate_package import generate
import stage_package


def runtime(channel):
    version = {"stable": "45.0.0", "prerelease": "46.0.0-beta.2", "nightly": "47.0.0-nightly.20261023"}[channel]
    return {"version": version, "runtime_key": "a" * 64, "sha256": "b" * 64,
        "url": "https://github.com/discord-patched-electron/discord-patched-electron/releases/download/test-fixture/runtime.zip"}


class Recipes(unittest.TestCase):
    def test_makepkg_source_symlink_keeps_staging_inside_srcdir(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package-inputs.json").write_text("{}")
            source = root / "src"
            source.mkdir()
            (source / "package-inputs.json").symlink_to(root / "package-inputs.json")
            with patch.object(sys, "argv", ["stage", "--inputs", str(source / "package-inputs.json"), "--asar", "asar"]), patch.object(stage_package, "stage") as stage:
                stage_package.main()
            stage.assert_called_once_with({}, source, "asar")

    def test_all_nine_recipes_have_isolated_paths_and_pinned_binaries(self):
        clients = read_json(ROOT / "config/clients.lock.json")["clients"]
        with tempfile.TemporaryDirectory() as temp:
            names = set()
            for discord, client in clients.items():
                for electron in ("stable", "prerelease", "nightly"):
                    directory = Path(temp) / (discord + "-" + electron)
                    result = generate(runtime(electron), client, electron, 1, directory)
                    names.add(result["package"])
                    launcher = (directory / "discord-launcher.sh").read_text()
                    self.assertIn("root=/usr/lib/" + result["package"], launcher)
                    self.assertIn("DISCORD_USER_DATA_DIR", launcher)
                    subprocess.run(["bash", "-n", str(directory / "PKGBUILD")], check=True)
                    metadata = subprocess.check_output(["bash", "-c",
                        'source "$1"; printf "%s\\n" "$pkgname" "$pkgver"; '
                        'for i in "${!source[@]}"; do printf "%s %s\\n" "${sha256sums[$i]}" "${source[$i]}"; done',
                        "check", str(directory / "PKGBUILD")], text=True)
                    lines = metadata.splitlines()
                    self.assertEqual(lines[0], result["package"])
                    binary_lines = [line for line in lines[2:] if "full.distro" in line or "runtime.zip::" in line]
                    self.assertEqual(len(binary_lines), len(client["sources"]) + 1)
                    self.assertTrue(all(len(line.split()[0]) == 64 for line in binary_lines))
                    if shutil.which("makepkg"):
                        srcinfo = subprocess.check_output(["makepkg", "--printsrcinfo"], cwd=directory, text=True)
                        self.assertIn("pkgname = " + result["package"], srcinfo)
                        self.assertNotIn("conflicts =", srcinfo)
                        self.assertNotIn("depends = electron", srcinfo)
                    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
                    generate(runtime(electron), client, electron, 1, directory)
                    self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()})
            self.assertEqual(len(names), 9)

    def test_rejects_channel_and_module_revision_mismatches(self):
        client = read_json(ROOT / "config/clients.lock.json")["clients"]["ptb"]
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                generate(runtime("stable"), client, "nightly", 1, Path(temp))
            wrong = copy.deepcopy(client)
            wrong["sources"]["discord_voice"]["module_version"] += 1
            with self.assertRaises(ValueError):
                generate(runtime("stable"), wrong, "stable", 1, Path(temp))
            for url in ("https://github.com/a/b/releases/latest/download/runtime.zip", "file:///tmp/runtime.zip"):
                with self.assertRaises(ValueError):
                    generate({**runtime("stable"), "url": url}, client, "stable", 1, Path(temp))


if __name__ == "__main__":
    unittest.main()
