"""CI helpers for optional native libraries.

The test job syncs the dev group, which installs Open3D, then runs pytest with
no apt step. Open3D's wheel needs libEGL.so.1, and ubuntu-latest does not ship
it. Installing that library here is what lets the TSDF tests execute.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

_OPEN3D_APT = (
    "libegl1",
    "libgl1",
    "libgl1-mesa-dri",
    "libglib2.0-0",
    "libglvnd0",
    "libgomp1",
    "libopengl0",
    "libusb-1.0-0",
    "libx11-6",
)


def _drop_open3d() -> None:
    for name in list(sys.modules):
        if name == "open3d" or name.startswith("open3d."):
            del sys.modules[name]


def _missing_gl_library(exc: BaseException) -> bool:
    text = str(exc)
    return "libEGL" in text or "libGL" in text or ".so" in text


def _install_open3d_system_libs() -> None:
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    subprocess.check_call(["sudo", "apt-get", "update", "-qq"], env=env)
    subprocess.check_call(["sudo", "apt-get", "install", "-y", "-qq", *_OPEN3D_APT], env=env)


def pytest_configure(config: object) -> None:
    del config
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    os.environ.setdefault("EGL_PLATFORM", "surfaceless")
    try:
        importlib.import_module("open3d")
    except ImportError as exc:
        if not _missing_gl_library(exc):
            return
        _drop_open3d()
        _install_open3d_system_libs()
        _drop_open3d()
        importlib.import_module("open3d")
