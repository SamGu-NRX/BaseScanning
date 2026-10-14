"""Score meter-brand reading on the #16 meter photos, from Vision's recognised lines.

Inputs, all outside git:
  <data>/ocr/clean.jsonl        Vision lines per photo and configuration, written by #16's meterocr
  <data>/brand_reader_a.csv     brand labels from an independent reader
  --manifest                    #16's manifest.csv; its notes name each meter's maker (second reader)
  --numbers                     #16's results/locate_per_image.csv, to join the number results

Writes a Markdown report to stdout and per-photo rows to --rows.

Run from experiments/autodetect/meter_brand:
  git show origin/t3/meter-closeup:experiments/meter-closeup/manifest.csv > /tmp/m16_manifest.csv
  git show origin/t3/meter-closeup:experiments/meter-closeup/results/locate_per_image.csv > /tmp/m16_locate.csv
  python3 score.py --manifest /tmp/m16_manifest.csv --numbers /tmp/m16_locate.csv \
      --rows results/brand_per_image.csv > results/brand.md
"""

import argparse
import csv
import json
import math
import os
import re
from pathlib import Path

from brands import SPEC, canonical, match_brand, normalise

DATA = Path(os.environ.get("METER_DATA", Path.home() / "house-scanning-data" / "meter"))
CONFIGS = ("accurate", "accurate_lc", "fast")


def wilson(k: int, n: int) -> str:
    if n == 0:
        return "–"
    z = 1.96
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return f"{k}/{n} = {100 * p:.0f}% ({100 * (centre - half):.0f}%–{100 * (centre + half):.0f}%)"


def load_ocr() -> dict[tuple[str, str], list[dict]]:
    out = {}
    for line in open(DATA / "ocr" / "clean.jsonl"):
        r = json.loads(line)
        out[(Path(r["path"]).stem, r["config"])] = r["lines"]
    return out


def pick_listed(lines: list[dict]) -> tuple[str | None, bool]:
    """The listed maker on the tallest line naming one, and whether several makers were named."""
    found = [(l["box"][3], match_brand(l["text"])) for l in lines]
    found = [f for f in found if f[1]]
    if not found:
        return None, False
    return max(found)[1], len({b for _, b in found}) > 1


def pick_unlisted(lines: list[dict]) -> str | None:
    """Without a list: the tallest line that reads as a word, not a rating, number or code."""
    words = []
    for l in lines:
        norm = normalise(l["text"])
        letters = sum(c.isalpha() for c in norm)
        if letters >= 3 and letters >= 0.6 * len(norm.replace(" ", "")) and not SPEC.search(l["text"].upper()):
            words.append((l["box"][3], l["text"]))
    return max(words)[1] if words else None


def names_brand(text: str | None, brand: str) -> bool:
    if not text:
        return False
    return match_brand(text) == brand or bool(re.search(
        rf"(?:^| ){re.escape(normalise(brand))}(?:$| )", normalise(text)))


