"""Native build identity; package/module adapters cannot invalidate C++ objects."""
from common import digest, hash_files


def runtime_key(target: dict, sources: dict, toolchain: dict) -> str:
    port = sources.get("ports", {}).get(target["version"].split(".")[0])
    if port:
        # Validation status is evidence, not a native compilation input.
        port = {key: value for key, value in port.items() if key != "status"}
    port_files = [port["patch"]] if port else []
    if port:
        port_files += [entry["patch"] for entry in port.get("followup_patches", [])]
    return digest({"target": {key: target[key] for key in ("version", "commit")},
                   "fork": sources["fork"], "port": port, "toolchain": toolchain,
                   "inputs": hash_files([sources["fork"]["patch"], "scripts/assemble.py", "scripts/common.py",
                                         "scripts/build_reference.py", "scripts/runtime_fingerprints.py", "scripts/compiler_cache.py",
                                         "config/build.json", "config/candidate-build.json", "config/compiler-cache.json",
                                         "ci/gn/BUILD.gn.in", "ci/runner/Dockerfile", *port_files])})

