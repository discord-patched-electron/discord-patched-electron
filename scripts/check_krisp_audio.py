"""Generate and evaluate a synthetic offline Krisp regression fixture.

This verifies file-based processing, not a live call, microphone device or human
intelligibility judgment. The speech reference was generated with eSpeak NG.
"""

from __future__ import annotations

import argparse
import array
import math
import random
import wave
from pathlib import Path

from common import ROOT, read_json, sha256, write_json


def read_wav(path: Path):
    with wave.open(str(path), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (48000, 1, 2):
            raise ValueError(f"Expected 48 kHz mono PCM16 WAV: {path}")
        samples = array.array("h", source.readframes(source.getnframes()))
    return samples


def generate(directory: Path):
    source = ROOT / "tests/audio/speech.wav"
    metadata = read_json(ROOT / "tests/audio/speech.json")
    if sha256(source) != metadata["sha256"]:
        raise ValueError("Speech fixture checksum differs from its provenance")
    speech = read_wav(source)
    clean = [0] * 96000 + [int(sample * 0.7) for sample in speech] + [0] * 96000
    clean += [0] * (-len(clean) % 480)  # Native processing uses 10 ms frames.
    rng = random.Random(42)
    noisy = [max(-32768, min(32767, int(sample + rng.gauss(0, 1800)))) for sample in clean]
    (directory / "input").mkdir(parents=True, exist_ok=True)
    for path, samples in (
        (directory / "reference.wav", clean),
        (directory / "input/noisy.wav", noisy),
    ):
        with wave.open(str(path), "wb") as target:
            target.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            target.writeframes(array.array("h", samples).tobytes())
    write_json(
        directory / "fixture.json",
        {
            "speech": metadata,
            "noise_seed": 42,
            "noise_standard_deviation_pcm16": 1800,
            "speech_gain": 0.7,
            "padding_seconds": 2,
            "reference_sha256": sha256(directory / "reference.wav"),
            "input_sha256": sha256(directory / "input/noisy.wav"),
        },
    )


def analyze(fixture: Path, probe: Path) -> dict:
    import numpy as np

    metadata = read_json(fixture / "fixture.json")
    if sha256(fixture / "reference.wav") != metadata["reference_sha256"]:
        raise ValueError("Reference audio changed")
    runtime = read_json(probe / "runtime-report.json")
    if not runtime.get("passed") or runtime.get("audio_batches", {}).get("errors"):
        raise ValueError("Native runtime/batch processing failed")
    batches = runtime["audio_batches"]["batches"]
    if len(batches) != 3 or [b["enabled"] for b in batches] != [False, True, False]:
        raise ValueError("Expected off/on/off batch processing")
    if sha256(probe / "audio-input/noisy.wav") != metadata["input_sha256"]:
        raise ValueError("Processed input differs from the synthetic fixture")
    reference = np.asarray(read_wav(fixture / "reference.wav"), dtype=np.float64)
    results = []
    for index, batch in enumerate(batches):
        if batch["processed"] != 1 or batch["failed"] != 0:
            raise ValueError("Batch must process exactly one fixture successfully")
        path = probe / "profile" / f"audio-{index}" / "noisy_processed.wav"
        processed = np.asarray(read_wav(path), dtype=np.float64)
        if abs(len(processed) - len(reference)) >= 480:
            raise ValueError("Unexpected processed audio length")
        size = 1 << (len(reference) + len(processed) - 1).bit_length()
        correlation = np.fft.irfft(
            np.fft.rfft(processed, size) * np.conj(np.fft.rfft(reference, size)), size
        )
        lags = np.arange(-9600, 9601)  # Permit up to 200 ms of processing delay.
        lag = int(lags[np.argmax(correlation[lags % size])])
        x, y = reference[max(0, -lag) :], processed[max(0, lag) :]
        length = min(len(x), len(y))
        x, y = x[96000 : length - 96000], y[96000 : length - 96000]
        gain = float(np.dot(x, y) / np.dot(x, x))
        centered_x, centered_y = x - x.mean(), y - y.mean()
        norm = float(np.linalg.norm(centered_x) * np.linalg.norm(centered_y))
        speech_correlation = float(np.dot(centered_x, centered_y) / norm) if norm else 0.0
        noise_rms = float(np.sqrt(np.mean(processed[48000:86400] ** 2)))
        results.append(
            {
                "enabled": batch["enabled"],
                "sha256": sha256(path),
                "samples": len(processed),
                "lag_samples": lag,
                "speech_correlation": speech_correlation,
                "speech_gain": gain,
                "noise_rms_pcm16": noise_rms,
            }
        )
    off, on, restored = results
    reduction_db = 20 * math.log10(
        max(off["noise_rms_pcm16"], 1e-12) / max(on["noise_rms_pcm16"], 1e-12)
    )
    restored_db = 20 * math.log10(
        max(restored["noise_rms_pcm16"], 1e-12) / max(off["noise_rms_pcm16"], 1e-12)
    )
    logs = "\n".join(
        path.read_text(errors="replace")
        for path in (probe / "profile/voice").glob("discord-webrtc_*")
    )
    checks = {
        "krisp_initialization_logged": "Krisp initialized successfully" in logs,
        "noise_reduction_at_least_15db": reduction_db >= 15,
        "speech_correlation_at_least_0_6": on["speech_correlation"] >= 0.6,
        "speech_correlation_improved": on["speech_correlation"] >= off["speech_correlation"] + 0.03,
        "speech_gain_retained": on["speech_gain"] >= 0.5 * off["speech_gain"] > 0,
        "disabled_noise_restored": abs(restored_db) < 0.1,
        "disabled_speech_restored": abs(restored["speech_correlation"] - off["speech_correlation"])
        < 0.01,
    }
    return {
        "schema": 1,
        "offline_krisp_verified": all(checks.values()),
        "checks": checks,
        "noise_reduction_db": reduction_db,
        "batches": results,
        "numpy_version": np.__version__,
        "fixture": metadata,
        "runtime_versions": runtime["versions"],
        "original_inputs": runtime["original_inputs"],
        "runtime_fixture_inputs": runtime["fixture_inputs"],
        "analyzer_sha256": sha256(Path(__file__)),
        "real_microphone_verified": False,
        "real_discord_call_verified": False,
        "human_intelligibility_verified": False,
        "publishable": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("generate")
    create.add_argument("directory", type=Path)
    check = subparsers.add_parser("analyze")
    check.add_argument("fixture", type=Path)
    check.add_argument("probe", type=Path)
    check.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "generate":
        generate(args.directory)
    else:
        result = analyze(args.fixture, args.probe)
        write_json(args.report, result)
        print(
            f"Offline Krisp verified: {result['offline_krisp_verified']}; noise reduction {result['noise_reduction_db']:.1f} dB"
        )
        if not result["offline_krisp_verified"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
