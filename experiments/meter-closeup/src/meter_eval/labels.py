"""Merge the two readers' labels into the committed manifest.csv.

Both readers are AI models that read each photo by eye (reader 1 is the experiment author,
reader 2 an independent pass that never saw reader 1's labels). The label is always reader
1's number, so reader 1 chose which printed identifier counts as the meter number. Two
agreement rules are recorded:

- number_agreed (the loose rule the headline results use): reader 1's number, normalized,
  equals any number reader 2 listed (its main number or one of its other numbers), or one
  contains the other and the shorter has at least 6 characters (a barcode line such as
  ACG012345678 printing the plate number 12 345 678). Neither reader may have doubted a
  character.
- number_agreed_strict: both readers' main numbers are identical after normalization and
  both readers were sure.

A person's answers from the review page (`review.py`), when present: "keep" makes a number
agreed under both rules; "fix" replaces the number with the person's reading but does not
by itself make it agreed.

Plaintext labels stay in the data directory. The manifest keeps keyed digests (match.py),
and identifier_digests.txt keeps the digest of every identifier either reader transcribed,
so `leakcheck` can find one in the repository without the plaintext.
"""

import argparse
import csv
import re
import secrets

from meter_eval.match import core, digest, lenient_digest, normalize, normalize_class, number_core
from meter_eval.paths import DATA_DIR, EXPERIMENT_DIR, KEY_PATH, MANIFEST

READER1 = DATA_DIR / "labels_reader1.csv"
READER2 = [DATA_DIR / "labels_reader2a.csv", DATA_DIR / "labels_reader2b.csv"]
HUMAN = DATA_DIR / "labels_human.csv"
SOURCES = DATA_DIR / "shortlist.csv"
IDENTIFIER_DIGESTS = EXPERIMENT_DIR / "identifier_digests.txt"
# The shortest identifier either reader transcribed has four digits. The leak check digests every
# window of SHORTEST to LONGEST letters and digits holding that many digits; scrub() uses the same.
SHORTEST_IDENTIFIER, LONGEST_IDENTIFIER = 4, 24
# Reader 2 marked these "unsure" for a reason other than the characters of reader 1's number,
# per its notes: which of two printed numbers is the meter's ID (m25, m30, m33, m38), or a
# second number cut off by the frame (m63). It transcribed reader 1's number identically.
DOUBT_NOT_ABOUT_CHARACTERS = {"m25", "m30", "m33", "m38", "m63"}

FIELDS = [
    "id",
    "title",
    "page_url",
    "image_url",
    "license",
    "license_url",
    "author",
    "usable",
    "number_hmac",
    "number_hmac_lenient",
    "number_len",
    "number_core_hmac",
    "number_core_len",
    "number_agreed",
    "number_agreed_strict",
    "human_check",
    "class_label",
    "class_kind",
    "class_agreed",
    "notes",
]


def read_csv(path) -> dict[str, dict]:
    with path.open() as handle:
        return {row["id"]: row for row in csv.DictReader(handle)}


def numbers_of(row: dict) -> set[str]:
    values = [row["meter_number"], *row.get("other_numbers", "").split(";")]
    # Reader 2 annotated some numbers in parentheses, e.g. "12345678 (FlexNet)".
    values = [re.sub(r"\(.*?\)", "", v) for v in values]
    return {normalize(v) for v in values if v.strip() and v not in ("NONE", "EXCLUDE")}


def same_number(a: str, b: str) -> bool:
    short, long = sorted((a, b), key=len)
    return a == b or (len(short) >= 6 and short in long)


def class_kind(label: str) -> str:
    if not label or label == "NONE":
        return ""
    return "ansi_class" if "CL" in label.upper() else "current_rating"


def scrub(note: str) -> str:
    """Replace digits in free-text notes until the leak check could find no identifier there.

    Uses the leak check's own view of text: normalize() drops every character but A-Z and 0-9,
    so "12/345", "12:345" and "12 A 34" all read as runs. Any digit inside a window of up to
    LONGEST_IDENTIFIER normalized characters holding SHORTEST_IDENTIFIER or more digits becomes
    "#". Dropping digits pulls the rest closer together, so this repeats until no window is left.
    """
    chars = list(note)
    while True:
        # (position in the note, normalized character) for every character normalize() keeps.
        kept = [(i, n) for i, c in enumerate(chars) for n in normalize(c)]
        digits = [0]
        for _, n in kept:
            digits.append(digits[-1] + n.isdigit())
        marked = set()
        for start in range(len(kept)):
            end = min(start + LONGEST_IDENTIFIER, len(kept))
            if digits[end] - digits[start] >= SHORTEST_IDENTIFIER:
                marked |= {i for i, n in kept[start:end] if n.isdigit()}
        if not marked:
            return "".join(chars)
        for i in marked:
            chars[i] = "#"


