"""Image headers, decode and HEIC transcode."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image

from server.packet.integrity import resolve_under
from server.packet.params import PacketParams
from server.packet.purposes import still_id_issue
from server.packet.report import Bag, pointer

HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}


def sniff_format(data: bytes) -> str:
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in HEIC_BRANDS:
        return "heic"
    return "other"


def _header(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(32)


def iter_images(pk: dict):
    for i, frame in enumerate(pk.get("keyframes") or []):
        yield "keyframe", i, frame
    for i, still in enumerate(pk.get("stills") or []):
        yield "still", i, still


def _register_heif() -> None:
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        return


def check_headers(pk: dict, root: Path, bag: Bag, listed: dict[str, dict]) -> None:
    _register_heif()
    tier = (pk.get("session") or {}).get("tier")
    for kind, i, record in iter_images(pk):
        rel = record.get("img")
        if not isinstance(rel, str) or rel not in listed:
            continue
        full = resolve_under(root, rel)
        if full is None or not full.is_file():
            continue
        fmt = sniff_format(_header(full))
        ptr = pointer("keyframes" if kind == "keyframe" else "stills", i, "img")
        if fmt == "jpeg":
            pass
        elif fmt == "heic" and kind == "still" and tier == "photo_only":
            pass
        else:
            bag.error("image_format", ptr, f"{rel} is {fmt}, expected jpeg")
            continue
        try:
            with Image.open(full) as image:
                width, height = image.size
        except Exception:
            bag.error("image_undecodable", ptr, f"{rel} header could not be read")
            continue
        try:
            expect = (int(record["w"]), int(record["h"]))
        except (KeyError, TypeError, ValueError):
            bag.error("schema", ptr, "image width and height must be integers")
            continue
        if (width, height) != expect and not (fmt == "heic" and (width, height) == (expect[1], expect[0])):
            bag.error(
                "image_size_mismatch",
                ptr,
                f"{rel} header is {width}x{height}, packet says {expect[0]}x{expect[1]}",
            )


def decode_all(pk: dict, root: Path, listed: dict[str, dict]) -> dict[tuple[str, int], bool]:
    """Full decode. Frames that already failed the format check are left undecodable."""
    _register_heif()
    out: dict[tuple[str, int], bool] = {}
    for kind, i, record in iter_images(pk):
        rel = record.get("img")
        key = (kind, i)
        if not isinstance(rel, str) or rel not in listed:
            out[key] = False
            continue
        full = resolve_under(root, rel)
        if full is None or not full.is_file():
            out[key] = False
            continue
        if sniff_format(_header(full)) == "other":
            out[key] = False
            continue
        try:
            with Image.open(full) as image:
                image.load()
            out[key] = True
        except Exception:
            out[key] = False
    return out


def images_usable(decodable: dict[tuple[str, int], bool], params: PacketParams) -> tuple[bool, int]:
    total = len(decodable)
    good = sum(1 for ok in decodable.values() if ok)
    if total == 0:
        return False, 0
    need = total if total < params.images_usable_min else params.images_usable_min
    share = good / total
    return share >= params.images_usable_share and good >= need, good


def _paths_with_file_errors(pk: dict, bag: Bag) -> set[str]:
    """A hash or size failure already explains the bytes. Don't also call them undecodable.

    Index the error pointers once. Scanning every error for every `files[]` row is quadratic
    and a 200k manifest does not finish.
    """
    indexes: set[int] = set()
    for issue in bag.errors:
        if issue.level != "error":
            continue
        parts = issue.pointer.split("/")
        if len(parts) >= 3 and parts[1] == "files" and parts[2].isdigit():
            indexes.add(int(parts[2]))
    bad: set[str] = set()
    files_raw = pk.get("files")
    files = files_raw if isinstance(files_raw, list) else []
    for index in indexes:
        if index >= len(files) or not isinstance(files[index], dict):
            continue
        path = files[index].get("path")
        if isinstance(path, str):
            bad.add(path)
    return bad


def mark_undecodable(pk: dict, root: Path, bag: Bag, decoded: dict[tuple[str, int], bool]) -> None:
    bad_paths = _paths_with_file_errors(pk, bag)
    for (kind, i), ok in decoded.items():
        if ok:
            continue
        records = pk.get("keyframes") if kind == "keyframe" else pk.get("stills")
        if not isinstance(records, list) or i >= len(records):
            continue
        record = records[i]
        rel = record.get("img")
        if not isinstance(rel, str) or rel in bad_paths:
            continue
        ptr = pointer("keyframes" if kind == "keyframe" else "stills", i, "img")
        if any(issue.pointer == ptr and issue.level == "error" for issue in bag.errors):
            continue
        full = resolve_under(root, rel)
        if full is None or not full.is_file():
            continue
        if sniff_format(_header(full)) == "other":
            continue
        bag.error("image_undecodable", ptr, f"{rel} could not be decoded")


def warn_heic(pk: dict, root: Path, bag: Bag) -> None:
    """photo_only HEIC stills are transcoded later. Record that on the validation report."""
    session = pk.get("session")
    if not isinstance(session, dict) or session.get("tier") != "photo_only":
        return
    _register_heif()
    for i, still in enumerate(pk.get("stills") or []):
        if not isinstance(still, dict):
            continue
        rel = still.get("img")
        if not isinstance(rel, str) or still_id_issue(still.get("id")) is not None:
            continue
        full = resolve_under(root, rel)
        if full is None or not full.is_file() or sniff_format(_header(full)) != "heic":
            continue
        bag.warn("heic_transcoded", pointer("stills", i, "img"), "HEIC still will be transcoded to JPEG")
        swapped = False
        orientation = still.get("orientation")
        try:
            with Image.open(full) as image:
                width, height = image.size
                orientation = image.info.get("original_orientation", orientation)
            packet_w, packet_h = int(still["w"]), int(still["h"])
            swapped = (packet_w, packet_h) == (height, width) and (packet_w, packet_h) != (width, height)
        except Exception:
            swapped = False
        if swapped or orientation not in (None, 1):
            bag.warn(
                "heic_orientation_applied",
                pointer("stills", i, "orientation"),
                "HEIC orientation is applied when the still is transcoded",
            )


def transcode_heic(pk: dict, root: Path, params: PacketParams) -> list[str]:
    """Write upright JPEGs for photo_only HEIC stills. Returns notes."""
    session = pk.get("session")
    if not isinstance(session, dict) or session.get("tier") != "photo_only":
        return []
    try:
        import pillow_heif
    except ImportError:
        return []
    pillow_heif.register_heif_opener()
    notes: list[str] = []
    files = pk.get("files")
    if not isinstance(files, list):
        files = []
        pk["files"] = files
    listed = {entry["path"] for entry in files if isinstance(entry, dict) and isinstance(entry.get("path"), str)}
    for still in pk.get("stills") or []:
        if not isinstance(still, dict):
            continue
        rel = still.get("img")
        full = resolve_under(root, rel) if isinstance(rel, str) else None
        if full is None or not full.is_file() or sniff_format(_header(full)) != "heic":
            continue
        try:
            with Image.open(full) as image:
                image.load()
                width, height = image.size
                original_orientation = image.info.get("original_orientation")
                exif_bytes = image.info.get("exif")
                buffer = io.BytesIO()
                save_kwargs: dict = {"format": "JPEG", "quality": params.heic_quality}
                if exif_bytes:
                    save_kwargs["exif"] = exif_bytes
                image.save(buffer, **save_kwargs)
        except Exception:
            continue
        payload = buffer.getvalue()
        still_id = still.get("id")
        if still_id_issue(still_id) is not None:
            continue
        dest_rel = f"normalized/stills/{still_id}.jpg"
        dest = resolve_under(root, dest_rel)
        if dest is None:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        try:
            packet_wh = (int(still["w"]), int(still["h"]))
        except (KeyError, TypeError, ValueError):
            packet_wh = (width, height)
        swapped = packet_wh == (height, width) and packet_wh != (width, height)
        raw_ext = still.get("ext")
        ext = dict(raw_ext) if isinstance(raw_ext, dict) else {}
        raw_hss = ext.get("hss")
        hss = dict(raw_hss) if isinstance(raw_hss, dict) else {}
        hss["originalImg"] = rel
        hss["originalOrientation"] = original_orientation
        ext["hss"] = hss
        still["ext"] = ext
        still["img"] = dest_rel
        still["w"] = width
        still["h"] = height
        still["orientation"] = 1
        if dest_rel not in listed:
            files.append(
                {
                    "path": dest_rel,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "role": "compat",
                }
            )
            listed.add(dest_rel)
        note = f"still {still['id']}: HEIC transcoded to JPEG q{params.heic_quality}, EXIF kept, orientation applied"
        notes.append(note)
        still.setdefault("_hss_notes", []).append("heic_transcoded")
        if swapped or (original_orientation not in (None, 1)):
            still["_hss_notes"].append("heic_orientation_applied")
    return notes
