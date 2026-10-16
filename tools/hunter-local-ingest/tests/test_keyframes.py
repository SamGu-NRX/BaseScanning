"""The synthetic keyframes are real, decodable JPEGs.

The first REAL_API review caught the earlier fixture: SHA-256 noise bytes
declared ``image/jpeg`` decode nowhere, so any reader that opens the
captured images fails. These tests pin the contract the fixture now
carries: each keyframe is a baseline JFIF JPEG that a strict marker,
huffman and IDCT decoder (this file, standard-library only) turns back
into the seeded tile mosaic the generator wrote, at the packet's
declared dimensions, byte for byte reproducible per seed.

The decoder here walks the actual segment stream, reads the file's own
quantization and huffman tables, un-stuffs entropy bytes, decodes the
DC/AC coefficient stream and runs the published 2D IDCT, so it fails on
noise bytes at the first marker and fails on any structural shortcut.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from hunter_local_ingest.fixturegen import build, keyframe_jpeg, keyframe_tiles, write_plan

KEYFRAME_PATHS = tuple(f"keyframes/kf{i + 1:02d}.jpg" for i in range(3))
WIDTH, HEIGHT = 64, 48


class _JpegError(ValueError):
    """Not the JPEG the contract requires."""


class _BitReader:
    """Entropy-coded bytes with JPEG byte un-stuffing (FF00 -> FF)."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._acc = 0
        self._n = 0

    def bit(self) -> int:
        if self._n == 0:
            if self._pos >= len(self._data):
                raise _JpegError("entropy data exhausted")
            byte = self._data[self._pos]
            self._pos += 1
            if byte == 0xFF:
                if self._pos >= len(self._data) or self._data[self._pos] != 0x00:
                    raise _JpegError("marker inside entropy data")
                self._pos += 1
            self._acc = byte
            self._n = 8
        self._n -= 1
        return (self._acc >> self._n) & 1

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value


def _codes_from_table(bits: list[int], symbols: list[int]) -> dict[int, tuple[int, int]]:
    """Canonical huffman codes (JPEG spec C.2): (code, length) per symbol."""
    codes: dict[int, tuple[int, int]] = {}
    code = 0
    index = 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            if index >= len(symbols):
                raise _JpegError("huffman table longer than its symbols")
            codes[symbols[index]] = (code, length)
            index += 1
            code += 1
        code <<= 1
    if index != len(symbols):
        raise _JpegError("huffman table shorter than its symbols")
    return codes


def _decode_huffman(reader: _BitReader, codes: dict[int, tuple[int, int]]) -> int:
    code = 0
    for length in range(1, 17):
        code = (code << 1) | reader.bit()
        for symbol, (known, known_length) in codes.items():
            if known_length == length and known == code:
                return symbol
    raise _JpegError("no huffman code matches")


def _extend(value: int, category: int) -> int:
    return value if value >= (1 << (category - 1)) else value - (1 << category) + 1


def _idct(coefficients: list[int], quant: list[int]) -> list[list[float]]:
    """The published 2D IDCT (JPEG spec A.3.3) over one dequantized block."""
    spatial = [[0.0] * 8 for _ in range(8)]
    for x in range(8):
        for y in range(8):
            total = 0.0
            for u in range(8):
                cu = 1.0 / math.sqrt(2.0) if u == 0 else 1.0
                for v in range(8):
                    cv = 1.0 / math.sqrt(2.0) if v == 0 else 1.0
                    total += (
                        cu
                        * cv
                        * coefficients[v * 8 + u]
                        * quant[v * 8 + u]
                        * math.cos((2 * x + 1) * u * math.pi / 16)
                        * math.cos((2 * y + 1) * v * math.pi / 16)
                    )
            spatial[y][x] = total / 4.0
    return spatial


