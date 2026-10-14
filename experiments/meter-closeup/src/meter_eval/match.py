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
import os
import re
from collections.abc import Sequence

from meter_eval.paths import KEY_PATH
from meter_eval.quality import union_box

# Lenient mode maps letters Vision commonly returns for digits. Applied to both the label and
# the output, so it can only add matches.
DIGIT_FIXUPS = str.maketrans({"O": "0", "I": "1", "L": "1"})

# Printed labels shared by reading and candidate ranking. Compound labels precede
# shorter forms; a label boundary keeps serial letters in ABC1234567 or NO1234567.
NUMBER_LABELS = (
    "Serial No",
    "Meter No",
    "Meter #",
    "S/N",
    "Serial",
    "Meter",
    "SN",
    "No",
    "N0",
    "Nr",
    "N°",
    "№",
    "#",
)
KEYWORD = re.compile(
    r"^[\W_]*(?:"
    + "|".join(
        re.escape(label).replace(r"\ ", r"[\W_]+") + (r"(?=$|[\W_])" if label[-1].isalnum() else "")
        for label in NUMBER_LABELS
    )
    + r")[\W_]*",
    re.IGNORECASE,
)


def read_target(text: str) -> str:
    return "".join(re.findall(r"[^\W_]+", KEYWORD.sub("", text, count=1))).casefold()


def number_core(text: str) -> str:
    return read_target(text).upper()


# Ratings are boundaries, never serial groups: plate units and class/form prefixes.
SERIAL_RATINGS = {
    "units": (
        "V",
        "VAC",
        "A",
        "AMP",
        "AMPS",
        "HZ",
        "W",
        "KW",
        "KWH",
        "VA",
        "KH",
        "TA",
        "PH",
        "WIRE",
    ),
    "classes": ("CL", "FM", "2S", "3W"),
}


def match_serial(
    lines: Sequence[dict], target: str | None = None, *, target_digest: str | None = None
) -> list[dict]:
    """Admissible row candidates, exact target matches first, with source and union boxes.

    Labels and ratings split runs. Numeric neighbours disallow a partial number;
    alphabetic neighbours permit a serial inside surrounding printed words.
    """
    rows = []
    for line in sorted(lines, key=lambda item: item["box"][1]):
        _, y, _, h = line["box"]
        for row in rows:
            _, ry, _, rh = row[0]["box"]
            if min(y + h, ry + rh) - max(y, ry) >= 0.5 * min(h, rh):
                row.append(line)
                break
        else:
            rows.append([line])
    wanted = number_core(target) if target is not None else None
    found = []
    label_groups = [
        tuple(re.findall(r"N°|№|#|[A-Z0-9]+", label.upper())) for label in NUMBER_LABELS
    ]
    units = set(SERIAL_RATINGS["units"])
    classes = set(SERIAL_RATINGS["classes"])
    for row_id, row in enumerate(rows):
        row_text = " ".join(piece["text"] for piece in sorted(row, key=lambda p: p["box"][0]))
        row_box = union_box([piece["box"] for piece in row])
        groups = [
            (token.group(), piece["box"])
            for piece in sorted(row, key=lambda p: p["box"][0])
            for token in re.finditer(r"N°|№|#|[A-Z0-9]+", piece["text"].upper())
        ]
        words = [word for word, _ in groups]
        kinds = ["numeric" if word.isdigit() else "text" for word in words]
        i = 0
        while i < len(words):
            label = next(
                (parts for parts in label_groups if tuple(words[i : i + len(parts)]) == parts), None
            )
            if label:
                kinds[i : i + len(label)] = ["boundary"] * len(label)
                i += len(label)
            else:
                i += 1
        for i, word in enumerate(words):
            if kinds[i] == "boundary":
                continue
            attached = re.fullmatch(r"([0-9]+)([A-Z]+)", word)
            if (
                word in units
                or word in classes
                or re.fullmatch(r"CL[0-9]+", word)
                or (attached and attached[2] in units)
            ):
                kinds[i] = "boundary"
                if word in units and i and kinds[i - 1] == "numeric":
                    kinds[i - 1] = "boundary"
                if word in ("CL", "FM") and i + 1 < len(words) and kinds[i + 1] == "numeric":
                    kinds[i + 1] = "boundary"
        for start in range(len(words)):
            if kinds[start] == "boundary" or (start and kinds[start - 1] == "numeric"):
                continue
            joined = ""
            boxes = []
            for end in range(start, len(words)):
                if kinds[end] == "boundary":
                    break
                joined += words[end]
                if groups[end][1] not in boxes:
                    boxes.append(groups[end][1])
                if end + 1 < len(words) and kinds[end + 1] == "numeric":
                    continue
                # Alphabetic fragments can join a serial; a maker word alone is not one.
                if not any(character.isdigit() for character in joined):
                    continue
                found.append(
                    {
                        "core": joined,
                        "boxes": boxes.copy(),
                        "box": union_box(boxes),
                        "row_id": row_id,
                        "row_box": row_box,
                        "keyword": bool(KEYWORD.match(row_text)),
                        "text": row_text,
                        "matches_target": joined == wanted
                        if wanted is not None
                        else target_digest is not None and digest(joined) == target_digest,
                    }
                )
    return sorted(found, key=lambda candidate: candidate["matches_target"], reverse=True)


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


def rows_of_text(lines: Sequence[dict]) -> list[tuple[str, list[list[float]]]]:
    """Each line's (text, [box]), plus lines sharing a row joined left to right.

    Vision sometimes splits one printed number into two observations ("123 45" and "678").
    Two boxes share a row when their vertical overlap is at least half the shorter box.
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
        if len(row) > 1:
            ordered = sorted(row, key=lambda item: item["box"][0])
            texts.append((" ".join(item["text"] for item in ordered), [i["box"] for i in ordered]))
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
    if not target:
        return False
    # Keep token boundaries before dropping separators: CL20 must not match CL200.
    pattern = r"(?<![A-Z0-9])" + r"[^A-Z0-9()]*".join(re.escape(c) for c in target)
    pattern += r"(?![A-Z0-9])"
    return any(
        re.search(pattern, text.upper().replace("×", "X")) is not None
        for text, _ in rows_of_text(lines)
    )


def lenient_digest(number: str) -> str:
    return digest(normalize(number).translate(DIGIT_FIXUPS))
