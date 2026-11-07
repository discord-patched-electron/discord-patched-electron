"""Compile the notification policy shipped in the Chromium dependency patch."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MediaSessionPolicy(unittest.TestCase):
    @unittest.skipUnless(shutil.which("c++"), "C++ compiler required")
    def test_application_sounds_and_real_media(self):
        outer = (ROOT / "patches/ports/46/media-content-only.patch").read_text()
        # Extract the added dependency patch, then its added standalone header.
        inner = "\n".join(line[1:] for line in outer.splitlines() if line.startswith("+"))
        header = inner.split(
            "+++ b/third_party/blink/renderer/platform/media/dpe_media_session_policy.h\n", 1
        )[1]
        header = header.split("diff --git", 1)[0]
        header = "\n".join(line[1:] for line in header.splitlines() if line.startswith("+"))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "policy.h").write_text(header + "\n")
            (directory / "check.cc").write_text("""#include "policy.h"
#include <cassert>
int main() {
  using blink::DpeIsApplicationSound;
  for (auto host : {"discord.com", "canary.discord.com", "ptb.discord.com",
                    "cdn.discordapp.com", "cdn.discordapp.net", "static.discord.com"}) {
    assert(DpeIsApplicationSound("https", host, "/assets/ring.ogg", false));
    assert(DpeIsApplicationSound("https", host, "/sounds/message.mp3", false));
    assert(!DpeIsApplicationSound("https", host, "/assets/video.webm", true));
    assert(!DpeIsApplicationSound("https", host, "/attachments/123/voice.ogg", false));
  }
  assert(!DpeIsApplicationSound("https", "media.discordapp.net", "/attachments/voice.ogg", false));
  assert(!DpeIsApplicationSound("blob", "", "/voice", false));
  assert(!DpeIsApplicationSound("https", "www.youtube.com", "/assets/player", true));
  assert(!DpeIsApplicationSound("https", "example.com", "/assets/song.ogg", false));
  assert(!DpeIsApplicationSound("https", "discord.com.example.com", "/assets/sound", false));
  assert(!DpeIsApplicationSound("https", "discord.com", "/assets-not/voice", false));
}
""")
            binary = directory / "check"
            subprocess.run(
                [
                    "c++",
                    "-std=c++20",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(directory / "check.cc"),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(binary)], check=True)
        # Both initial playback and later metadata changes must filter only the
        # browser delegate; the renderer keeps its actual audio-track metadata.
        self.assertEqual(
            inner.count(
                "+  delegate_->DidMediaMetadataChange(delegate_id_, HasMediaSessionAudio(),"
            ),
            1,
        )
        self.assertIn(
            "+      delegate_->DidMediaMetadataChange(delegate_id_, HasMediaSessionAudio(),", inner
        )
        self.assertNotIn("+      delegate_has_audio_, HasVideo(),", inner)
        self.assertEqual(
            inner.count("+" + " " * 36 + "HasVideo(), media::MediaContentType::kPersistent);"), 1
        )
