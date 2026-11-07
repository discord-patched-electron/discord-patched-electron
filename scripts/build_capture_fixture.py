"""Build the disposable capture-stall addon; never stage it in a product."""

import subprocess

from build_rust_support import build as build_rust


def build(target):
    archive, identity = build_rust(capture_gpu=False, features=("capture-delay-fixture",))
    target.parent.mkdir(parents=True, exist_ok=True)
    exports = target.with_suffix(".exports")
    exports.write_text("{ global: napi_register_module_v1; local: *; };\n")
    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-Wl,-z,relro,-z,now",
            "-Wl,--undefined=napi_register_module_v1",
            "-Wl,--gc-sections",
            "-Wl,--no-undefined",
            "-Wl,--version-script=" + str(exports),
            str(archive),
            "-ldl",
            "-lpthread",
            "-lm",
            "-o",
            str(target),
        ],
        check=True,
    )
    return identity
