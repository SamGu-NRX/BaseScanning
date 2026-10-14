import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "fetch-fixtures.sh"


def contents(folder):
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "failure",
    [
        "archive",
        "extract",
        "empty",
        "missing-session",
        "move",
        "occupied",
        "reappeared",
        "reappeared-empty",
        "restore",
        "success",
    ],
)
def test_failed_refresh_preserves_existing_fixtures(tmp_path, failure):
    script = tmp_path / "fetch-fixtures.sh"
    script.write_text(SCRIPT.read_text())
    (tmp_path / "rename-fixtures.py").write_text(
        SCRIPT.with_name("rename-fixtures.py").read_text()
    )
    fixtures = tmp_path / "Fixtures"
    fixtures.mkdir()
    (fixtures / "old.json").write_text("working replay")
    (fixtures / "synthetic-wall-lidar").mkdir()
    (fixtures / "synthetic-wall-lidar/session.json").write_text('{"working": true}')
    original = contents(fixtures)
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
missing-session) echo unrelated > "$4/Fixtures/new.json" ;;
success|move|occupied|restore|reappeared|reappeared-empty)
    mkdir -p "$4/Fixtures/synthetic-wall-lidar"
    echo replacement > "$4/Fixtures/synthetic-wall-lidar/session.json"
    ;;
esac
""")
    mv = bin_dir / "mv"
    mv.write_text("""#!/bin/sh
if [ "${1##*/}" = Fixtures ] && [ "$1" != "$TEST_ROOT/Fixtures" ]; then
    case "$FAILURE" in
    move|occupied|restore)
    test -d "${1%/*}/previous" || exit 2
    echo failed > "$TEST_ROOT/final-move-failed"
    if [ "$FAILURE" = occupied ]; then
        mkdir "$TEST_ROOT/Fixtures"
        echo concurrent > "$TEST_ROOT/Fixtures/other.json"
    fi
    exit 1
    ;;
    esac
fi
if [ "$FAILURE" = restore ] && [ "${1##*/}" = previous ]; then
    echo failed > "$TEST_ROOT/restore-failed"
    exit 1
fi
if [ "${1##*/}" = Fixtures ] && [ "$1" != "$TEST_ROOT/Fixtures" ]; then
    case "$FAILURE" in
    reappeared|reappeared-empty)
        mkdir "$TEST_ROOT/Fixtures"
        if [ "$FAILURE" = reappeared ]; then
            echo concurrent > "$TEST_ROOT/Fixtures/other.json"
        fi
        ;;
    esac
fi
exec /bin/mv "$@"
""")
    python = bin_dir / "python3"
    python.write_text(
        '#!/bin/sh\nhelper="$1"\nshift\n'
        + mv.read_text()
        .removeprefix("#!/bin/sh\n")
        .replace('exec /bin/mv "$@"', 'exec "$TEST_PYTHON" "$helper" "$@"')
    )
    git.chmod(0o755)
    tar.chmod(0o755)
    mv.chmod(0o755)
    python.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:/usr/bin:/bin",
        TEST_ROOT=str(tmp_path),
        FAILURE=failure,
        TEST_PYTHON=sys.executable,
    )
    result = subprocess.run(
        ["/bin/sh", str(script)], env=env, capture_output=True, text=True, check=False
    )
    if failure == "success":
        assert result.returncode == 0, result.stderr
        assert (
            fixtures / "synthetic-wall-lidar/session.json"
        ).read_text() == "replacement\n"
        assert not (fixtures / "old.json").exists()
    elif failure in ("occupied", "restore", "reappeared", "reappeared-empty"):
        assert result.returncode != 0
        if failure in ("occupied", "restore"):
            assert (tmp_path / "final-move-failed").read_text() == "failed\n"
        stages = list(tmp_path.glob(".fixtures.*"))
        assert len(stages) == 1
        backup = stages[0] / "previous"
        assert contents(backup) == original
        assert f"backup preserved at {backup}" in result.stderr
        if failure in ("occupied", "reappeared"):
            assert (fixtures / "other.json").read_text() == "concurrent\n"
            assert list(fixtures.iterdir()) == [fixtures / "other.json"]
        elif failure == "reappeared-empty":
            assert list(fixtures.iterdir()) == []
        else:
            assert (tmp_path / "restore-failed").read_text() == "failed\n"
            assert not fixtures.exists()
    else:
        assert result.returncode != 0
        assert contents(fixtures) == original
        if failure == "move":
            assert (tmp_path / "final-move-failed").read_text() == "failed\n"
        if failure == "missing-session":
            assert "lack synthetic-wall-lidar/session.json" in result.stderr
    if failure not in ("occupied", "restore", "reappeared", "reappeared-empty"):
        assert not list(tmp_path.glob(".fixtures.*"))
