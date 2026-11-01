"""Verify the reserved source identity before doing compiler work."""

import os

from build_reference import build_config
from common import ROOT, read_json, require_enabled_electron
from runtime_fingerprints import runtime_key


def main():
    target = os.environ["ELECTRON_BUILD_TARGET"]
    require_enabled_electron(target)
    sources = read_json(ROOT / "config/sources.lock.json")
    actual = runtime_key(sources["targets"][target], sources, build_config(target))
    if actual != os.environ["EXPECTED_RUNTIME_KEY"]:
        raise ValueError("Source changed since scheduling")


if __name__ == "__main__":
    main()
