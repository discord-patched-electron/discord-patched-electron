"""Resource samples for comparing compiler runner sizes without guessing."""

from pathlib import Path


def cpu_ticks(stat: Path = Path("/proc/stat")) -> tuple[int, int]:
    fields = next(
        line.split()[1:] for line in stat.read_text().splitlines() if line.startswith("cpu ")
    )
    values = [int(value) for value in fields[:8]]  # guest time is already in user/nice.
    total = sum(values)
    return total, total - values[3] - values[4]


def utilization(before: tuple[int, int], after: tuple[int, int]) -> float | None:
    elapsed = after[0] - before[0]
    if elapsed <= 0 or after[1] < before[1]:
        return None
    return round(100 * (after[1] - before[1]) / elapsed, 2)


def timed_run(output: Path, stage: str, *args, **kwargs):
    import time

    from common import read_json, run, write_json

    report = output / "phases.json"
    started = time.monotonic()
    cpu = cpu_ticks()
    success = False
    try:
        result = run(*args, **kwargs)
        success = True
        return result
    finally:
        phases = read_json(report) if report.exists() else []
        phases.append(
            {
                "stage": stage,
                "seconds": round(time.monotonic() - started, 2),
                "host_cpu_busy_percent": utilization(cpu, cpu_ticks()),
                "success": success,
            }
        )
        write_json(report, phases)
