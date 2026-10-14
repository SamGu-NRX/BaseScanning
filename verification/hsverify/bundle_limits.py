"""Bound ZIP metadata before the verification harness constructs a ZipFile."""

import struct
from pathlib import Path


def zip_directory_limits(path: Path, max_entries: int, max_bytes: int) -> None:
    """Check central-directory records in bounded reads before ZipFile allocates their metadata.
    ZIP64 and split archives exceed this worker's ordinary ZIP admission format and are refused.
    The record layouts are PKWARE APPNOTE sections 4.3.12 and 4.3.16."""
    with path.open("rb") as f:
        size = path.stat().st_size
        f.seek(max(0, size - 22 - 65535))
        tail = f.read(22 + 65535)
        # Match ZipFile's footer selection: a no-comment footer at EOF, otherwise the last
        # signature. Backtracking past malformed signatures can validate a different directory
        # from the one ZipFile later allocates. Refuse that ambiguity before constructing it.
        if len(tail) >= 22 and tail[-22:-18] == b"PK\x05\x06" and tail[-2:] == b"\0\0":
            pos = len(tail) - 22
        else:
            pos = tail.rfind(b"PK\x05\x06")
        if pos < 0:
            raise ValueError(f"{path}: missing ZIP end record")
        if pos + 22 > len(tail):
            raise ValueError(f"{path}: truncated ZIP end record")
        record = struct.unpack("<4s4H2IH", tail[pos : pos + 22])
        if pos + 22 + record[-1] != len(tail):
            raise ValueError(f"{path}: ambiguous ZIP end record or invalid comment length")
        _, disk, central_disk, disk_entries, total_entries, length, offset, _ = record
        if disk or central_disk or disk_entries != total_entries:
            raise ValueError(f"{path}: split ZIP archives are not supported")
        if total_entries > max_entries:
            raise ValueError(f"{path}: {total_entries} entries, limit {max_entries}")
        directory_end = offset + length
        end_offset = size - len(tail) + pos
        # ZipFile checks this locator even when the ordinary footer has no ZIP64 sentinels.
        # It must not replace the directory fields after the bounded scan below.
        if end_offset >= 20:
            f.seek(end_offset - 20)
            if f.read(4) == b"PK\x06\x07":
                raise ValueError(f"{path}: ZIP64 archive metadata is not supported")
        if length == 0xFFFFFFFF or offset == 0xFFFFFFFF or directory_end != end_offset:
            raise ValueError(f"{path}: ZIP64 or inconsistent ZIP directory")
        f.seek(offset)
        count, expanded = 0, 0
        while f.tell() < directory_end:
            header = f.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise ValueError(f"{path}: invalid ZIP central-directory record")
            fields = struct.unpack("<4s6H3I5H2I", header)
            count += 1
            expanded += fields[9]
            if count > max_entries:
                raise ValueError(f"{path}: {count} entries, limit {max_entries}")
            if expanded > max_bytes:
                raise ValueError(f"{path}: expanded archive exceeds {max_bytes} bytes")
            f.seek(sum(fields[10:13]), 1)
            if f.tell() > directory_end:
                raise ValueError(f"{path}: ZIP central-directory record exceeds directory")
        if count != total_entries:
            raise ValueError(f"{path}: inconsistent ZIP entry count")
