#!/bin/sh
# Copies the synthetic replay fixtures from the guided-capture branch (PR #10) into Fixtures/,
# without adding ios/ to a sparse checkout. Fixtures/ is git-ignored.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
if [ "${1-}" != --lock-held ]; then
    exec python3 "$here/rename-fixtures.py" --refresh "$here/fetch-fixtures.sh"
fi
# A hard kill skips the trap; recover our recorded backup before any git/network work.
python3 "$here/rename-fixtures.py" --recover "$here"
root="$(git -C "$here" rev-parse --show-toplevel)"
git -C "$root" fetch --quiet origin t3/ios-mvf
stage="$(mktemp -d "$here/.fixtures.XXXXXX")"
published=0
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$published" -eq 0 ] && [ -d "$stage/previous" ]; then
        if [ ! -e "$here/Fixtures" ] && [ ! -L "$here/Fixtures" ] && \
            python3 "$here/rename-fixtures.py" "$stage/previous" "$here/Fixtures"; then
            :
        else
            echo "Could not restore fixtures; backup preserved at $stage/previous" >&2
            exit 1
        fi
    fi
    rm -rf "$stage"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
python3 "$here/rename-fixtures.py" --mark-stage "$stage"
# Keep archive creation separate from extraction so either failure preserves the old fixtures.
git -C "$root" archive --output="$stage/fixtures.tar" origin/t3/ios-mvf ios/HouseScanUITests/Fixtures
tar -xf "$stage/fixtures.tar" -C "$stage" --strip-components=2
if [ ! -f "$stage/Fixtures/synthetic-wall-lidar/session.json" ]; then
    echo "Fetched fixtures lack synthetic-wall-lidar/session.json; keeping existing fixtures" >&2
    exit 1
fi
if [ -e "$here/Fixtures" ]; then
    python3 "$here/rename-fixtures.py" --mark-backup "$stage" "$here/Fixtures"
    python3 "$here/rename-fixtures.py" "$here/Fixtures" "$stage/previous"
fi
python3 "$here/rename-fixtures.py" "$stage/Fixtures" "$here/Fixtures"
published=1
ls "$here/Fixtures"
