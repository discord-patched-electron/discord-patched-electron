"""Resolve independent include insertions in a Git three-way source patch."""

import os
import re
import subprocess
import tempfile
from pathlib import Path

INCLUDE = re.compile(rb'[ \t]*#include[ \t]+(?:"[^"\r\n]+"|<[^>\r\n]+>)[ \t]*\n')
MARKERS = re.compile(
    rb"^<<<<<<< upstream\n(.*?)^\|\|\|\|\|\|\| base\n(.*?)^=======\n(.*?)^>>>>>>> patch\n",
    re.MULTILINE | re.DOTALL,
)


def include_insertions(base, side):
    """Group inserted directives by their position among the original includes."""
    if len(set(side)) != len(side) or any(not INCLUDE.fullmatch(line) for line in side):
        raise ValueError("Conflict is not a list of distinct literal includes")
    added = [[] for _ in range(len(base) + 1)]
    position = 0
    for line in side:
        if position < len(base) and line == base[position]:
            position += 1
        elif line in base:
            raise ValueError("Conflict reorders an existing include")
        else:
            added[position].append(line)
    if position != len(base):
        raise ValueError("Conflict removes or changes an existing include")
    return added


def merge_includes(conflicted):
    added = set()

    def resolve(match):
        upstream, base, incoming = (group.splitlines(keepends=True) for group in match.groups())
        ours, theirs = (include_insertions(base, side) for side in (upstream, incoming))
        combined = []
        for index, (left, right) in enumerate(zip(ours, theirs, strict=True)):
            insertions = list(dict.fromkeys(left + right))
            if [line for line in insertions if line in right] != right:
                raise ValueError("Include insertions disagree on order")
            combined.extend(insertions)
            added.update(insertions)
            if index < len(base):
                combined.append(base[index])
        return b"".join(combined)

    merged, count = MARKERS.subn(resolve, conflicted)
    if not count or re.search(rb"^(?:<<<<<<< |\|{7} |>>>>>>> )", merged, re.MULTILINE):
        raise ValueError("Unresolved three-way conflict")
    uncommented = re.sub(rb"/\*.*?\*/|//[^\n]*", b"", merged, flags=re.DOTALL)
    includes = [
        line.strip() for line in uncommented.splitlines(keepends=True) if INCLUDE.fullmatch(line)
    ]
    if any(includes.count(line.strip()) != 1 for line in added):
        raise ValueError("Include insertion duplicates a directive elsewhere in the file")
    remaining = {line.strip() for line in added}
    conditional_depth = 0
    for line in uncommented.splitlines(keepends=True):
        directive = line.strip()
        if directive in remaining:
            if conditional_depth:
                raise ValueError("Include conflict is inside a conditional directive")
            remaining.remove(directive)
        if not remaining:
            break
        if re.match(rb"#(?:if|ifdef|ifndef)\s", directive):
            conditional_depth += 1
        elif directive == b"#endif":
            conditional_depth -= 1
        if line.strip() and not (
            INCLUDE.fullmatch(line)
            or re.fullmatch(rb"[ \t]*#(?:if|ifdef|ifndef|elif|define) [^\\\n]+\n", line)
            or line.strip() in (b"#pragma once", b"#else", b"#endif")
        ):
            raise ValueError("Conflict is outside the file's leading include directives")
    return merged


def apply_source_patch(checkout, patch, options, *, allow_include_merge):
    git = ["git", "-C", str(checkout)]
    process = subprocess.run(
        [*git, "apply", "--3way", "--index", "--whitespace=nowarn", *options, str(patch)],
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    log = process.stdout + process.stderr
    if not process.returncode:
        return True, log, []
    # Git can report a missing preimage as well as index conflicts. Leave that
    # case to the port author rather than accepting a partially applied patch.
    if not allow_include_merge or re.search(r"^(?:fatal|error):", log, re.MULTILINE):
        return False, log, []

    entries = subprocess.check_output([*git, "ls-files", "--unmerged", "-z"])
    conflicts = {}
    for entry in entries.split(b"\0")[:-1]:
        metadata, name = entry.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        conflicts.setdefault(os.fsdecode(name), {})[stage] = mode, oid
    if not conflicts:
        return False, log, []

    resolutions = {}
    for name, stages in conflicts.items():
        if (
            Path(name).suffix not in (".cc", ".cpp", ".c", ".h", ".hpp", ".mm")
            or set(stages) != {b"1", b"2", b"3"}
            or {mode for mode, _ in stages.values()} != {b"100644"}
        ):
            return False, log + f"Manual port required for {name}\n", []
        with tempfile.TemporaryDirectory(prefix="dpe-patch-merge-") as temporary:
            files = []
            for stage in (b"2", b"1", b"3"):
                path = Path(temporary) / stage.decode()
                path.write_bytes(
                    subprocess.check_output([*git, "cat-file", "blob", stages[stage][1]])
                )
                files.append(path)
            merge = subprocess.run(
                [
                    "git",
                    "merge-file",
                    "-p",
                    "--diff3",
                    "-L",
                    "upstream",
                    "-L",
                    "base",
                    "-L",
                    "patch",
                    *files,
                ],
                capture_output=True,
            )
        if not 0 < merge.returncode < 128 or merge.stderr:
            return False, log + f"Cannot reconstruct index conflict for {name}\n", []
        try:
            resolutions[name] = merge_includes(merge.stdout)
        except ValueError as error:
            return False, log + f"{name}: {error}\n", []

    # Stage nothing until every conflict in this patch has been classified.
    for name, contents in resolutions.items():
        (checkout / name).write_bytes(contents)
    subprocess.run([*git, "--literal-pathspecs", "add", "--", *resolutions], check=True)
    return True, log, sorted(resolutions)
