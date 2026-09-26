"""Score VLM meter-can type and rust calls against labels/meter_can_condition_hand.csv.

    B2_RAW=/tmp/b2raw python score_can.py

Hand labels are coarse (ring vs ringless is not reliably visible in these close-ups):
socket (round socket meter, its can/box visible), cabinet (meter behind a cutout/window),
bank (several meters), bolt_in (A-base / DIN / plug-in, no socket), not_visible.
"""

import csv
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "vendor"))
from meter_eval.stats import share  # noqa: E402
from score_meter import load  # noqa: E402

RAW = Path(os.environ.get("B2_RAW", HERE / "raw")) / "meter"
COARSE = {
    "ringless_socket": "socket", "ring_type_socket": "socket", "meter_main_combo": "socket",
    "cabinet_or_panel_mounted": "cabinet", "multi_meter_bank": "bank",
    "no_socket_bolt_in_meter": "bolt_in", "not_visible": "not_visible",
    "pole_or_pedestal": "not_visible", "other": "not_visible", None: "not_visible",
}
FAMILY = {"socket": "round", "cabinet": "round", "bank": "round", "bolt_in": "bolt_in", "not_visible": "not_visible"}


def main() -> None:
    with (HERE / "labels" / "meter_can_condition_hand.csv").open() as h:
        labels = {r["id"]: r for r in csv.DictReader(h)}
    print("| Model | can class (5-way) | family (round / bolt-in / not visible) | bolt-in recall | says rust (light/heavy) | rust hits on 3 labelled | heavy rust calls |")
    print("|---|---|---|---|---|---|---|")
    for path in sorted(RAW.glob("vlm_*.jsonl")):
        res = load(path)
        n = ok5 = okf = bolt = bolt_ok = rusty = rust_hit = heavy = 0
        confusion = Counter()
        for i, lab in labels.items():
            a = (res.get(i) or {}).get("answer") or {}
            pred = COARSE.get((a.get("meter_can") or {}).get("type"), "not_visible")
            n += 1
            ok5 += pred == lab["can_class"]
            okf += FAMILY[pred] == FAMILY[lab["can_class"]]
            confusion[(lab["can_class"], pred)] += 1
            if lab["can_class"] == "bolt_in":
                bolt += 1
                bolt_ok += pred == "bolt_in"
            r = (a.get("condition") or {}).get("rust")
            rusty += r in ("light", "heavy")
            heavy += r == "heavy"
            rust_hit += lab["rust"] != "none" and r in ("light", "heavy")
        name = path.stem.removeprefix("vlm_")
        print(f"| {name} | {share(ok5, n)} | {share(okf, n)} | {share(bolt_ok, bolt)} | {rusty}/{n} | {rust_hit}/3 | {heavy} |")
    print("\nLabel counts:", dict(Counter(r["can_class"] for r in labels.values())))


if __name__ == "__main__":
    main()
