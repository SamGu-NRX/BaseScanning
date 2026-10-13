"""Decide whether recognizer output contains a labelled string.

Meter numbers are compared through an HMAC-SHA256 digest of their normalized form, because
CONTRIBUTING.md forbids committing meter numbers. An unkeyed hash of a short number can be
reversed by trying every number of that length, so the digest is keyed: without the key in
KEY_PATH (or the METER_HMAC_KEY environment variable, as in CI) the committed digests do not
reveal the numbers, and scoring a read needs the key.
"""

import functools
import hashlib
import hmac
import itertools
import os
import re
from collections.abc import Sequence

from meter_eval.paths import KEY_PATH

# Lenient mode maps letters Vision commonly returns for digits. Applied to both the label and
# the output, so it can only add matches.
DIGIT_FIXUPS = str.maketrans({"O": "0", "I": "1", "L": "1"})


def normalize(text: str) -> str:
    """Uppercase and keep only A-Z and 0-9."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def normalize_class(text: str) -> str:
    """Uppercase, keep A-Z, 0-9 and parentheses, and read × as X.

    Separators are dropped because meters print them inconsistently and Vision returns them
    inconsistently ("0.1-10" read as "0.1,-10", "20(60)A" as "20(60)/A"); parentheses stay
    so 10(60)A and 1060A remain different. This rule was loosened after inspecting the first
    run's misses, which were all separator or × differences.
    """
    return re.sub(r"[^A-Z0-9()]", "", text.upper().replace("×", "X"))


def core(text: str) -> str:
    """The normalized text without a leading run of letters before a digit.

    "NO. 12345678", "ABC 123456" and "XYZW123456" become 12345678, 123456 and 123456, so a
    utility prefix or a label word printed apart from the number does not decide a match.
    """
    return re.sub(r"^[A-Z]+(?=\d)", "", normalize(text))


@functools.cache
def key() -> bytes:
    if os.environ.get("METER_HMAC_KEY"):
        return bytes.fromhex(os.environ["METER_HMAC_KEY"])
    if not KEY_PATH.exists():
        raise FileNotFoundError(
            f"No HMAC key: METER_HMAC_KEY is unset (in CI, the repository secret of that name) "
            f"and {KEY_PATH} does not exist. Ask the team for the key, or start over with "
            "`python -m meter_eval.labels --new-key` and rebuild the manifest from the "
            "plaintext labels."
        )
    return bytes.fromhex(KEY_PATH.read_text().strip())


def digest(normalized: str) -> str:
    return hmac.new(key(), normalized.encode(), hashlib.sha256).hexdigest()


# Boxes in one row join only across a horizontal gap of at most this many character widths
# (a box's width over its non-space characters, averaged over the two boxes). It is measured in
# the image's own x units, so it needs no aspect ratio. On the cached Vision reads of the 75
# labelled photos (clean and locate runs, every configuration), a limit of 2 to 6 widths leaves
# every number match as it was and 1.5 loses three; 3 keeps a margin. The limit does split
# unrelated fields there (a model code from a serial on the same row), but no read depended on
# that join, so these photos show no false read it prevents.
MAX_JOIN_GAP_CHARS = 3.0


def _char_width(line: dict) -> float:
    return line["box"][2] / max(1, len(line["text"].replace(" ", "")))


def rows_of_text(lines: Sequence[dict]) -> list[tuple[str, list[list[float]]]]:
    """Each line's (text, [box]), plus neighbouring lines in one row joined left to right.

    Vision sometimes splits one printed number into two observations ("123 45" and "678").
    Two boxes share a row when their vertical overlap is at least half the shorter box, and
    they join only when the gap between them is at most MAX_JOIN_GAP_CHARS character widths,
    so digits from an unrelated field further along the same row are not read as part of the
    number.
    """
    texts = [(line["text"], [line["box"]]) for line in lines]
    rows: list[list[dict]] = []
    for line in sorted(lines, key=lambda item: item["box"][1]):
        _, y, _, h = line["box"]
        for row in rows:
            _, ry, _, rh = row[0]["box"]
            overlap = min(y + h, ry + rh) - max(y, ry)
            if overlap >= 0.5 * min(h, rh):
                row.append(line)
                break
        else:
            rows.append([line])
    for row in rows:
        ordered = sorted(row, key=lambda item: item["box"][0])
        chains = [[ordered[0]]]
        for left, right in itertools.pairwise(ordered):
            gap = right["box"][0] - (left["box"][0] + left["box"][2])
            if gap <= MAX_JOIN_GAP_CHARS * (_char_width(left) + _char_width(right)) / 2:
                chains[-1].append(right)
            else:
                chains.append([right])
        for chain in chains:
            if len(chain) > 1:
                texts.append((" ".join(i["text"] for i in chain), [i["box"] for i in chain]))
    return texts


def number_boxes(
    lines: Sequence[dict], target_digest: str, length: int, lenient: bool
) -> list[list[float]] | None:
    """Boxes of the first line or joined row containing the labelled number, else None."""
    for text, boxes in rows_of_text(lines):
        candidate = normalize(text)
        if lenient:
            candidate = candidate.translate(DIGIT_FIXUPS)
        for start in range(len(candidate) - length + 1):
            if digest(candidate[start : start + length]) == target_digest:
                return boxes
    return None


def number_read(lines: Sequence[dict], target_digest: str, length: int, lenient: bool) -> bool:
    return number_boxes(lines, target_digest, length, lenient) is not None


def class_read(lines: Sequence[dict], label: str) -> bool:
    target = normalize_class(label)
    return any(target in normalize_class(text) for text, _ in rows_of_text(lines))


def lenient_digest(number: str) -> str:
    return digest(normalize(number).translate(DIGIT_FIXUPS))
