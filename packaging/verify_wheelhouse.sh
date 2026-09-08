#!/usr/bin/env bash
# Unpack a wheelhouse tarball, install it, and prove it works -- with no network.
#
#   verify_wheelhouse.sh <tarball>
#
# Intended to run inside a throwaway container or network namespace that genuinely has no
# route out; it refuses to run otherwise, because a "passing" offline test on a machine that
# can reach PyPI proves nothing at all.
#
# CI runs this in `docker run --network none`. Locally:
#
#   unshare -rn packaging/verify_wheelhouse.sh dist/pregdos-*-wheelhouse-*.tar.gz

set -euo pipefail

TARBALL=$(readlink -f "${1:?usage: verify_wheelhouse.sh <tarball>}")
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "== confirming this environment really has no network =="
python3 - <<'PY'
import socket
try:
    socket.create_connection(("pypi.org", 443), timeout=5)
except OSError as exc:
    print("   no network, as intended:", type(exc).__name__)
else:
    raise SystemExit("!! network is reachable -- this check would prove nothing")
PY

echo "== unpacking =="
# --no-same-owner: the tarball is owned by root:root, and extraction may run unprivileged.
tar --no-same-owner -xzf "$TARBALL" -C "$WORK"
cd "$WORK"/pregdos-*-wheelhouse-*

echo "== checksums =="
sha256sum -c sha256sums --quiet
echo "   all files match sha256sums"

echo "== installing offline =="
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --no-index --find-links=wheelhouse pregdos
"$WORK/venv/bin/pip" check

echo "== verifying the install serves pages =="
"$WORK/venv/bin/python" verify_offline_install.py
