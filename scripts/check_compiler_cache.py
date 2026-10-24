"""Exercise real cache hits and invalidation, using a small disposable C fixture."""

import argparse
import subprocess
import tempfile
from pathlib import Path

from common import ROOT, read_json, run, sha256, write_json
from compiler_cache import compiler_cache


def check(compiler: Path, output: Path):
    config = read_json(ROOT / "config/candidate-build.json")
    with tempfile.TemporaryDirectory(prefix="dpe-cache-check-") as temp:
        root = Path(temp)
        source = root / "source"
        source.mkdir()
        header = source / "value.h"
        header.write_text("#define VALUE 1\n")
        (source / "fixture.c").write_text("""#include "value.h"
#if __has_include("optional.h")
#include "optional.h"
#else
#define EXTRA 0
#endif
#ifdef __AVX2__
#define ISA 3
#else
#define ISA 2
#endif
int answer(void) { return VALUE + ISA + EXTRA; }
""")
        flags = config["cpu_flags"]
        macros = subprocess.check_output(
            [str(compiler), *flags, "-dM", "-E", "-x", "c", "/dev/null"], text=True
        )
        required = [
            "__AVX2__",
            "__FMA__",
            "__BMI__",
            "__BMI2__",
            "__F16C__",
            "__LZCNT__",
            "__MOVBE__",
        ]
        if (
            any(f"#define {name} 1" not in macros for name in required)
            or "#define __AVX512F__" in macros
        ):
            raise ValueError(
                "Compiler does not implement the requested v3 baseline without AVX-512"
            )
        with compiler_cache(root / "cache", source, output) as wrapper:

            def compile(cpu_flags):
                run(
                    wrapper,
                    compiler,
                    *cpu_flags,
                    "-O2",
                    "-c",
                    "fixture.c",
                    "-o",
                    "fixture.o",
                    cwd=source,
                )
                return sha256(source / "fixture.o")

            first = compile(flags)
            second = compile(flags)
            if first != second:
                raise ValueError("Cached object differs from uncached object")
            header.write_text("#define VALUE 100\n")
            edited = compile(flags)
            changed_cpu = compile(["-march=x86-64-v2", "-mtune=generic"])
            (source / "optional.h").write_text("#define EXTRA 1000\n")
            appeared = compile(flags)
            if len({first, edited, changed_cpu, appeared}) != 4:
                raise ValueError(
                    "Cache failed to invalidate changed headers, CPU flags or a newly appearing header"
                )
        stats = read_json(output / "compiler-cache.json")["stats"]
        hits = sum(stats["cache_hits"]["counts"].values())
        misses = sum(stats["cache_misses"]["counts"].values())
        if hits != 1 or misses != 4 or stats["compile_fails"] or stats["cache_write_errors"]:
            raise ValueError(f"Expected one cache hit and four misses: {stats}")
        write_json(output / "invalidation-stats.json", read_json(output / "compiler-cache.json"))
        # Restart the server on the same disk cache with a different checkout
        # root, as happens between CI jobs / Electron versions.
        moved = root / "other-checkout"
        moved.mkdir()
        (moved / "fixture.c").write_bytes((source / "fixture.c").read_bytes())
        (moved / "value.h").write_text("#define VALUE 1\n")
        with compiler_cache(root / "cache", moved, output) as wrapper:
            run(wrapper, compiler, *flags, "-O2", "-c", "fixture.c", "-o", "fixture.o", cwd=moved)
        stats = read_json(output / "compiler-cache.json")["stats"]
        if (
            sum(stats["cache_hits"]["counts"].values()) != 1
            or sum(stats["cache_misses"]["counts"].values()) != 0
            or sha256(moved / "fixture.o") != first
        ):
            raise ValueError(
                "Persisted cache did not safely reuse the object in a different checkout"
            )
        write_json(
            output / "verification.json",
            {
                "compiler": str(compiler),
                "cpu_baseline": config["cpu_baseline"],
                "flags": flags,
                "cache_hits": hits,
                "cache_misses": misses,
                "header_invalidation": True,
                "cpu_flag_invalidation": True,
                "new_header_invalidation": True,
                "identical_cached_object": True,
                "cross_checkout_hit_after_server_restart": True,
                "avx512_required": False,
                "full_electron_build_tested": False,
            },
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "out/cache-check")
    args = parser.parse_args()
    check(args.compiler.resolve(), args.output.resolve())