def note_head(pid: str, manifest: dict[str, dict]) -> str:
    """#16 reader 1 opened each note with the maker and model, e.g. `Tatung D4S; faded print`.

    A note reading `same meter as m05` refers to that photo's note.
    """
    head = manifest.get(pid, {}).get("notes", "").split(";")[0]
    same = re.match(r"same meter as (m\d+)", head)
    return note_head(same.group(1), manifest) if same else head


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--numbers", required=True)
    ap.add_argument("--rows", required=True)
    args = ap.parse_args()

    ocr = load_ocr()
    labels = {r["id"]: r for r in csv.DictReader(open(DATA / "brand_reader_a.csv"))}
    manifest = {r["id"]: r for r in csv.DictReader(open(args.manifest))}
    numbers = {r["id"]: r for r in csv.DictReader(open(args.numbers))}

    rows = []
    for pid, lab in sorted(labels.items()):
        m = manifest.get(pid, {})
        if m.get("usable") != "yes":
            continue
        # Drop the reader's qualifiers and translations, e.g. "OXFORO (uncertain)", "DZG (Deutsche ...)".
        printed_name = re.sub(r"\(.*?\)", "", lab["brand_printed"]).strip()
        brand = canonical(printed_name) if printed_name else None
        head = note_head(pid, manifest)
        row = {
            "id": pid,
            "brand": brand or "",
            "form": lab["brand_form"],
            "legible": lab["brand_legible"],
            "second_reader": match_brand(head) or "",
            # Agreed when #16's note names reader A's brand, listed or not.
            "agreed": "yes" if brand and names_brand(head, brand) else "no",
            "us_style": numbers.get(pid, {}).get("us_style", ""),
            "number_read": numbers.get(pid, {}).get("read", ""),
            "number_rank": numbers.get(pid, {}).get("rank", ""),
        }
        for cfg in CONFIGS:
            lines = ocr.get((pid, cfg), [])
            listed, several = pick_listed(lines)
            unlisted = pick_unlisted(lines)
            row[f"{cfg}_listed"] = listed or ""
            row[f"{cfg}_several"] = int(several)
            row[f"{cfg}_any_line"] = int(bool(brand) and any(names_brand(l["text"], brand) for l in lines))
            row[f"{cfg}_unlisted"] = unlisted or ""
            row[f"{cfg}_unlisted_right"] = int(bool(brand) and names_brand(unlisted, brand))
        rows.append(row)

    Path(args.rows).parent.mkdir(parents=True, exist_ok=True)
    with open(args.rows, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    def subset(name: str, keep) -> tuple[str, list[dict]]:
        return name, [r for r in rows if keep(r)]

    printed = lambda r: r["brand"] and r["form"] in ("text", "logo_text") and r["legible"] in ("yes", "partial")
    sets = [
        subset("brand printed in letters, both readers agree", lambda r: printed(r) and r["agreed"] == "yes"),
        subset("brand printed in letters, reader A", printed),
        subset("US-style meters, brand printed, agreed", lambda r: printed(r) and r["agreed"] == "yes" and r["us_style"] == "1"),
    ]

    print("# Meter brand reading\n")
    print(f"Usable photos: {len(rows)}. Reader A named a brand on {sum(bool(r['brand']) for r in rows)}; "
          f"printed in letters and legible on {sum(bool(printed(r)) for r in rows)}; "
          f"the two readers agree on {sum(bool(printed(r)) and r['agreed'] == 'yes' for r in rows)} of those.\n")
    forms = {}
    for r in rows:
        forms[(r["form"], r["legible"])] = forms.get((r["form"], r["legible"]), 0) + 1
    print("| Brand form | Legible | Photos |\n|---|---|---|")
    for (form, leg), n in sorted(forms.items()):
        print(f"| {form} | {leg} | {n} |")
    print()

    for cfg in CONFIGS:
        print(f"## Vision `{cfg}`\n")
        print("| Photos | Brand in any line | List rule: right | List rule: wrong maker | List rule: none | No-list rule: right |")
        print("|---|---|---|---|---|---|")
        for name, rs in sets:
            n = len(rs)
            anyl = sum(r[f"{cfg}_any_line"] for r in rs)
            right = sum(r[f"{cfg}_listed"] == r["brand"] for r in rs)
            wrong = sum(bool(r[f"{cfg}_listed"]) and r[f"{cfg}_listed"] != r["brand"] for r in rs)
            none = sum(not r[f"{cfg}_listed"] for r in rs)
            unl = sum(r[f"{cfg}_unlisted_right"] for r in rs)
            print(f"| {name} ({n}) | {wilson(anyl, n)} | {wilson(right, n)} | {wrong} | {none} | {wilson(unl, n)} |")
        print()

    print("## By how the brand is printed (`accurate`, both readers agree)\n")
    print("| Printed as | Photos | Brand in any line | List rule: right | List rule: wrong maker |\n|---|---|---|---|---|")
    for form, label in (("text", "plain letters"), ("logo_text", "letters inside a logo or wordmark")):
        rs = [r for r in sets[0][1] if r["form"] == form]
        n = len(rs)
        print(f"| {label} | {n} | {wilson(sum(r['accurate_any_line'] for r in rs), n)} | "
              f"{wilson(sum(r['accurate_listed'] == r['brand'] for r in rs), n)} | "
              f"{sum(bool(r['accurate_listed']) and r['accurate_listed'] != r['brand'] for r in rs)} |")
    print()

    # Photos without a readable brand: does the list rule stay silent?
    silent = [r for r in rows if not printed(r)]
    false_named = [r for r in silent if r["accurate_listed"]]
    print(f"Photos without a brand printed in legible letters: {len(silent)}. "
          f"The list rule (`accurate`) named a maker on {len(false_named)} of them"
          + (": " + ", ".join(f"{r['id']} → {r['accurate_listed']} (reader A: {r['brand'] or 'none'}, {r['form']})" for r in false_named) if false_named else "")
          + ".\n")

    print("## Brand and number together (`accurate`, #16's ranking for the number)\n")
    print("| Photos | Brand right (list rule) | Number in #16's top three | Both |\n|---|---|---|---|")
    for name, rs in sets:
        rs = [r for r in rs if r["number_read"] != ""]
        n = len(rs)
        b = [r["accurate_listed"] == r["brand"] for r in rs]
        top3 = [r["number_read"] == "1" and r["number_rank"] not in ("", "0") and int(r["number_rank"]) <= 3 for r in rs]
        print(f"| {name} ({n}) | {wilson(sum(b), n)} | {wilson(sum(top3), n)} | {wilson(sum(x and y for x, y in zip(b, top3)), n)} |")
    print()

    misses = [r for name, rs in sets[:1] for r in rs if r["accurate_listed"] != r["brand"]]
    print("## Misses, `accurate`, agreed set\n")
    print("| Photo | Label | Picked | Form | Legible | Brand in any line |\n|---|---|---|---|---|---|")
    for r in misses:
        print(f"| {r['id']} | {r['brand']} | {r['accurate_listed'] or '–'} | {r['form']} | {r['legible']} | {r['accurate_any_line']} |")


if __name__ == "__main__":
    main()
