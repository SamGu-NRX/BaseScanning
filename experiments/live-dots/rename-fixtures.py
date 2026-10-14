"""Rename a fixture directory without nesting or replacing an existing destination."""

import ctypes
import fcntl
import json
import os
import shutil
import sys
from pathlib import Path


def refresh(script: Path) -> None:
    # Exec keeps the lock in the shell itself, including its cleanup trap. A hard
    # exit releases it without leaving a supervisor that can block recovery.
    with (script.parent / ".fixtures-refresh.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        os.set_inheritable(lock.fileno(), True)
        os.execv("/bin/sh", ["/bin/sh", str(script), "--lock-held"])


def identity(path: Path) -> list[int]:
    stat = path.lstat()
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{path}: expected a fixture directory")
    return [stat.st_dev, stat.st_ino]


def mark_backup(stage: Path, fixtures: Path) -> None:
    (stage / "ownership.json").write_text(
        json.dumps({"stage": identity(stage), "previous": identity(fixtures)})
    )


def mark_stage(stage: Path) -> None:
    (stage / "ownership.json").write_text(json.dumps({"stage": identity(stage)}))


def recover_backup(here: Path) -> None:
    fixtures = here / "Fixtures"
    # This namespace is reserved for refresh stages. The caller holds the refresh
    # lock, so even stages killed before their marker was written are abandoned.
    stages = [
        stage
        for stage in here.glob(".fixtures.*")
        if stage.is_dir() and not stage.is_symlink()
    ]
    if not fixtures.exists() and not fixtures.is_symlink():
        backups = [
            stage / "previous"
            for stage in stages
            if (stage / "previous").exists() or (stage / "previous").is_symlink()
        ]
        if len(backups) > 1:
            raise RuntimeError(
                "multiple fixture backups; inspect them before refreshing"
            )
        if backups:
            identity(backups[0])
            rename_exclusive(str(backups[0]), str(fixtures))
    for stage in stages:
        shutil.rmtree(stage)


def rename_exclusive(source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (os.fsencode(source), os.fsencode(destination), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        args = (
            -100,
            os.fsencode(source),
            -100,
            os.fsencode(destination),
            1,
        )  # AT_FDCWD, NOREPLACE
    else:
        raise RuntimeError(
            "fixture replacement requires macOS or Linux exclusive rename"
        )
    rename.restype = ctypes.c_int
    if rename(*args) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


if __name__ == "__main__":
    if sys.argv[1] == "--refresh":
        refresh(Path(sys.argv[2]))
    elif sys.argv[1] == "--recover":
        recover_backup(Path(sys.argv[2]))
    elif sys.argv[1] == "--mark-backup":
        mark_backup(Path(sys.argv[2]), Path(sys.argv[3]))
    elif sys.argv[1] == "--mark-stage":
        mark_stage(Path(sys.argv[2]))
    else:
        rename_exclusive(*sys.argv[1:])
