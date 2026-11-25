import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from source_patch import apply_source_patch


class SourcePatch(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.checkout = Path(directory) / "checkout"
        self.checkout.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.patch = Path(directory) / "port.patch"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", self.checkout, *args], text=True).strip()

    def prepare(self, base, upstream, patched):
        for name, contents in base.items():
            (self.checkout / name).write_text(contents)
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        for name, contents in patched.items():
            (self.checkout / name).write_text(contents)
        self.patch.write_text(self.git("diff", "--full-index") + "\n")
        self.git("reset", "--hard", "HEAD")
        for name, contents in upstream.items():
            (self.checkout / name).write_text(contents)
        self.git("add", ".")
        self.git("commit", "-qm", "upstream")

    def test_independent_header_insertions_keep_both_sides_and_clean_hunks(self):
        base = '#include "base.h"\n#include "next.h"\n\nint flag = 0;\n'
        upstream = base.replace('#include "next.h"', '#include "upstream.h"\n#include "next.h"')
        patched = base.replace('#include "next.h"', '#include "port.h"\n#include "next.h"')
        self.prepare(
            {"source.cc": base, "other.cc": "int other = 0;\n"},
            {"source.cc": upstream},
            {"source.cc": patched, "other.cc": "int other = 1;\n"},
        )
        passed, _, merged = apply_source_patch(
            self.checkout, self.patch, [], allow_include_merge=True
        )
        self.assertTrue(passed)
        self.assertEqual(merged, ["source.cc"])
        self.assertEqual(self.git("ls-files", "-u"), "")
        expected = upstream.replace('#include "next.h"', '#include "port.h"\n#include "next.h"')
        self.assertEqual(self.git("show", ":source.cc"), expected.strip())
        self.assertEqual(self.git("show", ":other.cc"), "int other = 1;")

    def test_real_code_conflicts_prevent_staging_even_the_resolvable_files(self):
        base = '#include "base.h"\n\nint value = 0;\n'
        self.prepare(
            {"headers.cc": base, "logic.cc": base},
            {"headers.cc": '#include "upstream.h"\n' + base, "logic.cc": base.replace("0", "1")},
            {"headers.cc": '#include "port.h"\n' + base, "logic.cc": base.replace("0", "2")},
        )
        passed, log, merged = apply_source_patch(
            self.checkout, self.patch, [], allow_include_merge=True
        )
        self.assertFalse(passed)
        self.assertIn("logic.cc:", log)
        self.assertEqual(merged, [])
        self.assertIn("headers.cc", self.git("ls-files", "-u"))

    def test_exact_anchor_disables_include_conflict_fallback(self):
        base = '#include "base.h"\n'
        self.prepare(
            {"source.cc": base},
            {"source.cc": '#include "upstream.h"\n' + base},
            {"source.cc": '#include "port.h"\n' + base},
        )
        passed, _, merged = apply_source_patch(
            self.checkout, self.patch, [], allow_include_merge=False
        )
        self.assertFalse(passed)
        self.assertEqual(merged, [])

    def test_conditional_include_conflicts_require_review(self):
        base = '#if PLATFORM\n#include "base.h"\n#endif\n'
        self.prepare(
            {"source.cc": base},
            {
                "source.cc": base.replace(
                    '#include "base.h"', '#include "upstream.h"\n#include "base.h"'
                )
            },
            {
                "source.cc": base.replace(
                    '#include "base.h"', '#include "port.h"\n#include "base.h"'
                )
            },
        )
        passed, log, merged = apply_source_patch(
            self.checkout, self.patch, [], allow_include_merge=True
        )
        self.assertFalse(passed)
        self.assertIn("conditional directive", log)
        self.assertEqual(merged, [])
