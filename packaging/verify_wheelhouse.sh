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

TARGET="${1:?usage: verify_wheelhouse.sh <tarball|directory>}"

# A directory is accepted because the caller often has one tarball in a known place but not
# its exact name (the version is derived by setuptools-scm).  Globbing at the call site does
# not work when the path only exists inside the container: the host shell expands it against
# its own filesystem, matches nothing, and passes the pattern through literally.
if [ -d "$TARGET" ]; then
    # Shell globbing rather than find(1): this runs in a slim container, and the fewer
    # utilities it assumes, the fewer ways it has to fail for reasons unrelated to the test.
    shopt -s nullglob
    candidates=("$TARGET"/*.tar.gz)
    shopt -u nullglob
    if [ ${#candidates[@]} -ne 1 ]; then
        echo "!! expected exactly one *.tar.gz in $TARGET, found ${#candidates[@]}" >&2
        exit 2
    fi
    TARGET="${candidates[0]}"
fi

if [ ! -f "$TARGET" ]; then
    echo "!! not a readable file: $TARGET" >&2
    case "$TARGET" in
        *[*?]*) echo "   (it contains a glob character -- the pattern was never expanded;" >&2
                echo "    pass a directory instead, or expand it where the path exists)" >&2 ;;
    esac
    exit 2
fi

TARBALL=$(readlink -f "$TARGET")
echo "Verifying $(basename "$TARBALL")"
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

# The tarball name records the interpreter it was built for (cp311 -> 3.11).  Check it
# before installing: on RHEL 9 `python3` is 3.9, and pip's failure for a cp311-only wheel is
# "No matching distribution found for numpy", which reads like a missing wheel rather than
# the wrong interpreter.  $PYTHON overrides the interpreter used.
PYTHON="${PYTHON:-python3}"
case "$(basename "$TARBALL")" in
    *-cp3*) tag=$(basename "$TARBALL" | sed -n 's/.*-cp3\([0-9]*\)-.*/3.\1/p') ;;
    *)      tag="" ;;
esac
if [ -n "$tag" ]; then
    have=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    if [ "$have" != "$tag" ]; then
        echo "!! this wheelhouse is for Python $tag, but $PYTHON is $have" >&2
        echo "   install python$tag and re-run as:  PYTHON=python$tag $0 $TARGET" >&2
        exit 2
    fi
fi

echo "== installing offline =="
"$PYTHON" -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install --no-index --find-links=wheelhouse pregdos
"$WORK/venv/bin/pip" check

echo "== verifying the install serves pages =="
"$WORK/venv/bin/python" verify_offline_install.py
