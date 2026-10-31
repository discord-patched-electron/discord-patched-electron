"""Exec busctl with parent-death cleanup, including renderer crashes."""

import ctypes
import os
import signal
import sys

if __name__ == "__main__":
    parent = int(sys.argv[1])
    if ctypes.CDLL(None).prctl(1, signal.SIGTERM) != 0 or os.getppid() != parent:
        sys.exit(1)
    os.execv("/usr/bin/busctl", ["busctl", *sys.argv[2:]])
