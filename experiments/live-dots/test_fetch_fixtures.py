import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "fetch-fixtures.sh"


@pytest.mark.parametrize("failure", ["archive", "extract", "empty", "move", "success"])
def test_failed_refresh_preserves_existing_fixtures(tmp_path, failure):
    script = tmp_path / "fetch-fixtures.sh"
    script.write_text(SCRIPT.read_text())
    fixtures = tmp_path / "Fixtures"
    fixtures.mkdir()
    (fixtures / "old.json").write_text("working replay")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text("""#!/bin/sh
case "$3" in
rev-parse) echo "$TEST_ROOT" ;;
fetch) exit 0 ;;
archive)
    [ "$FAILURE" != archive ] || exit 1
    : > "${4#--output=}"
    ;;
esac
""")
    tar = bin_dir / "tar"
    tar.write_text("""#!/bin/sh
[ "$FAILURE" != extract ] || exit 1
mkdir -p "$4/Fixtures"
case "$FAILURE" in
success|move) echo replacement > "$4/Fixtures/new.json" ;;
esac
""")
    mv = bin_dir / "mv"
    mv.write_text("""#!/bin/sh
if [ "$FAILURE" = move ] && [ "${1##*/}" = Fixtures ] && [ "$1" != "$TEST_ROOT/Fixtures" ]; then
    test -d "${1%/*}/previous" || exit 2
    echo failed > "$TEST_ROOT/final-move-failed"
    exit 1
fi
exec /bin/mv "$@"
""")
    git.chmod(0o755)
    tar.chmod(0o755)
    mv.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:/usr/bin:/bin",
        TEST_ROOT=str(tmp_path),
        FAILURE=failure,
    )
    result = subprocess.run(
        ["/bin/sh", str(script)], env=env, capture_output=True, text=True, check=False
    )
    if failure == "success":
        assert result.returncode == 0, result.stderr
        assert (fixtures / "new.json").read_text() == "replacement\n"
        assert not (fixtures / "old.json").exists()
    else:
        assert result.returncode != 0
        assert (fixtures / "old.json").read_text() == "working replay"
        assert list(fixtures.iterdir()) == [fixtures / "old.json"]
        if failure == "move":
            assert (tmp_path / "final-move-failed").read_text() == "failed\n"
    assert not list(tmp_path.glob(".fixtures.*"))
