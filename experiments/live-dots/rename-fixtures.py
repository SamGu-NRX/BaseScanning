"""Rename a fixture directory without nesting or replacing an existing destination."""

import ctypes
import json
import os
import shutil
import sys
from pathlib import Path


def identity(path: Path) -> list[int]:
    stat = path.lstat()
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{path}: expected a fixture directory")
    return [stat.st_dev, stat.st_ino]


def mark_backup(stage: Path, fixtures: Path) -> None:
    (stage / "ownership.json").write_text(
        json.dumps({"stage": identity(stage), "previous": identity(fixtures)})
    )


def recover_backup(here: Path) -> None:
    fixtures = here / "Fixtures"
    if fixtures.exists() or fixtures.is_symlink():
        return
    owned = []
    for stage in here.glob(".fixtures.*"):
        try:
            record = json.loads((stage / "ownership.json").read_text())
            if (
                isinstance(record, dict)
                and record.get("stage") == identity(stage)
                and record.get("previous") == identity(stage / "previous")
            ):
                owned.append(stage)
        except (OSError, ValueError):
            continue
    if len(owned) > 1:
        raise RuntimeError(
            "multiple owned fixture backups; inspect them before refreshing"
        )
    if owned:
        stage = owned[0]
        rename_exclusive(str(stage / "previous"), str(fixtures))
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
    if sys.argv[1] == "--recover":
        recover_backup(Path(sys.argv[2]))
    elif sys.argv[1] == "--mark-backup":
        mark_backup(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        rename_exclusive(*sys.argv[1:])