def _decode_baseline_jpeg(data: bytes) -> list[list[list[float]]]:
    """One-component baseline JPEG to a list of 8x8 pixel blocks, strictly."""
    if data[:2] != b"\xff\xd8":
        raise _JpegError("no SOI")
    pos = 2
    quant: list[int] | None = None
    dims: tuple[int, int] | None = None
    dc_codes: dict[int, tuple[int, int]] | None = None
    ac_codes: dict[int, tuple[int, int]] | None = None
    jfif = False
    blocks: list[list[list[float]]] = []
    while pos < len(data):
        if data[pos] != 0xFF:
            raise _JpegError("no marker where a marker belongs")
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        marker = data[pos]
        pos += 1
        if marker == 0xD9:  # EOI
            if pos != len(data):
                raise _JpegError("trailing bytes after EOI")
            if quant is None or dims is None or dc_codes is None or ac_codes is None:
                raise _JpegError("stream ended before the tables it needs")
            return blocks
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # standalone markers
            continue
        if pos + 2 > len(data):
            raise _JpegError("segment header cut off")
        length = int.from_bytes(data[pos : pos + 2], "big")
        segment = data[pos + 2 : pos + length]
        pos += length
        if marker == 0xE0:
            jfif = segment[:5] == b"JFIF\x00"
        elif marker == 0xFE:  # COM: ignored, carries the fixed-size padding
            continue
        elif marker == 0xDB:
            if segment[0] != 0x00:
                raise _JpegError("only 8-bit quantization tables are required")
            quant = list(segment[1:65])
            if len(quant) != 64:
                raise _JpegError("quantization table is not 64 entries")
        elif marker == 0xC0:
            if segment[0] != 8:
                raise _JpegError("only 8-bit baseline JPEG is required")
            dims = (int.from_bytes(segment[1:3], "big"), int.from_bytes(segment[3:5], "big"))
            if segment[5] != 1 or segment[7] != 0x11 or segment[8] != 0x00:
                raise _JpegError("not a single-component, non-subsampled frame")
        elif marker == 0xC4:
            table_class = segment[0] >> 4
            bits = list(segment[1:17])
            symbols = list(segment[17:])
            codes = _codes_from_table(bits, symbols)
            if table_class == 0:
                dc_codes = codes
            else:
                ac_codes = codes
        elif marker == 0xDA:
            if dims is None or quant is None or dc_codes is None or ac_codes is None:
                raise _JpegError("scan before the tables it needs")
            if (
                segment[0] != 1
                or segment[1] != 0x01
                or segment[2] != 0x00
                or segment[3:] != b"\x00\x3f\x00"
            ):
                raise _JpegError("unexpected scan header")
            scan_start = pos
            while pos < len(data):
                if (
                    data[pos] == 0xFF
                    and data[pos + 1] not in (0x00,)
                    and not (0xD0 <= data[pos + 1] <= 0xD7)
                ):
                    break
                pos += 1
            reader = _BitReader(data[scan_start:pos])
            blocks_x = (dims[1] + 7) // 8
            blocks_y = (dims[0] + 7) // 8
            predictor = 0
            for _ in range(blocks_y):
                for _ in range(blocks_x):
                    category = _decode_huffman(reader, dc_codes)
                    diff = _extend(reader.bits(category), category) if category else 0
                    predictor += diff
                    coefficients = [0] * 64
                    coefficients[0] = predictor
                    while True:
                        symbol = _decode_huffman(reader, ac_codes)
                        run, size = symbol >> 4, symbol & 0x0F
                        if size == 0:
                            if run != 0:
                                raise _JpegError("AC symbol is not an end-of-block")
                            break
                        reader.bits(size)  # the fixture carries no AC detail
                    blocks.append(_idct(coefficients, quant))
            if not jfif:
                raise _JpegError("no JFIF APP0 segment")
        else:
            raise _JpegError("unexpected segment in a baseline stream")
    raise _JpegError("no EOI")


def _expected_blocks(tiles: list[list[int]]) -> list[tuple[int, int, int]]:
    """Tile (column, row, value) in scan order, one per 8x8 block."""
    return [(x, y, tiles[y][x]) for y in range(len(tiles)) for x in range(len(tiles[0]))]


