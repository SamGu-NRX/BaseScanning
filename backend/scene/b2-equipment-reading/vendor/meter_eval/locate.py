"""Q4 and Q5: find the meter number among everything Vision reads on a close-up.

Candidates are the digit-bearing tokens of each recognized line plus decoded barcode
payloads. A candidate is right when its core (see `match.core`) equals the labelled number's
core, so "NO. 12345678" and "ABC 123456" count, while a longer string that merely contains
the number, such as a barcode line "*XYZ1234567890…*", does not.

The rules and the ranking were written from the odd-numbered photos only (`--split dev`)
and committed before the even-numbered photos were scored.

Writes results/locate.md and results/locate_per_image.csv; raw Vision output stays in the
data directory because it contains meter numbers.
"""

import argparse
import csv
import json
import re

from meter_eval.match import core, digest, number_read
from meter_eval.ocr import Reader
from meter_eval.paths import DATA_DIR, MANIFEST, RESULTS_DIR
from meter_eval.stats import share

# A label word in front of the number: No., Nr., №, #, S/N, Serial.
# "#:" is how Vision renders the Taiwanese label 電號: in front of the number.
KEYWORD = re.compile(r"^\s*(?:N[O0R]\.?|N°|№|#\s*:|S/?N|SERIAL\s*#?)\s*[.:#]?\s*", re.IGNORECASE)
# Lines that describe the meter rather than identify it: ratings, voltages, constants, forms.
SPEC = re.compile(
    r"\d\s*\(\s*\d+\s*\)|\d\s*V\b|HZ|\bKH\b|KH\s*\d|\bKT\b|\bTA\s*=?\s*\d|\bCL\s*\d|\d\s*CL\b|"
    r"\bFM\s*\d|FORM|TYPE|IMP|KWH|REV|U/|°C|AMP|VOLT|\bPHASE|\bWIRE|\d+/\d+|\bCA\s*\d",
    re.IGNORECASE,
)
DIGIT_GROUP = re.compile(r"^[\d.,\-]+$")


def digits(text: str) -> int:
    return sum(ch.isdigit() for ch in text)


def tokens(text: str) -> list[str]:
    """Digit-bearing tokens of one line, with a leading label word removed.

    Runs of purely numeric groups merge ("12 345 678" is one token); other groups stand
    alone ("123ABC456789"). The whole remaining line is also a token when it has at most four
    groups, so a number printed as "1 ABC00 1234 5678" survives.
    """
    rest = KEYWORD.sub("", text).strip()
    groups = rest.split()
    found, run = [], []
    for group in groups:
        if DIGIT_GROUP.match(group):
            run.append(group)
            continue
        if run:
            found.append(" ".join(run))
            run = []
        found.append(group)
    if run:
        found.append(" ".join(run))
    if 1 < len(groups) <= 4:
        found.append(rest)
    unique = list(dict.fromkeys(found))
    return [t for t in unique if digits(t) >= 4]


def payload_tokens(barcodes: list[dict]) -> set[str]:
    """Cores of the digit-bearing parts of every decoded payload."""
    parts = set()
    for barcode in barcodes:
        for part in re.split(r"[\s;*,{}]+", barcode.get("payload") or ""):
            if digits(part) >= 4:
                parts.add(core(part))
    return parts


def candidates(result: dict) -> list[dict]:
    """Every candidate with the features the rules use.

    A text token that equals a barcode payload part is confirmed. One that sits inside a
    longer payload part (at least 6 characters) is a partial read of it, as when Vision drops
    a digit or a prefix printed apart; the candidate then takes the payload's full string.
    """
    payloads = payload_tokens(result.get("barcodes") or [])
    found = []
    for line in result["lines"]:
        text = line["text"]
        _, _, w, h = line["box"]
        for token in tokens(text):
            token_core = core(token)
            confirmed = token_core in payloads
            if not confirmed and len(token_core) >= 6:
                longer = [p for p in payloads if token_core in p]
                if longer:
                    token_core, confirmed = min(longer, key=len), True
            found.append(
                {
                    "core": token_core,
                    "box": line["box"],
                    "barcode_confirmed": confirmed,
                    "keyword": bool(KEYWORD.match(text))
                    and not re.match(r"\s*CAT", text, re.IGNORECASE),
                    "alone": core(KEYWORD.sub("", text)) == token_core,
                    "spec": bool(SPEC.search(text)),
                    "vertical": h > w,
                    "zeros": set(token_core) <= {"0"},
                    "length_ok": 6 <= len(token_core) <= 14 and digits(token_core) >= 5,
                    "height": h,
                }
            )
    return found


