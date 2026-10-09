"""Download, verify and unpack the public datasets the evals read.

    uv run python -m evals.datasets advio      # ADVIO sequences 20 to 23 (about 800 MB of zips)
    uv run python -m evals.datasets eth3d      # ETH3D facade and electro (about 2.4 GB of archives)

Each archive is checked against its size and sha256 below, unpacked (only the files the evals use),
then deleted. A completion record, written only after every expected file is in place, ties the
unpacked files to the archive's sha256; a later run skips the archive only if that record matches
and every recorded file is still there at its recorded size. A download refuses to start with less than `MIN_FREE_GB` free, because the Mac these
run on is shared. Both datasets are for non-commercial research: they measure accuracy here and are
never committed or redistributed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import py7zr

from evals.paths import ADVIO_DIR, ETH3D_DIR

MIN_FREE_GB = 6.0


@dataclass(frozen=True)
class Archive:
    url: str
    size: int
    sha256: str
    dest: Path
    marker: str  # a path under `dest` that exists once the archive is unpacked
    members: tuple[str, ...] = ()  # zip members to extract (prefix match); empty means all

    @property
    def name(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def _advio(n: int, size: int, sha: str, keep_video: bool = False) -> Archive:
    base = f"advio-{n:02d}/"
    members = (base + "iphone/", base + "ground-truth/", base + "pixel/arcore.csv")
    if not keep_video:
        members = (
            base + "iphone/arkit.csv",
            base + "iphone/frames.csv",
            base + "iphone/platform-locations.csv",
            base + "ground-truth/",
            base + "pixel/arcore.csv",
        )
    url = f"https://zenodo.org/record/1476931/files/advio-{n:02d}.zip"
    return Archive(url, size, sha, ADVIO_DIR, base + "iphone/arkit.csv", members)


ADVIO = [
    # Sequence 20 keeps its video: the replay session is cut from it.
    _advio(
        20,
        195538352,
        "be21154394df09d6ecebe1894062f290bb53d74a5c6ccbca4b3a69f91ee2c8a2",
        keep_video=True,
    ),
    _advio(21, 209791503, "fb8a1cf3f645bbd9ea848e66924e83ae75b5cd18d632986e5629af1eecb58570"),
    _advio(22, 254664089, "96c7212fc9cb610a88ba9fa62ac2581f0cdae49e80007888698ee2cf5ebd56f4"),
    _advio(23, 134340011, "726d9b80d30036a8585f6cecaae385236b689cc4b8b19be6362c6df223825dc1"),
]

_ETH = "https://www.eth3d.net/data/"
ETH3D = [
    Archive(
        _ETH + "facade_dslr_undistorted.7z",
        1252088400,
        "046e577388db0633eeb2d8d72da6a2de53857434b4a7a98e4c97485795f9ce82",
        ETH3D_DIR,
        "facade/images",
    ),
    Archive(
        _ETH + "facade_dslr_scan_eval.7z",
        176517224,
        "d686462d417fbea010d0021f918bec8b881444929ec06e3bd4e8a5230ebf59a8",
        ETH3D_DIR,
        "facade/dslr_scan_eval",
    ),
    Archive(
        _ETH + "facade_dslr_occlusion.7z",
        144292883,
        "26a9d2076ba16b7f8e24311579c47b0b7b826b180906ced20fa1cba8f3dd130d",
        ETH3D_DIR,
        "facade/occlusion",
    ),
    Archive(
        _ETH + "electro_dslr_undistorted.7z",
        514345623,
        "0d2bc31fec0032b8fb20703abba8e45ef7a395c13d2d3476adc1f4dd4ffd7d8b",
        ETH3D_DIR,
        "electro/images",
    ),
    Archive(
        _ETH + "electro_dslr_scan_eval.7z",
        250703286,
        "5ca10a73e7da0e3c511e255bba5656cca4c372dc00c1417c97b75228a5bb3bac",
        ETH3D_DIR,
        "electro/dslr_scan_eval",
    ),
    Archive(
        _ETH + "electro_dslr_occlusion.7z",
        47214879,
        "cc25a22de9fc27251a5178454c24785671a2b2c199756a0d03297413290830b3",
        ETH3D_DIR,
        "electro/occlusion",
    ),
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


DOWNLOAD_TIMEOUT_S = 60  # per socket operation: a stalled server fails instead of hanging


def fetch(archive: Archive) -> None:
    archive.dest.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(archive.dest).free / 1e9
    need_gb = 2.5 * archive.size / 1e9  # archive plus unpacked copy, with margin
    if free_gb - need_gb < MIN_FREE_GB:
        raise SystemExit(
            f"{archive.name}: {free_gb:.1f} GB free, need {need_gb:.1f} GB plus {MIN_FREE_GB} GB reserve"
        )
    tmp = archive.dest / (archive.name + ".part")
    print(f"downloading {archive.url}")
    try:
        with (
            urllib.request.urlopen(archive.url, timeout=DOWNLOAD_TIMEOUT_S) as resp,
            tmp.open("wb") as out,
        ):
            shutil.copyfileobj(resp, out, 1 << 20)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    size = tmp.stat().st_size
    digest = sha256_of(tmp)
    if size != archive.size or digest != archive.sha256:
        tmp.unlink()
        raise SystemExit(
            f"{archive.name}: got {size} bytes sha256 {digest}, expected {archive.size} {archive.sha256}"
        )
    record_path(archive).unlink(missing_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(tmp) as z:
            names = [
                n for n in z.namelist() if not archive.members or n.startswith(archive.members)
            ]
            z.extractall(archive.dest, members=names)
    else:
        with py7zr.SevenZipFile(tmp) as z:
            names = z.getnames()
            z.extractall(archive.dest)
    tmp.unlink()
    write_record(archive, names)
    print(f"  verified and unpacked into {archive.dest}")


def record_path(archive: Archive) -> Path:
    return archive.dest / ".complete" / f"{archive.name}.json"


def write_record(archive: Archive, names: list[str]) -> None:
    """Record the unpacked files once every expected one is present: the marker, and at least one
    file under each requested member."""
    files = {n: (archive.dest / n).stat().st_size for n in names if (archive.dest / n).is_file()}
    missing = [m for m in archive.members if not any(n.startswith(m) for n in files)]
    if not (archive.dest / archive.marker).exists():
        missing.append(archive.marker)
    if missing:
        raise SystemExit(f"{archive.name}: unpacked without {missing}; not recording it complete")
    path = record_path(archive)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"sha256": archive.sha256, "files": files}, indent=0))


def is_complete(archive: Archive) -> bool:
    """True when this archive's record matches its pinned sha256 and every recorded file exists at
    its recorded size. Sizes catch a truncated or partly deleted copy without rehashing gigabytes;
    an edit that keeps a file's size is not caught."""
    path = record_path(archive)
    if not path.exists():
        return False
    try:
        record = json.loads(path.read_text())
    except json.JSONDecodeError:
        return False  # a truncated record reads as not complete: the next run redownloads
    if (
        not isinstance(record, dict)
        or record.get("sha256") != archive.sha256
        or not record.get("files")
    ):
        return False
    for name, size in record["files"].items():
        f = archive.dest / name
        if not f.is_file() or f.stat().st_size != size:
            return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("dataset", choices=["advio", "eth3d"])
    ap.add_argument(
        "--force", action="store_true", help="download even if the data is already unpacked"
    )
    args = ap.parse_args()
    for a in ADVIO if args.dataset == "advio" else ETH3D:
        if is_complete(a) and not args.force:
            print(f"{a.name}: already unpacked and recorded complete ({record_path(a)})")
            continue
        fetch(a)


if __name__ == "__main__":
    main()
