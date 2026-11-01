"""Stable candidate paths with recoverable transitions between native inputs."""

import os
import tempfile
from pathlib import Path

from common import digest, read_json, run, write_json


def workspace_id(target: str, inputs: dict) -> str:
    if os.environ.get("ELECTRON_INCREMENTAL_WORKSPACE") != "1" or target == "reference":
        return digest(inputs)
    # Keep compiler ABI, major release and toolchain boundaries separate. Within
    # a slot, Git/gclient and Ninja determine which changed inputs need rebuilding.
    compatibility = {
        name: inputs[name] for name in ("fork", "config", "container", "candidate_toolchain")
    }
    compatibility["major"] = inputs["target"]["version"].split(".")[0]
    return "incremental-" + digest(compatibility)


def transition(work: Path, target: str, inputs: dict, assemble, verify_source, reset_repositories):
    previous = work / "native-inputs.json"
    pending = work / "transition.json"
    if not pending.exists() and (not previous.exists() or read_json(previous) == inputs):
        return
    if not work.name.startswith("incremental-"):
        raise ValueError("Native input change in a fixed-identity workspace")
    source = work / "checkout"
    electron = source / "src/electron"
    report_path = work / "source.json"
    cache = work.parent.parent
    if pending.exists():
        transaction = read_json(pending)
        if transaction["inputs"] != inputs:
            raise ValueError("Finish the interrupted workspace transition before changing targets")
    else:
        verify_source(electron, read_json(report_path))
        with tempfile.TemporaryDirectory(prefix="source-transition-", dir=cache) as temporary:
            staging = Path(temporary)
            checkout = staging / "electron"
            try:
                if not assemble(target, cache / "source.git", checkout, staging / "source.json"):
                    raise ValueError("New source port failed; retained objects were not changed")
                report = read_json(staging / "source.json")
                verify_source(checkout, report)
                transaction = {"inputs": inputs, "source": report}
                write_json(pending, transaction)
            finally:
                if checkout.exists():
                    run(
                        "git", "-C", cache / "source.git", "worktree", "remove", "--force", checkout
                    )
    # Write-ahead marker makes an interruption repeatable. These are disposable,
    # repository-owned checkouts; never apply this operation to the user's tree.
    for name in ("prepared.json", "synchronized.json", "completed.json"):
        (work / name).unlink(missing_ok=True)
    bases = work / "patch-bases.json"
    if bases.exists():
        reset_repositories(source, read_json(bases))
    report = transaction["source"]
    run("git", "-C", electron, "reset", "--hard", report["upstream_commit"])
    run("git", "-C", electron, "read-tree", "--reset", "-u", report["tree"])
    verify_source(electron, report)
    write_json(report_path, report)
    write_json(previous, inputs)
    pending.unlink()