def near_barcode(candidate: dict, barcodes: list[dict]) -> bool:
    """The candidate's line sits within 1.5 line heights above or below a barcode it overlaps."""
    x, y, w, h = candidate["box"]
    for b in barcodes:
        bx, by, bw, bh = b["box"]
        overlaps = min(x + w, bx + bw) - max(x, bx) > 0.3 * min(w, bw)
        gap = max(by - (y + h), y - (by + bh))
        if overlaps and gap <= 1.5 * h:
            return True
    return False


def score(c: dict) -> float:
    """Hand-set ranking score, chosen on the odd-numbered photos.

    Breaking ties toward taller print beat breaking them toward lower lines or not at all
    (top-1 27 vs 26 vs 23 of 38 there).
    """
    return (
        8 * c["barcode_confirmed"]
        + 3 * c["keyword"]
        + 2 * c["alone"]
        + 1 * c["length_ok"]
        - 6 * c["spec"]
        - 4 * c["vertical"]
        - 6 * c["zeros"]
        + c["height"]  # tie-break toward larger print
    )


def top_candidate(result: dict) -> dict | None:
    """The phone's best guess at the number's line: the highest-scoring candidate."""
    return best(candidates(result))


def ranked(found: list[dict]) -> list[str]:
    """Candidate cores, best first, each core once."""
    order = sorted(found, key=score, reverse=True)
    return list(dict.fromkeys(c["core"] for c in order))


def tallest(pool: list[dict]) -> dict | None:
    return max(pool, key=lambda c: c["height"], default=None)


def best(pool: list[dict]) -> dict | None:
    return max(pool, key=score, default=None)


def rules(found: list[dict], barcodes: list[dict]) -> dict[str, dict | None]:
    """Each rule's pick, or None when the rule does not fire."""
    decoded = [b for b in barcodes if b.get("payload")]
    plausible = [c for c in found if not c["spec"] and not c["vertical"] and not c["zeros"]]
    confirmed = best([c for c in found if c["barcode_confirmed"]])
    return {
        "tallest digit line (phase 1)": tallest(found),
        "barcode confirms a text line": confirmed,
        "line nearest a decoded barcode": tallest(
            [c for c in plausible if near_barcode(c, decoded)]
        ),
        "after a No./Nr./#: keyword": tallest([c for c in plausible if c["keyword"]]),
        "alone on its line, 6-14 characters, not a spec line": tallest(
            [c for c in plausible if c["alone"] and c["length_ok"]]
        ),
        "combined: barcode, else keyword": confirmed
        or tallest([c for c in plausible if c["keyword"] and c["length_ok"]]),
    }


def scan(rows: list[dict]) -> dict[str, dict]:
    raw_path = DATA_DIR / "ocr" / "scan.jsonl"
    results = {}
    with Reader() as reader, raw_path.open("w") as raw:
        for row in rows:
            result = reader.read(DATA_DIR / "images" / f"{row['id']}.jpg", barcodes=True)
            raw.write(json.dumps(result) + "\n")
            results[row["id"]] = result
    return results


def barcode_stats(row: dict, result: dict) -> dict:
    barcodes = result.get("barcodes") or []
    payloads = [b["payload"] for b in barcodes if b.get("payload")]
    target = row["number_core_hmac"]
    length = int(row["number_core_len"] or 0)
    has_number = any(
        digest(core(p)[i : i + length]) == target
        for p in payloads
        for i in range(len(core(p)) - length + 1)
    )
    return {
        "barcode_found": int(bool(barcodes)),
        "barcode_decoded": int(bool(payloads)),
        "barcode_has_number": int(has_number) if row["number_hmac"] else "",
    }


