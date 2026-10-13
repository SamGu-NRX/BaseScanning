#!/bin/sh
# Copies the synthetic replay fixtures from the guided-capture branch (PR #10) into Fixtures/,
# without adding ios/ to a sparse checkout. Fixtures/ is git-ignored.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
root="$(git -C "$here" rev-parse --show-toplevel)"
git -C "$root" fetch --quiet origin t3/ios-mvf
stage="$(mktemp -d "$here/.fixtures.XXXXXX")"
cleanup() {
    if [ -d "$stage/previous" ] && [ ! -e "$here/Fixtures" ]; then
        mv "$stage/previous" "$here/Fixtures"
    fi
    rm -rf "$stage"
}
trap cleanup EXIT HUP INT TERM
# Keep archive creation separate from extraction so either failure preserves the old fixtures.
git -C "$root" archive --output="$stage/fixtures.tar" origin/t3/ios-mvf ios/HouseScanUITests/Fixtures
tar -xf "$stage/fixtures.tar" -C "$stage" --strip-components=2
test -d "$stage/Fixtures"
test -n "$(ls -A "$stage/Fixtures")"
if [ -e "$here/Fixtures" ]; then
    mv "$here/Fixtures" "$stage/previous"
fi
mv "$stage/Fixtures" "$here/Fixtures"
ls "$here/Fixtures"
