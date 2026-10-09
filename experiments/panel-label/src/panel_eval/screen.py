"""Tally screened.csv: how many openly licensed panel photos have a legible label field.

screened.csv lists every photo examined at full resolution after screening thumbnails, with a
per-field call made by eye: manufacturer, model or series, and amperage. Duplicates (the same
file found twice, or one photo reposted) are counted once. The flag cells are typed by hand,
so they may read 1/0, yes/no or true/false in any case, padded or not.
"""

import csv
from collections.abc import Iterable

from panel_eval.paths import EXPERIMENT_DIR

SCREENED = EXPERIMENT_DIR / "screened.csv"
# The pre-set bar: "fewer than about 40 usable photos" stops the experiment.
REQUIRED = 40

# Every row must carry the dedupe pointer and the three flags; a short line or renamed
# column would otherwise turn silently into "nothing legible".
FIELDS = ("duplicate_of", "manufacturer", "model", "amperage")
FLAG_FIELDS = FIELDS[1:]
# The flags are typed by hand, so accept the obvious yes/no spellings.
YES = {"1", "yes", "y", "true", "t"}
NO = {"0", "no", "n", "false", "f", ""}


def _flag(field: str, value: str, where: str) -> bool:
    text = value.strip().lower()
    if text in YES:
        return True
    if text in NO:
        return False
    raise ValueError(f"{where}: {field} is {value!r}; expected a yes/no flag like 1, yes, or no")


def _ident(row: dict) -> str:
    ident = row.get("id")
    return "" if ident is None else str(ident).strip()


def tally(rows: Iterable[dict]) -> dict[str, int]:
    records = list(rows)
    # A duplicate_of citing no id in the file would drop the photo from every count, so
    # references are checked whenever the rows carry ids at all.
    known_ids = {_ident(r) for r in records}
    known_ids.discard("")

    counts = {
        "examined": 0,
        "any field legible": 0,
        "manufacturer and (model or amperage)": 0,
        "all three": 0,
    }
    for position, r in enumerate(records, start=1):
        ident = _ident(r)
        where = f"screened row {position} ({ident})" if ident else f"screened row {position}"
        missing = [f for f in FIELDS if r.get(f) is None]
        if missing:
            raise ValueError(
                f"{where} has no value for {', '.join(missing)}"
                " - is the CSV line truncated or a column renamed?"
            )
        manufacturer, model, amperage = (_flag(f, str(r[f]), where) for f in FLAG_FIELDS)
        duplicate_of = str(r["duplicate_of"]).strip()
        if duplicate_of and known_ids:
            if duplicate_of not in known_ids:
                raise ValueError(
                    f"{where} cites duplicate_of {duplicate_of!r}, which is not an id in the file"
                )
            if duplicate_of == ident:
                raise ValueError(f"{where} cites itself as its own duplicate")
        if duplicate_of:
            continue
        counts["examined"] += 1
        if manufacturer or model or amperage:
            counts["any field legible"] += 1
        if manufacturer and (model or amperage):
            counts["manufacturer and (model or amperage)"] += 1
        if manufacturer and model and amperage:
            counts["all three"] += 1
    return counts


def main() -> None:
    try:
        handle = SCREENED.open(encoding="utf-8", newline="")
    except FileNotFoundError:
        raise SystemExit(
            f"screened.csv not found at {SCREENED} - screening results are maintained by hand"
        ) from None
    with handle:
        counts = tally(csv.DictReader(handle))
    for name, count in counts.items():
        print(f"{name}: {count}")
    verdict = "meets" if counts["any field legible"] >= REQUIRED else "is short of"
    print(f"Even the most lenient count {verdict} the {REQUIRED}-photo bar.")


if __name__ == "__main__":
    main()
