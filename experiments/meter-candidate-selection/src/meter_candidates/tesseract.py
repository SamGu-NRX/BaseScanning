"""The Tesseract arm: the one OCR engine this Linux sandbox can really run.

Reads a frozen synthetic plate with the Tesseract CLI and reshapes its word TSV into the
experiment's reader shape ({lines: [{text, box}], barcodes: []}). This is real OCR output
on synthetic input — committed as raw evidence, never substituted by a mock. Apple Vision
through meterocr stays not-run (tables.METEROCR_NOT_RUN).
"""

import shutil
import subprocess

BARCODES: list = []


def available() -> tuple[str, str] | None:
    """(path, version) when the CLI exists, else None."""
    path = shutil.which("tesseract")
    if not path:
        return None
    out = subprocess.run([path, "--version"], capture_output=True, text=True, check=True)
    version = out.stdout.splitlines()[0].strip()
    return path, version


def read_image(binary: str, version: str, image: str, psm: str = "6") -> dict:
    """One plate's lines in reader shape, from Tesseract's word TSV."""
    out = subprocess.run(
        [binary, image, "stdout", "--psm", psm, "tsv"],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = [line.split("\t") for line in out.stdout.splitlines() if line.strip()]
    header = rows[0]
    columns = {name: i for i, name in enumerate(header)}
    grouped: dict[tuple, list] = {}
    for row in rows[1:]:
        if row[columns["level"]] != "5":  # word level only
            continue
        key = (
            row[columns["page_num"]],
            row[columns["block_num"]],
            row[columns["par_num"]],
            row[columns["line_num"]],
        )
        grouped.setdefault(key, []).append(row)
    lines = []
    for words in grouped.values():
        left = min(int(row[columns["left"]]) for row in words)
        top = min(int(row[columns["top"]]) for row in words)
        right = max(int(row[columns["left"]]) + int(row[columns["width"]]) for row in words)
        bottom = max(int(row[columns["top"]]) + int(row[columns["height"]]) for row in words)
        lines.append(
            {
                "text": " ".join(row[columns["text"]] for row in words),
                "box": [left, top, right - left, bottom - top],
            }
        )
    return {
        "lines": lines,
        "barcodes": [dict(b) for b in BARCODES],
        "reader": {"name": "tesseract", "version": version, "psm": psm},
    }
