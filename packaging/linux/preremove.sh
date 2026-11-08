#!/bin/sh
set -eu
# @FORMAT@: only a real uninstall, never an upgrade/rollback scriptlet.
@REMOVE_GUARD@
exec python3 -I /opt/@PKGNAME@/uninstall.py --package @PKGNAME@
