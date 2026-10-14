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
# shorter forms. Recognised prefixes split off even when attached to the serial.
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
# Label wrappers are explicit so format characters cannot disappear around a label.
_LABEL_SEPARATOR = r"[\s().:/#-]"
_LABEL_PATTERN = "|".join(
    (_LABEL_SEPARATOR + "+").join(r"(?ai:" + re.escape(word) + ")" for word in label.split())
    for label in NUMBER_LABELS
)
_LABEL_PREFIX = re.compile(r"(?:" + _LABEL_PATTERN + r")")
KEYWORD = re.compile(rf"^{_LABEL_SEPARATOR}*(?:" + _LABEL_PATTERN + rf"){_LABEL_SEPARATOR}*")


_SERIAL_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
# Only these punctuation marks and Unicode whitespace split a serial group.
_SERIAL_SEPARATORS = "-/.:"
_SERIAL_GROUP = re.compile(r"[^\s" + re.escape(_SERIAL_SEPARATORS) + r"]+")


def serial_text(text: str) -> str:
    """Map only ASCII and fullwidth ASCII letters/digits, preserving invalid groups."""
    mapped = []
    for character in text:
        code = ord(character)
        if 0xFF10 <= code <= 0xFF19 or 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:
            character = chr(code - 0xFEE0)
        if "a" <= character <= "z":
            character = chr(ord(character) - 32)
        mapped.append(character)
    return "".join(mapped)


def read_target(text: str) -> str | None:
    """Label-stripped serial text, or None for any unsupported group character."""
    text = serial_text(text)
    text = KEYWORD.sub("", text, count=1)
    if any(
        character not in _SERIAL_ALPHABET
        for group in _SERIAL_GROUP.findall(text)
        for character in group
    ):
        return None
    return "".join(character for character in text if character in _SERIAL_ALPHABET).lower()


def number_core(text: str) -> str | None:
    target = read_target(text)
    return target.upper() if target is not None else None


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
    "classes": (r"CL\s*[0-9]{1,3}", r"FM\s*[0-9]{1,3}[A-Z]?", "2S", "3W"),
}


# Ratings are short because voltage/current/frequency/Kh/class values have at most
# four digits; longer digit-bearing strings remain possible serials.
_SMALL_RATING_NUMBER = (
    r"(?:[0-9]{1,3}\.[0-9]|[0-9]{1,2}\.[0-9]{2}|[0-9]\.[0-9]{3}|\.[0-9]{1,4}|[0-9]{1,4})"
)
_RATING = re.compile(
    r"(?<!\w)(?<![0-9]\.)(?:"
    + _SMALL_RATING_NUMBER
    + r"\s*(?:"
    + "|".join(sorted(SERIAL_RATINGS["units"], key=len, reverse=True))
    + r")|KH\s*"
    + _SMALL_RATING_NUMBER
    + "|"
    + "|".join(SERIAL_RATINGS["classes"])
    + r")(?!\w)(?!\.[0-9])"
)


def match_serial(
    lines: Sequence[dict], target: str | None = None, *, target_digest: str | None = None
) -> list[dict]:
    """Admissible row candidates, exact target matches first, with source and union boxes.

    Labels and ratings split runs. Numeric neighbours disallow a partial number;
    alphabetic neighbours permit a serial inside surrounding printed words.
    A separately printed label word always splits a run, so AB SN 12345 cannot
    represent ABSN12345; the same serial printed as one group is supported.
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
    if target is not None and wanted is None:
        return []
    found = []
    for row_id, row in enumerate(rows):
        row_box = union_box([piece["box"] for piece in row])
        # No Unicode normalization/case folding or O/0, I/1, Cyrillic/Latin mapping.
        ordered = sorted(row, key=lambda piece: piece["box"][0])
        normalized = []
        owners = []
        for piece in ordered:
            text = serial_text(piece["text"])
            if normalized:
                owners.append(None)
            normalized.append(text)
            owners.extend([piece["box"]] * len(text))
        row_text = " ".join(normalized)
        masked = list(row_text)
        boundary_spans = []
        label_end = 0
        for token in _SERIAL_GROUP.finditer(row_text):
            if token.start() < label_end:
                continue
            label = _LABEL_PREFIX.match(row_text, token.start())
            if label:
                label_end = label.end()
                boundary_spans.append((label.start(), label.end()))
                masked[label.start() : label.end()] = [" "] * (label.end() - label.start())
        labelled_text = "".join(masked)
        for rating in _RATING.finditer(labelled_text):
            # Never extract a rating from inside a group containing an unsupported character.
            neighbours = (
                labelled_text[rating.start() - 1 : rating.start()] if rating.start() else ""
            )
            neighbours += labelled_text[rating.end() : rating.end() + 1]
            if any(
                not character.isspace() and character not in _SERIAL_SEPARATORS
                for character in neighbours
            ):
                continue
            boundary_spans.append((rating.start(), rating.end()))
            masked[rating.start() : rating.end()] = [" "] * (rating.end() - rating.start())
        # Explicit spans keep attached labels separate without deleting any suffix characters.
        spans = [(start, end, "", True) for start, end in boundary_spans]
        spans.extend(
            (token.start(), token.end(), token.group(), False)
            for token in _SERIAL_GROUP.finditer("".join(masked))
        )
        groups = []
        kinds = []
        for start, end, word, boundary in sorted(spans):
            boxes = []
            for box in owners[start:end]:
                if box is not None and box not in boxes:
                    boxes.append(box)
            groups.append((word, boxes))
            if boundary:
                kind = "boundary"
            elif any(character not in _SERIAL_ALPHABET for character in word):
                kind = "invalid"
            elif word.isdigit():
                kind = "numeric"
            else:
                kind = "text"
            kinds.append(kind)
        words = [word for word, _ in groups]
        for start in range(len(words)):
            if kinds[start] in ("boundary", "invalid") or (
                start and kinds[start - 1] in ("numeric", "invalid")
            ):
                continue
            joined = ""
            boxes = []
            for end in range(start, len(words)):
                if kinds[end] in ("boundary", "invalid"):
                    break
                joined += words[end]
                for box in groups[end][1]:
                    if box not in boxes:
                        boxes.append(box)
                if end + 1 < len(words) and kinds[end + 1] in ("numeric", "invalid"):
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