def test_keyframes_are_decodable_jpegs_at_the_declared_dimensions(tmp_path: Path) -> None:
    document = write_plan(tmp_path / "plan.json", seed=1)
    packet = json.loads(bytes.fromhex(document["plan"]["packet_hex"]).decode("utf-8"))
    assert (packet["keyframes"][0]["w"], packet["keyframes"][0]["h"]) == (WIDTH, HEIGHT)
    declared = {entry["path"]: entry for entry in document["plan"]["files"]}
    for path in KEYFRAME_PATHS:
        content = bytes.fromhex(declared[path]["content_hex"])
        blocks = _decode_baseline_jpeg(content)
        assert len(blocks) == (WIDTH // 8) * (HEIGHT // 8)


def test_decoded_keyframe_pixels_are_the_seeded_mosaic(tmp_path: Path) -> None:
    for seed in (0, 1, 7):
        document = write_plan(tmp_path / f"plan-{seed}.json", seed=seed)
        declared = {entry["path"]: entry for entry in document["plan"]["files"]}
        tiles = keyframe_tiles(seed, KEYFRAME_PATHS[0], WIDTH, HEIGHT)
        content = bytes.fromhex(declared[KEYFRAME_PATHS[0]]["content_hex"])
        blocks = _decode_baseline_jpeg(content)
        for x, y, value in _expected_blocks(tiles):
            block = blocks[y * (WIDTH // 8) + x]
            # The IDCT returns level-shifted values (centered on 0); the
            # decoded pixel adds the standard +128 back.
            flat = [round(v) + 128 for row in block for v in row]
            assert max(flat) - min(flat) == 0, "a tile decoded with internal detail"
            assert flat[0] == value, f"tile ({x}, {y}) decoded {flat[0]}, generator wrote {value}"


def test_keyframe_jpeg_is_deterministic_and_seed_dependent() -> None:
    again = keyframe_jpeg(1, KEYFRAME_PATHS[0], WIDTH, HEIGHT)
    other = keyframe_jpeg(2, KEYFRAME_PATHS[0], WIDTH, HEIGHT)
    assert keyframe_jpeg(1, KEYFRAME_PATHS[0], WIDTH, HEIGHT) == again
    assert again != other, "a different seed must change the keyframe bytes"
    assert (
        hashlib.sha256(again).hexdigest()
        == hashlib.sha256(keyframe_jpeg(1, KEYFRAME_PATHS[0], WIDTH, HEIGHT)).hexdigest()
    )


def test_plan_keyframe_digests_bind_the_jpeg_bytes(tmp_path: Path) -> None:
    document = write_plan(tmp_path / "plan.json", seed=1)
    for i, path in enumerate(KEYFRAME_PATHS):
        entry = next(f for f in document["plan"]["files"] if f["path"] == path)
        content = bytes.fromhex(entry["content_hex"])
        assert entry["sha256"] == hashlib.sha256(content).hexdigest()
        assert content[:3] == b"\xff\xd8\xff", "a JPEG starts with SOI and a marker"
        assert content != keyframe_jpeg(
            1, KEYFRAME_PATHS[(i + 1) % len(KEYFRAME_PATHS)], WIDTH, HEIGHT
        )


def test_keyframe_byte_counts_are_seed_independent() -> None:
    """Non-stream file sizes must not depend on the seed (receipts contract)."""
    from hunter_local_ingest.fixturegen import _KEYFRAME_JPEG_BYTES

    sizes = {
        (seed, path): len(keyframe_jpeg(seed, path, WIDTH, HEIGHT))
        for seed in (0, 1, 7)
        for path in KEYFRAME_PATHS
    }
    assert set(sizes.values()) == {_KEYFRAME_JPEG_BYTES}


def test_noise_bytes_are_not_keyframes() -> None:
    """The reviewed bug stays fixed: hash-noise bytes fail the strict decoder."""
    from hunter_local_ingest.fixturegen import noise_bytes

    with pytest.raises(_JpegError):
        _decode_baseline_jpeg(noise_bytes(1, "keyframes/kf01.jpg", 1536))


def test_build_ties_keyframes_to_the_packet(tmp_path: Path) -> None:
    document = build(1, 3, 64)
    packet = json.loads(bytes.fromhex(document["plan"]["packet_hex"]).decode("utf-8"))
    assert [entry["path"] for entry in packet["files"] if entry["role"] == "keyframe"] == list(
        KEYFRAME_PATHS
    )