def evaluate(row: dict, result: dict) -> dict:
    out = {
        "id": row["id"],
        "split": "dev" if int(row["id"][1:]) % 2 else "test",
        "us_style": int(row["class_kind"] == "ansi_class"),
        "strict": int(row["number_agreed_strict"] == "yes"),
    }
    out |= barcode_stats(row, result)
    if row["number_agreed"] != "yes":
        return out
    out["read"] = int(
        number_read(result["lines"], row["number_hmac"], int(row["number_len"]), lenient=False)
    )
    found = candidates(result)
    target = row["number_core_hmac"]
    order = ranked(found)
    hits = [i for i, c in enumerate(order) if digest(c) == target]
    out["rank"] = hits[0] + 1 if hits else ""
    out["candidates"] = len(order)
    for name, pick in rules(found, result.get("barcodes") or []).items():
        out[name] = "" if pick is None else int(digest(pick["core"]) == target)
    return out


def barcode_table(rows: list[dict]) -> str:
    out = [
        "| Photos | Barcode found | Decoded | Decode contains the meter number |",
        "|---|---|---|---|",
    ]
    for title, subset in (
        ("All usable", rows),
        ("US-style (CL class label)", [r for r in rows if r["us_style"]]),
    ):
        found = [r for r in subset if r["barcode_found"]]
        decoded = [r for r in found if r["barcode_decoded"]]
        with_number = [r for r in found if r["barcode_has_number"] != ""]
        contains = [r for r in with_number if r["barcode_has_number"]]
        out.append(
            f"| {title}: {len(subset)} | {len(found)} | {len(decoded)} | "
            f"{len(contains)} of {len(with_number)} with a labelled number |"
        )
    return "\n".join(out) + "\n"


def rule_table(rows: list[dict], names: list[str]) -> str:
    out = []
    groups = {
        "odd-numbered photos, used to write the rules": lambda r: r["split"] == "dev",
        "even-numbered photos, held out": lambda r: r["split"] == "test",
        "all photos": lambda r: True,
        "all photos, strict labels (readers' main numbers identical)": lambda r: r["strict"],
        # Exploratory: chosen after the held-out scoring, as a description of Base's market.
        "exploratory: US-style meters (CL class label), odd-numbered": lambda r: (
            r["us_style"] and r["split"] == "dev"
        ),
        "exploratory: US-style meters (CL class label), held out": lambda r: (
            r["us_style"] and r["split"] == "test"
        ),
    }
    if {r["split"] for r in rows} == {"dev"}:
        groups = {"odd-numbered photos, used to write the rules": groups["all photos"]}
    for title, keep in groups.items():
        subset = [r for r in rows if "read" in r and keep(r)]
        read = [r for r in subset if r["read"]]
        out.append(f"**{title}: {len(subset)} with an agreed number, {len(read)} read**\n")
        out.append("| Rule | Picks | Precision | Recall over read photos |")
        out.append("|---|---|---|---|")
        for name in names:
            fired = [r for r in read if r[name] != ""]
            right = sum(r[name] for r in fired)
            out.append(
                f"| {name} | {len(fired)} | {share(right, len(fired))} | "
                f"{share(right, len(read))} |"
            )
        for k in (1, 3):
            hit = sum(1 for r in read if r["rank"] != "" and r["rank"] <= k)
            out.append(f"| ranking, top {k} | {len(read)} | – | {share(hit, len(read))} |")
        present = sum(1 for r in read if r["rank"] != "")
        out.append(
            f"| number is any candidate (ceiling) | {len(read)} | – | "
            f"{share(present, len(read))} |\n"
        )
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["dev", "all"], default="all")
    args = parser.parse_args()
    with MANIFEST.open() as handle:
        manifest = [r for r in csv.DictReader(handle) if r["usable"] == "yes"]
    if args.split == "dev":
        manifest = [r for r in manifest if int(r["id"][1:]) % 2]
    results = scan(manifest)
    rows = [evaluate(row, results[row["id"]]) for row in manifest]
    names = list(rules([], []))  # rule names, in table order
    text = "\n".join(
        [
            "### Barcodes\n",
            barcode_table(rows),
            "### Finding the number\n",
            rule_table(rows, names),
        ]
    )
    suffix = "" if args.split == "all" else "_dev"
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("id", "split"), k))
    with (RESULTS_DIR / f"locate_per_image{suffix}.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)
    (RESULTS_DIR / f"locate{suffix}.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
