import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "fetch-fixtures.sh"


@pytest.mark.parametrize(
    "ownership", [None, "malformed", "wrong-stage", "wrong-backup"]
)
def test_locked_recovery_restores_legacy_backup_and_removes_stage(tmp_path, ownership):
    import fcntl

    helper = runpy.run_path(str(SCRIPT.with_name("rename-fixtures.py")))
    stage = tmp_path / ".fixtures.caller"
    previous = stage / "previous"
    previous.mkdir(parents=True)
    (previous / "session.json").write_text("caller data")
    if ownership is not None:
        record = {
            "stage": helper["identity"](stage),
            "previous": helper["identity"](previous),
        }
        if ownership == "wrong-stage":
            record["stage"] = [-1, -1]
        elif ownership == "wrong-backup":
            record["previous"] = [-1, -1]
        (stage / "ownership.json").write_text(
            "{" if ownership == "malformed" else json.dumps(record)
        )
    before = contents(previous)
    with (tmp_path / ".fixtures-refresh.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        helper["recover_backup"](tmp_path)
    assert contents(tmp_path / "Fixtures") == before
    assert not stage.exists()


def contents(folder):
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("published", [False, True])
def test_locked_recovery_reclaims_a_stage_killed_before_its_marker(tmp_path, published):
    import fcntl

    helper = runpy.run_path(str(SCRIPT.with_name("rename-fixtures.py")))
    stage = tmp_path / ".fixtures.unmarked"
    stage.mkdir()
    (stage / "fixtures.tar").write_bytes(b"unfinished archive")
    if published:
        (tmp_path / "Fixtures").mkdir()
        (tmp_path / "Fixtures/session.json").write_text("current")
    with (tmp_path / ".fixtures-refresh.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        helper["recover_backup"](tmp_path)
    assert not stage.exists()
    if published:
        assert (tmp_path / "Fixtures/session.json").read_text() == "current"


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
        "hard-kill",
        "kill-extract",
        "kill-published",
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
success|move|occupied|restore|reappeared|reappeared-empty|hard-kill|kill-extract|kill-published)
    mkdir -p "$4/Fixtures/synthetic-wall-lidar"
    echo replacement > "$4/Fixtures/synthetic-wall-lidar/session.json"
    ;;
esac
if [ "$FAILURE" = kill-extract ]; then
    kill -KILL "$PPID"
fi
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
        .replace(
            'exec /bin/mv "$@"',
            '''if [ "$FAILURE" = hard-kill ] && [ "$1" = "$TEST_ROOT/Fixtures" ]; then
    "$TEST_PYTHON" "$helper" "$@" || exit 1
    kill -KILL "$PPID"
    exit 0
fi
if [ "$FAILURE" = kill-published ] && [ "${1##*/}" = Fixtures ] && [ "$1" != "$TEST_ROOT/Fixtures" ]; then
    "$TEST_PYTHON" "$helper" "$@" || exit 1
    kill -KILL "$PPID"
    exit 0
fi
exec "$TEST_PYTHON" "$helper" "$@"''',
        )
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
    if failure in ("hard-kill", "kill-extract", "kill-published"):
        assert result.returncode == -9, result.stderr
        stages = list(tmp_path.glob(".fixtures.*"))
        assert len(stages) == 1
        assert (stages[0] / "ownership.json").is_file()
        if failure == "hard-kill":
            assert not fixtures.exists()
        if failure != "kill-extract":
            assert contents(stages[0] / "previous") == original
        else:
            assert not (stages[0] / "previous").exists()
        expected = contents(fixtures) if fixtures.exists() else original
        # The first git invocation must already see the restored fixtures. Then fail
        # archive creation, so neither fetching nor a successful swap can mask recovery.
        git.write_text(
            git.read_text().replace(
                'rev-parse) echo "$TEST_ROOT" ;;',
                'rev-parse) test -d "$TEST_ROOT/Fixtures" || exit 2; '
                'for stage in "$TEST_ROOT"/.fixtures.*; do test ! -d "$stage" || exit 2; done; '
                'echo "$TEST_ROOT" ;;',
            )
        )
        again = subprocess.run(
            ["/bin/sh", str(script)],
            env={**env, "FAILURE": "archive"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert again.returncode == 1, again.stderr
        assert contents(fixtures) == expected
        if failure == "kill-published":
            assert "Fixtures is occupied; backup preserved" in again.stderr
            assert str(stages[0]) in again.stderr
            assert contents(stages[0] / "previous") == original
    elif failure == "success":
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
        if failure != "restore":
            destination_before = contents(fixtures)
            again = subprocess.run(
                ["/bin/sh", str(script)],
                env={**env, "FAILURE": "archive"},
                capture_output=True,
                text=True,
                check=False,
            )
            assert again.returncode != 0
            assert "Fixtures is occupied; backup preserved" in again.stderr
            assert str(stages[0]) in again.stderr
            assert contents(backup) == original
            assert contents(fixtures) == destination_before
    else:
        assert result.returncode != 0
        assert contents(fixtures) == original
        if failure == "move":
            assert (tmp_path / "final-move-failed").read_text() == "failed\n"
        if failure == "missing-session":
            assert "lack synthetic-wall-lidar/session.json" in result.stderr
    if failure not in (
        "occupied",
        "restore",
        "reappeared",
        "reappeared-empty",
        "kill-published",
    ):
        assert not list(tmp_path.glob(".fixtures.*"))


def test_overlapping_refresh_waits_before_recovery(tmp_path):
    import fcntl
    import select

    script = tmp_path / SCRIPT.name
    script.write_text(SCRIPT.read_text())
    helper = tmp_path / "rename-fixtures.py"
    source = SCRIPT.with_name(helper.name).read_text()
    # Pause the first process after backup creation, before publication. Announce
    # the second lock attempt so the test does not depend on process startup speed.
    source = source.replace(
        "def rename_exclusive(source: str, destination: str) -> None:",
        """def rename_exclusive(source: str, destination: str) -> None:
    if os.environ.get("REFRESH") == "first" and Path(source).name == "Fixtures" and Path(destination).name == "Fixtures" and Path(source).parent != Path(destination).parent:
        print("first paused", flush=True)
        input()
""",
    ).replace(
        "fcntl.flock(lock.fileno(), fcntl.LOCK_EX)",
        """if os.environ.get("REFRESH") == "second":
            print("second waiting", flush=True)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if os.environ.get("REFRESH") == "second":
            print("second locked", flush=True)""",
    )
    helper.write_text(source)
    fixtures = tmp_path / "Fixtures"
    fixtures.mkdir()
    (fixtures / "old.json").write_text("original")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in {
        "git": """#!/bin/sh
case "$3" in
rev-parse) echo "$TEST_ROOT" ;;
archive) : > "${4#--output=}" ;;
esac
""",
        "tar": """#!/bin/sh
mkdir -p "$4/Fixtures/synthetic-wall-lidar"
echo "$REFRESH" > "$4/Fixtures/synthetic-wall-lidar/session.json"
""",
        "python3": f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
    }.items():
        path = bin_dir / name
        path.write_text(text)
        path.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "TEST_ROOT": str(tmp_path)}
    processes = []

    def start(label):
        process = subprocess.Popen(
            ["/bin/sh", str(script)],
            env={**env, "REFRESH": label},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(process)
        return process

    def line(process):
        assert select.select([process.stdout], [], [], 5)[0], (
            "refresh did not reach barrier"
        )
        return process.stdout.readline().strip()

    try:
        first = start("first")
        assert line(first) == "first paused"
        (stage,) = tmp_path.glob(".fixtures.*")
        assert not fixtures.exists()
        before = contents(stage)
        second = start("second")
        assert line(second) == "second waiting"
        with (
            (tmp_path / ".fixtures-refresh.lock").open("a") as lock,
            pytest.raises(BlockingIOError),
        ):
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert contents(stage) == before
        first.stdin.write("continue\n")
        first.stdin.flush()
        _, err = first.communicate(timeout=5)
        assert first.returncode == 0, err
        output, err = second.communicate(timeout=5)
        assert second.returncode == 0, err
        assert "second locked" in output
        assert (
            fixtures / "synthetic-wall-lidar/session.json"
        ).read_text() == "second\n"
        assert not list(tmp_path.glob(".fixtures.*"))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
