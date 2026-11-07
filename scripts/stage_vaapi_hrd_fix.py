"""Stage reviewed HRD and GPU capture corrections in a new module directory.

The current native H.264 callers reverse EncMiscParameterHRD::new(fullness, size).
Correct HRD first, then preserve visible GPU capture dimensions. Validate both
complete outputs before creating any candidate directory. Packaging uses these
same corrections through patch_app_audio; this helper stages only a private copy.
See docs/agent/screenshare-native-gpu-20261102.md for binary/source evidence.
"""

import argparse
import shutil
from pathlib import Path

import patch_capture_dimensions as dimensions
import patch_vaapi_hrd as hrd
from capture_picker_scope import write_scope as write_capture_picker_scope
from capture_scale_scope import write_scope as write_capture_scale_scope
from capture_sync_scope import write_scope as write_capture_sync_scope
from capture_upstream_scope import write_scope as write_capture_upstream_scope
from common import sha256, write_json
from encoder_scope import write_scope as write_encoder_scope


def stage(module, output):
    module, output = module.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Choose a new candidate directory; existing files are never overwritten")
    input_digest = sha256(module)
    _, expected, sites = hrd.reviewed(input_digest)
    hrd_candidate = hrd.corrected(module.read_bytes())
    _, final_expected, dimension_offset = dimensions.reviewed(expected)
    candidate = dimensions.corrected(hrd_candidate)
    output.mkdir(parents=True)
    destination = output / "discord_voice.node"
    with destination.open("xb") as handle:
        handle.write(candidate)
    shutil.copymode(module, destination)
    for name in ("libmediapipe.so", "gpu_encoder_helper"):
        dependency = module.with_name(name)
        if dependency.exists():
            shutil.copy2(dependency, output / name)
    report = {
        "patch": hrd.PATCH,
        "input_sha256": input_digest,
        "output_sha256": expected,
        "sites": [offset for offset, _, _ in sites],
        "changed_bytes": 0
        if input_digest == expected
        else sum(sum(a != b for a, b in zip(old, new, strict=True)) for _, old, new in sites),
        "hardware_performance_validated": False,
        "installed": False,
    }
    write_json(output / "hrd-correction.json", report)
    write_json(
        output / "capture-dimensions-correction.json",
        {
            "patch": dimensions.PATCH,
            "input_sha256": expected,
            "output_sha256": final_expected,
            "sites": [dimension_offset],
            "changed_bytes": 0
            if expected == final_expected
            else sum(a != b for a, b in zip(dimensions.BEFORE, dimensions.AFTER, strict=True)),
            "installed": False,
        },
    )
    write_encoder_scope(destination, output)
    write_capture_scale_scope(destination, output)
    write_capture_upstream_scope(destination, output)
    write_capture_sync_scope(destination, output)
    write_capture_picker_scope(destination, output)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(stage(args.module, args.output))


if __name__ == "__main__":
    main()