def build() -> list[dict]:
    first, sources = read_csv(READER1), read_csv(SOURCES)
    second = {k: v for path in READER2 for k, v in read_csv(path).items()}
    human = read_csv(HUMAN) if HUMAN.exists() else {}
    rows = []
    for image_id, r1 in first.items():
        r2 = second[image_id]
        source = sources[image_id]
        number = r1["meter_number"]
        usable = "no: " + scrub(r1["notes"]) if number == "EXCLUDE" else "yes"
        row = {key: source[key] for key in ("title", "page_url", "image_url", "license")}
        row |= {
            "id": image_id,
            "license_url": source["license_url"],
            "author": " ".join(source["author"].split())[:120],
            "usable": usable,
            "notes": scrub(r1["notes"]),
        }
        verdict = human.get(image_id, {}).get("verdict", "")
        # A person's number replaces the readers': typed with "fix", or confirmed with "keep"
        # after a fix. A "keep" with no number confirms the readers' own number.
        if verdict in ("fix", "keep") and human[image_id].get("number"):
            number = human[image_id]["number"]
        if number not in ("EXCLUDE", "NONE"):
            r2_sure = r2["number_sure"] == "sure" or image_id in DOUBT_NOT_ABOUT_CHARACTERS
            agreed = (
                any(same_number(normalize(number), other) for other in numbers_of(r2))
                and r1["number_sure"] == "sure"
                and r2_sure
            )
            strict = (
                normalize(number) == normalize(r2["meter_number"])
                and r1["number_sure"] == "sure"
                and r2["number_sure"] == "sure"
            )
            kept = verdict == "keep"
            row |= {
                "number_hmac": digest(normalize(number)),
                "number_hmac_lenient": lenient_digest(number),
                "number_len": len(normalize(number)),
                "number_core_hmac": digest(number_core(number)),
                "number_core_len": len(number_core(number)),
                "number_agreed": "yes" if (agreed and verdict != "fix") or kept else "no",
                "number_agreed_strict": "yes" if (strict and verdict != "fix") or kept else "no",
                "human_check": verdict,
            }
        label = r1["class_label"]
        if usable == "yes" and label and label != "NONE":
            agreed = (
                normalize_class(label) == normalize_class(r2["class_label"])
                and r1["class_sure"] == "sure"
                and r2["class_sure"] == "sure"
            )
            row |= {
                "class_label": label,
                "class_kind": class_kind(label),
                "class_agreed": "yes" if agreed else "no",
            }
        rows.append(row)
    return rows


def known_numbers() -> set[str]:
    """Every identifier any reader transcribed, normalized, with and without its prefix.

    Covers both AI readers and a person's corrections from the review page, because a
    correction becomes a committed label that the leak check must also recognize.
    """
    first = read_csv(READER1)
    second = {k: v for path in READER2 for k, v in read_csv(path).items()}
    human = read_csv(HUMAN) if HUMAN.exists() else {}
    corrections = [
        {"meter_number": row["number"]}
        for row in human.values()
        if row.get("verdict") in ("fix", "keep") and row.get("number")
    ]
    found = set()
    for row in [*first.values(), *second.values(), *corrections]:
        for value in numbers_of(row):
            for form in (value, core(value)):
                if sum(ch.isdigit() for ch in form) >= SHORTEST_IDENTIFIER:
                    found.add(form)
    return found


def new_key() -> None:
    if KEY_PATH.exists():
        raise SystemExit(
            f"{KEY_PATH} exists. Every digest in manifest.csv depends on it; delete it by hand "
            "only if you mean to re-key, then rebuild the manifest."
        )
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_text(secrets.token_hex(32) + "\n")
    KEY_PATH.chmod(0o600)
    print(f"wrote a new key to {KEY_PATH}; now run `python -m meter_eval.labels`")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-key", action="store_true", help="create the HMAC key instead")
    if parser.parse_args().new_key:
        new_key()
        return
    rows = build()
    IDENTIFIER_DIGESTS.write_text(
        "".join(f"{d}\n" for d in sorted({digest(n) for n in known_numbers()}))
    )
    with MANIFEST.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, restval="")
        writer.writeheader()
        writer.writerows(rows)
    usable = [r for r in rows if r["usable"] == "yes"]
    agreed = [r for r in usable if r.get("number_agreed") == "yes"]
    strict = [r for r in usable if r.get("number_agreed_strict") == "yes"]
    print(
        f"{len(rows)} images, {len(usable)} usable, {len(agreed)} with an agreed meter number "
        f"({len(strict)} under the strict rule)"
    )


if __name__ == "__main__":
    main()
