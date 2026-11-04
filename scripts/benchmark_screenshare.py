"""Prepare reproducible native capture comparisons; execute only with --run.

The synthetic source isolates handoff/encoding from real compositor capture.
No pressure generators run here. Hardware/source-rate comparisons can still
affect desktop responsiveness: keep --run deferred during an active session.
"""

import argparse
import itertools
import json
import platform
import random
import statistics
import subprocess
from pathlib import Path

from check_screenshare import check
from common import ROOT, hash_files, sha256, write_json


def cases(profile, repetitions, seed):
    if profile not in ("software", "hardware") or not 2 <= repetitions <= 10:
        raise ValueError("Choose software/hardware and 2–10 repetitions")
    variants = []
    hardware = profile == "hardware"
    for memory, fps, source_fps in itertools.product(
        ("memfd", "dmabuf") if hardware else ("memfd",),
        (30, 60),
        (60, 240) if hardware else (60,),
    ):
        variants.append(
            dict(
                memory=memory,
                fps=fps,
                source_fps=source_fps,
                encoder="h264-vaapi" if hardware else "vp8",
                resolution="1920x1080" if hardware else "640x360",
                bitrate=6000000 if hardware else 1000000,
            )
        )
    rng = random.Random(seed)
    planned = []
    for repetition in range(repetitions):
        order = rng.sample(variants, len(variants))
        for variant in order:
            # Keep matched modes adjacent but alternate their order between
            # repetitions. Report each repetition; never pool unrelated phases.
            for mode in ("observe", "latest") if repetition % 2 == 0 else ("latest", "observe"):
                key = f"{variant['memory']}-{variant['fps']}fps-{variant['source_fps']}hz-{mode}"
                planned.append(
                    dict(
                        id=f"r{repetition + 1}-{key}",
                        pair=f"r{repetition + 1}-{key.rsplit('-', 1)[0]}",
                        variant=key,
                        repetition=repetition + 1,
                        options=dict(
                            variant,
                            capture_mode=mode,
                            source="screen",
                            backend="pipewire",
                            motion="moving",
                            seconds=12,
                            scenario="steady",
                            drop_video=False,
                        ),
                    )
                )
    return planned


def summarize(case, report):
    measurements = report.get("measurements", {})
    frames = measurements.get("frames", [])
    phases = measurements.get("phases", {})
    initial = phases.get("initial", {})
    unique_fps = None
    if len(frames) > 1:
        interval = frames[-1]["arrivedMs"] - frames[0]["arrivedMs"]
        if interval > 0:
            unique_fps = (len(frames) - 1) * 1000 / interval
    return dict(
        id=case["id"],
        variant=case["variant"],
        pair=case["pair"],
        passed=report.get("passed") is True,
        validation=report.get("validation", {}),
        unique_fps=unique_fps,
        latency_ms=initial.get("sourceToReceiverMs", {}),
        cadence_ms=initial.get("uniqueFrameIntervalMs", {}),
        dimensions=initial.get("dimensions", []),
        marker_coverage=report.get("marker_coverage"),
        capture=report.get("captureAdapter"),
        pipeline=report.get("pipeline"),
        # Keep actual encoder/bitrate data, including adaptation, for review.
        samples=report.get("samples", []),
        encoder_observations=report.get("encoder_observations", []),
    )


def aggregate(results):
    groups = {}
    for result in results:
        groups.setdefault(result["variant"], []).append(result)
    output = {}
    for variant, runs in groups.items():
        valid = [r for r in runs if r["passed"]]
        output[variant] = {"runs": len(runs), "valid_runs": len(valid)}
        # Failed validation must not disappear into a flattering aggregate.
        if len(valid) != len(runs):
            continue
        for key, values in (
            ("median_unique_fps", [r["unique_fps"] for r in valid]),
            ("median_run_p95_ms", [r["latency_ms"].get("p95") for r in valid]),
        ):
            if values and all(isinstance(v, (float, int)) for v in values):
                output[variant][key] = statistics.median(values)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("software", "hardware"), default="software")
    parser.add_argument("--repetitions", type=int, choices=range(2, 11), default=3)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--render-node", type=Path)
    parser.add_argument("--openh264", type=Path, help="Existing trusted H.264 decoder library")
    parser.add_argument(
        "--run", action="store_true", help="Execute the plan; otherwise write it only"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a fresh output directory")
    if args.run and args.profile == "hardware" and not args.render_node:
        parser.error("Executing hardware cases requires an explicit --render-node")
    if args.profile == "software" and args.render_node:
        parser.error("The software profile does not expose a GPU")
    plan = dict(
        schema=1,
        profile=args.profile,
        seed=args.seed,
        repetitions=args.repetitions,
        scope="synthetic PipeWire handoff and native local-loopback delivery; excludes real compositor, display presentation and network congestion",
        pressure_generators=False,
        execution_requested=args.run,
        runtime=str(args.runtime.resolve()),
        runtime_sha256=sha256(args.runtime),
        voice=str(args.voice.resolve()),
        voice_sha256=sha256(args.voice),
        openh264_sha256=sha256(args.openh264) if args.openh264 else None,
        render_node=str(args.render_node.resolve()) if args.render_node else None,
        kernel=platform.release(),
        machine=platform.machine(),
        source_hashes=hash_files(
            [
                "scripts/check_screenshare.py",
                "scripts/benchmark_screenshare.py",
                "scripts/capture_scope.py",
                "integration/video/capture.c",
                "integration/video/capture.cjs",
                *sorted(
                    str(path.relative_to(ROOT))
                    for path in (ROOT / "tests/screenshare").iterdir()
                    if path.is_file()
                ),
            ]
        ),
        cases=cases(args.profile, args.repetitions, args.seed),
    )
    args.output.mkdir(parents=True)
    write_json(args.output / "plan.json", plan)
    if not args.run:
        print(args.output / "plan.json")
        return
    results = []
    for case in plan["cases"]:
        output = args.output / case["id"]
        error = None
        try:
            check(
                args.runtime,
                args.voice,
                output,
                render_node=args.render_node,
                openh264=args.openh264,
                **case["options"],
            )
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as failure:
            error = str(failure)
        report_path = output / "report.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {"passed": False}
        result = summarize(case, report)
        if error:
            result.update(passed=False, error=error)
        results.append(result)
        write_json(
            args.output / "results.json",
            dict(
                scope=plan["scope"],
                planned_runs=len(plan["cases"]),
                completed_runs=len(results),
                runs=results,
                aggregates=aggregate(results),
                performance_improvement_verified=False,
            ),
        )
        if not result["passed"]:
            raise SystemExit(
                f"Stopped at invalid case {case['id']}; inspect its report before continuing"
            )
    print(args.output / "results.json")


if __name__ == "__main__":
    main()
