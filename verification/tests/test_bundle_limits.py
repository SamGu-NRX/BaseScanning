import ast
import io
import struct
import zipfile
from pathlib import Path

import pytest

from hsverify.bundle_limits import zip_directory_limits


def recon_guard(max_entries, max_bytes):
    # Execute only the stdlib guard so this parity check needs no recon model dependencies.
    source = Path(__file__).resolve().parents[2] / "recon" / "recon" / "capture.py"
    tree = ast.parse(source.read_text())
    guard = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_zip_directory_limits"
    )
    namespace = {
        "Path": Path,
        "struct": struct,
        "MAX_BUNDLE_ENTRIES": max_entries,
        "MAX_BUNDLE_BYTES": max_bytes,
    }
    exec(compile(ast.Module(body=[guard], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_zip_directory_limits"]


def ordinary_zip():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("scene.json", b"{}")
        archive.writestr("frames/", b"")
        archive.writestr("frames/a.jpg", b"image")
    return bytearray(data.getvalue())


@pytest.mark.parametrize(
    "mutation",
    [
        "ordinary",
        "entries",
        "bytes",
        "false_count",
        "comment",
        "footer_in_comment",
        "split",
        "directory_offset",
        "directory_record",
        "zip64_locator",
        "truncated",
    ],
)
def test_admission_matches_recon_guard(tmp_path, mutation):
    data = ordinary_zip()
    entries, expanded = 3, 7
    footer = len(data) - 22
    directory = struct.unpack_from("<I", data, footer + 16)[0]
    if mutation == "entries":
        entries = 2
    elif mutation == "bytes":
        expanded = 6
    elif mutation == "false_count":
        struct.pack_into("<HH", data, footer + 8, 1, 1)
        entries = 2
    elif mutation in ("comment", "footer_in_comment"):
        comment = b"valid comment" if mutation == "comment" else b"PK\x05\x06"
        struct.pack_into("<H", data, footer + 20, len(comment))
        data.extend(comment)
    elif mutation == "split":
        struct.pack_into("<H", data, footer + 4, 1)
    elif mutation == "directory_offset":
        struct.pack_into("<I", data, footer + 16, directory + 1)
    elif mutation == "directory_record":
        data[directory : directory + 4] = b"bad!"
    elif mutation == "zip64_locator":
        data[footer:footer] = b"PK\x06\x07" + bytes(16)
    elif mutation == "truncated":
        data = data[:-5]
    path = tmp_path / "bundle.zip"
    path.write_bytes(data)

    def outcome(guard):
        try:
            guard(path)
        except ValueError as exc:
            return str(exc)
        return None

    expected = outcome(recon_guard(entries, expanded))
    assert outcome(lambda p: zip_directory_limits(p, entries, expanded)) == expected
    assert (expected is None) == (mutation in ("ordinary", "comment"))
