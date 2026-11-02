"""Score offers against gold: top-1, top-3, rejections, split by cause.

A case's reader output and its gold label join by case id only, after ranking — the
candidate and ranking code never sees a label (gold.py). Outcomes for a case whose plate
carries a serial: `top1` (offered first), `top3` (offered in the first three),
`rank_miss` (offered below third), `filtered_miss` (the serial's core was among the
candidates but none was offerable), `recognition_miss` (no candidate ever had the serial's
core: no ranking fix could have helped). For a case with no serial:
`correct_rejection` (nothing offered) or `false_offer` (something plausible-looking was
offered anyway). Ranking cause is `rank_miss` + `filtered_miss`; recognition cause is
`recognition_miss`.
"""

from collections import Counter

from meter_candidates.candidates import candidates, core
from meter_candidates.ranking import ranked

TOP_N = 3
RANKING_CAUSE = ("rank_miss", "filtered_miss")
RECOGNITION_CAUSE = ("recognition_miss",)
OUTCOMES = ("top1", "top3", "rank_miss", "filtered_miss", "recognition_miss",
            "correct_rejection", "false_offer")


def evaluate_case(case: dict, result: dict, gold: dict) -> dict:
    found = candidates(result)
    order = ranked(found)
    all_cores = {c["core"] for c in found}
    record = {
        "id": case["id"],
        "arm": case["arm"],
        "present": gold["present"],
        "candidates_total": len(found),
        "candidates_offered": len(order),
        "rank": "",
        "top3": 0,
        "outcome": "",
    }
    if not gold["present"]:
        record["outcome"] = "false_offer" if order else "correct_rejection"
        return record
    target = core(gold["serial"])
    if target not in all_cores:
        record["outcome"] = "recognition_miss"
    elif target not in order:
        record["outcome"] = "filtered_miss"
    else:
        rank = order.index(target) + 1
        record["rank"] = rank
        record["top3"] = int(rank <= TOP_N)
        record["outcome"] = "top1" if rank == 1 else ("top3" if rank <= TOP_N else "rank_miss")
    return record


def aggregate(records: list[dict]) -> dict:
    """Per-arm outcome and cause counts."""
    out: dict[str, dict] = {}
    for arm in sorted({r["arm"] for r in records}):
        rows = [r for r in records if r["arm"] == arm]
        outcomes = Counter(r["outcome"] for r in rows)
        out[arm] = {
            "cases": len(rows),
            "present_cases": sum(r["present"] for r in rows),
            "absent_cases": sum(not r["present"] for r in rows),
            "top1": outcomes["top1"],
            "top3": sum(r["top3"] for r in rows),
            "rank_miss": outcomes["rank_miss"],
            "filtered_miss": outcomes["filtered_miss"],
            "recognition_miss": outcomes["recognition_miss"],
            "correct_rejection": outcomes["correct_rejection"],
            "false_offer": outcomes["false_offer"],
            "ranking_cause": sum(outcomes[o] for o in RANKING_CAUSE),
            "recognition_cause": sum(outcomes[o] for o in RECOGNITION_CAUSE),
        }
    return out
