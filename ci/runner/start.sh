#!/bin/bash
set -euo pipefail
cd /opt/runner
until [[ -f .configured ]]; do sleep 1; done
exec ./run.sh
