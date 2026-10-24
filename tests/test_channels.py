"""CI selectors and package identity follow the configured channel switches."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from channels import select_discord_channels
from package_release import metadata
from publish_aur import recipe
from releases import release_tag


class Channels(unittest.TestCase):
    def test_defaults_reject_disabled_unknown_and_duplicate_selections(self):
        self.assertEqual(select_discord_channels(), ("canary",))
        for selection in ("ptb", "stable", "unknown", "canary,canary", ""):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                select_discord_channels(selection)

    def test_enabling_channels_reaches_selection_packages_and_aur(self):
        with (
            patch("channels.read_json", return_value={"discord_channels": ["ptb", "canary"]}),
            patch("common.read_json", return_value={"electron_channels": ["stable", "prerelease"]}),
        ):
            self.assertEqual(select_discord_channels(), ("ptb", "canary"))
            config = metadata(Path("/tmp/app"), "20261024.120000", "ptb", 0, "stable")
            self.assertEqual(config["name"], "discord-ptb-stable-patched-electron-bin")
            manifest = {
                "channel": "ptb",
                "electron_version": "46.0.0",
                "version": "20261024.120000",
                "client_version": "1.0.999",
                "release_key": "a" * 64,
                "minimum_glibc": "2.38",
                "assets": {config["name"] + "-linux-x86_64.tar.gz": "b" * 64},
            }
            text = recipe(manifest, release_tag("1.0.999", "a" * 64, "ptb"))
            self.assertIn("pkgname=discord-ptb-stable-patched-electron-bin", text)
            self.assertIn("io.github.discord_patched_electron.DiscordPTB.desktop", text)
            self.assertNotIn("canary", text)
