"""Rename a fixture directory without nesting or replacing an existing destination."""

import ctypes
import os
import sys


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
    rename_exclusive(*sys.argv[1:])
