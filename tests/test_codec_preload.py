"""Exercise real dynamic loading, environment preservation and recovery."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "integration/launcher/codec-preload.sh"
SHELL = 'source "$1"; shift; dpe_restore_codec_environment; dpe_preload_openh264 "$@"; exec "$DPE_TEST_PYTHON" "$DPE_TEST_INSPECT"'


class CodecPreload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="codec tests ")
        cls.root = Path(cls.temp.name)
        cls.codec = cls.root / "codec cache" / "libcodec.so"
        cls.user = cls.root / "user libs" / "libuser.so"
        for library in (cls.codec, cls.user):
            library.parent.mkdir()
            subprocess.run(
                ["cc", "-shared", "-fPIC", "-x", "c", "-", "-o", str(library)],
                input="int dpe_codec_fixture(void) { return 42; }\n",
                text=True,
                check=True,
            )
        cls.inspect = cls.root / "inspect.py"
        cls.inspect.write_text(
            "import ctypes,json,os\n"
            "loaded={}\n"
            "for path in json.loads(os.environ['DPE_TEST_LIBRARIES']):\n"
            "    try:\n"
            "        lib=ctypes.CDLL(path,mode=os.RTLD_NOLOAD)\n"
            "        loaded[path]=lib.dpe_codec_fixture()==42\n"
            "    except OSError:\n"
            "        loaded[path]=False\n"
            "print(json.dumps({'loaded':loaded,'env':{k:v for k,v in os.environ.items() "
            "if k.startswith(('DPE_CODEC_','LD_'))}}))\n"
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_loader(self, flags=(), *, env=None, libraries=None):
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith(("DPE_", "LD_"))
        }
        process = subprocess.run(
            ["bash", "-c", SHELL, "test-codec", str(HELPER), *flags],
            env={
                **environment,
                "DPE_TEST_PYTHON": sys.executable,
                "DPE_TEST_INSPECT": str(self.inspect),
                "DPE_TEST_LIBRARIES": json.dumps([str(p) for p in libraries or [self.codec]]),
                **(env or {}),
            },
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        return json.loads(process.stdout), process.stderr

    def flags(self, path=None):
        return ["--enable-libopenh264", "--openh264-library-path=" + str(path or self.codec)]

    def test_preloads_real_library_from_directory_with_spaces_and_preserves_user_loader(self):
        result, errors = self.run_loader(
            self.flags(),
            env={"LD_PRELOAD": self.user.name, "LD_LIBRARY_PATH": str(self.user.parent)},
            libraries=[self.codec, self.user],
        )
        self.assertEqual(errors, "")
        self.assertTrue(all(result["loaded"].values()))
        self.assertEqual(result["env"]["DPE_CODEC_ORIGINAL_LD_PRELOAD"], self.user.name)
        self.assertEqual(result["env"]["DPE_CODEC_ORIGINAL_LD_LIBRARY_PATH"], str(self.user.parent))

    def test_relaunch_removes_our_preload_for_disabled_codec_and_recovery(self):
        original, _ = self.run_loader(
            self.flags(),
            env={"LD_PRELOAD": self.user.name, "LD_LIBRARY_PATH": str(self.user.parent)},
        )
        for args, extra in [
            ([], {}),
            (self.flags(), {"DPE_IGNORE_FLAGS": "1"}),
            (self.flags() + ["--ignore-flags"], {}),
        ]:
            with self.subTest(args=args, extra=extra):
                result, errors = self.run_loader(
                    args,
                    env={**original["env"], **extra},
                    libraries=[self.codec, self.user],
                )
                self.assertEqual(errors, "")
                self.assertFalse(result["loaded"][str(self.codec)])
                self.assertTrue(result["loaded"][str(self.user)])
                self.assertEqual(result["env"]["LD_PRELOAD"], self.user.name)
                self.assertNotIn("DPE_CODEC_PRELOAD_ACTIVE", result["env"])

    def test_repeated_relaunch_does_not_accumulate_preloads(self):
        first, _ = self.run_loader(self.flags())
        again, _ = self.run_loader(self.flags(), env=first["env"])
        self.assertEqual(first, again)

    def test_symlink_name_remains_available_to_later_dlopen(self):
        alias = self.root / "libalias.so"
        alias.symlink_to(self.codec)
        result, errors = self.run_loader(self.flags(alias), libraries=[alias])
        self.assertEqual(errors, "")
        self.assertTrue(result["loaded"][str(alias)])

    def test_last_path_wins_and_missing_library_does_not_prevent_startup(self):
        missing = self.root / "missing.so"
        result, errors = self.run_loader(self.flags() + ["--openh264-library-path=" + str(missing)])
        self.assertFalse(result["loaded"][str(self.codec)])
        self.assertIn("readable OpenH264 library", errors)
        result, errors = self.run_loader(
            self.flags(missing) + ["--openh264-library-path=" + str(self.codec)]
        )
        self.assertTrue(result["loaded"][str(self.codec)])
        self.assertEqual(errors, "")

    def test_library_path_alone_does_not_enable_codec(self):
        result, errors = self.run_loader(["--openh264-library-path=" + str(self.codec)])
        self.assertFalse(result["loaded"][str(self.codec)])
        self.assertEqual(errors, "")

    def test_unrepresentable_loader_path_is_not_split_or_evaluated(self):
        for name in ("bad name.so", "bad:alias.so", "$ORIGIN.so"):
            with self.subTest(name=name):
                alias = self.root / name
                alias.symlink_to(self.codec)
                result, errors = self.run_loader(self.flags(alias))
                self.assertFalse(result["loaded"][str(self.codec)])
                self.assertIn("Cannot preload", errors)
