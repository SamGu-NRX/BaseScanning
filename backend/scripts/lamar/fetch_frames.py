"""Fetch the three LaMAR LIN phone sessions used by A6.

Default path matches the handoff: curl the session zip, unzip
``<session>/raw_data/images/*``, delete the zip. ``--remotezip`` uses 12
threads with one RemoteZip each (the VM ``fetch_par.py`` rate, about 15 files/s).

``remotezip`` is a project data dependency.
"""

from __future__ import annotations

import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fetch_meta import SESSIONS, session_zip_url
from remotezip import RemoteZip


def image_members(namelist: list[str], session: str) -> list[str]:
    prefix = f"{session}/raw_data/images/"
    return sorted(name for name in namelist if name.startswith(prefix) and not name.endswith("/"))


def fetch_curl(session: str, raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    zip_path = raw_dir / f"{session}.zip"
    url = session_zip_url(session)
    subprocess.run(["curl", "-fsSL", "-o", str(zip_path), url], check=True)
    subprocess.run(
        ["unzip", "-q", "-o", zip_path.name, f"{session}/raw_data/images/*"],
        cwd=raw_dir,
        check=True,
    )
    zip_path.unlink()


def _read_member(url: str, member: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with RemoteZip(url) as archive:
        dest.write_bytes(archive.read(member))


def fetch_remotezip(session: str, raw_dir: Path, threads: int) -> None:
    url = session_zip_url(session)
    with RemoteZip(url) as archive:
        members = image_members(archive.namelist(), session)
    if not members:
        raise SystemExit(f"no images listed in {session}")

    def _one(member: str) -> None:
        _read_member(url, member, raw_dir / member)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(_one, member) for member in members]
        for future in as_completed(futures):
            future.result()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch three LaMAR LIN image sessions")
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--remotezip", action="store_true")
    parser.add_argument("--threads", type=int, default=12)
    args = parser.parse_args(argv)
    raw_dir = args.data.expanduser() / "raw"
    for session in SESSIONS:
        if args.remotezip:
            fetch_remotezip(session, raw_dir, args.threads)
        else:
            fetch_curl(session, raw_dir)
        print(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
