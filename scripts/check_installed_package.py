"""Check a system package as an ordinary user in a disposable distro container."""

import argparse
import json
import os
import pwd
import subprocess
from pathlib import Path


def check(app: Path):
    # The container installs packages as root. Drop privileges before opening
    # app files, so root-only modes cannot make a broken package appear to pass.
    if os.geteuid() == 0:
        user = pwd.getpwnam("nobody")
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    os.chdir("/tmp")
    for path in app.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                stream.read(1)
    runtime = app / "runtime/electron"
    libraries = subprocess.check_output(["ldd", str(runtime)], text=True)
    if "not found" in libraries:
        raise ValueError(libraries)
    versions = json.loads(
        subprocess.check_output(
            [str(runtime), "-p", "JSON.stringify(process.versions)"],
            env={**os.environ, "ELECTRON_RUN_AS_NODE": "1"},
            text=True,
        )
    )
    print(
        json.dumps(
            {
                "passed": True,
                "uid": os.getuid(),
                "versions": versions,
                "files_readable": True,
                "runtime_libraries_found": True,
                "gui_verified": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    check(parser.parse_args().app)
