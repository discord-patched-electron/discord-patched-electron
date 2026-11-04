"""Compare two native modules under bounded load using private synthetic streams.

Plan-only by default. --run permits CPU/GPU contention on the selected hardware.
Use taskset to reserve host CPUs; both the client and CPU workers inherit affinity.
"""

import argparse
import json
import random
import subprocess
from pathlib import Path

from benchmark_screenshare import summarize
from check_screenshare import check
from common import ROOT, hash_files, sha256, write_json


def cases(
    fps,
    memory,
    repetitions,
    seed,
    *,
    pressure_modes=("cpu", "gpu"),
    seconds=30,
    source_fps=60,
    baseline_capture_mode="observe",
    candidate_capture_mode="observe",
):
    if fps not in (30, 60) or memory not in ("memfd", "dmabuf") or not 2 <= repetitions <= 5:
        raise ValueError("Choose 30/60 FPS, memfd/dmabuf and 2–5 repetitions")
    if not pressure_modes or any(mode not in ("cpu", "gpu", "both") for mode in pressure_modes):
        raise ValueError("Choose CPU, GPU or combined pressure")
    if not 12 <= seconds <= 30 or source_fps not in (30, 60, 120, 240):
        raise ValueError("Choose a bounded duration and supported source rate")
    if any(
        mode not in ("observe", "latest", "paced")
        for mode in (baseline_capture_mode, candidate_capture_mode)
    ):
        raise ValueError("Unknown capture mode")
    rng = random.Random(seed)
    result = []
    for repetition in range(repetitions):
        for pressure in rng.sample(list(pressure_modes), len(pressure_modes)):
            for module in (
                ("baseline", "candidate") if repetition % 2 == 0 else ("candidate", "baseline")
            ):
                result.append(
                    dict(
                        id=f"r{repetition + 1}-{pressure}-{module}",
                        pair=f"r{repetition + 1}-{pressure}",
                        variant=f"{pressure}-{module}",
                        module=module,
                        options=dict(
                            source="screen",
                            motion="moving",
                            fps=fps,
                            seconds=seconds,
                            drop_video=False,
                            encoder="h264-vaapi",
                            resolution="1920x1080",
                            bitrate=6000000,
                            scenario="pressure",
                            source_fps=source_fps,
                            backend="pipewire",
                            capture_mode=baseline_capture_mode
                            if module == "baseline"
                            else candidate_capture_mode,
                            memory=memory,
                            pressure=pressure,
                        ),
                    )
                )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime", "baseline", "candidate", "openh264", "render-node", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--fps", type=int, choices=(30, 60), default=60)
    parser.add_argument("--memory", choices=("memfd", "dmabuf"), default="dmabuf")
    parser.add_argument("--repetitions", type=int, choices=range(2, 6), default=2)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--pressure", choices=("cpu", "gpu", "both"), action="append")
    parser.add_argument("--seconds", type=int, choices=range(12, 31), default=30)
    parser.add_argument("--source-fps", type=int, choices=(30, 60, 120, 240), default=60)
    parser.add_argument(
        "--baseline-capture-mode", choices=("observe", "latest", "paced"), default="observe"
    )
    parser.add_argument(
        "--candidate-capture-mode", choices=("observe", "latest", "paced"), default="observe"
    )
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    plan = dict(
        schema=1,
        scope="Synthetic native loopback: baseline/load/recovery. Includes software viewer conversion; excludes compositor and external network.",
        execution_requested=args.run,
        inputs={
            name: dict(path=str(path.resolve()), sha256=sha256(path))
            for name, path in (
                ("runtime", args.runtime),
                ("baseline", args.baseline),
                ("candidate", args.candidate),
                ("openh264", args.openh264),
            )
        },
        source_hashes=hash_files(
            [
                "scripts/check_screenshare.py",
                "scripts/benchmark_screenshare_load.py",
                "integration/video/capture.c",
                "integration/video/capture.cjs",
                "integration/video/capture-rate.h",
                *sorted(
                    str(p.relative_to(ROOT))
                    for p in (ROOT / "tests/screenshare").iterdir()
                    if p.is_file()
                ),
            ]
        ),
        render_node=str(args.render_node.resolve()),
        cases=cases(
            args.fps,
            args.memory,
            args.repetitions,
            args.seed,
            pressure_modes=tuple(args.pressure or ("cpu", "gpu")),
            seconds=args.seconds,
            source_fps=args.source_fps,
            baseline_capture_mode=args.baseline_capture_mode,
            candidate_capture_mode=args.candidate_capture_mode,
        ),
    )
    write_json(args.output / "plan.json", plan)
    if not args.run:
        print(args.output / "plan.json")
        return
    results = []
    for case in plan["cases"]:
        # Refuse a moving comparison when inputs change during a long run.
        if any(sha256(Path(v["path"])) != v["sha256"] for v in plan["inputs"].values()):
            raise RuntimeError("A recorded runtime, codec or native module changed during the run")
        if hash_files(list(plan["source_hashes"])) != plan["source_hashes"]:
            raise RuntimeError("Fixture sources changed during the run; start a fresh plan")
        output = args.output / case["id"]
        error = None
        print("Starting " + case["id"], flush=True)
        try:
            check(
                args.runtime,
                getattr(args, case["module"]),
                output,
                render_node=args.render_node,
                openh264=args.openh264,
                **case["options"],
            )
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as failure:
            error = str(failure)
        path = output / "report.json"
        report = json.loads(path.read_text()) if path.exists() else {"passed": False}
        result = summarize(case, report)
        result.update(
            phases=report.get("measurements", {}).get("phases", {}),
            pressure=report.get("pressure", {}),
        )
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
                performance_improvement_verified=False,
            ),
        )
        if not result["passed"]:
            raise SystemExit("Invalid case " + case["id"] + "; inspect before continuing")
    print(args.output / "results.json")


if __name__ == "__main__":
    main()
