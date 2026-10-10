"""Every outcome label, on reader-shaped input, with its cause."""

from meter_candidates.scoring import aggregate, evaluate_case


def _case(case_id, arm, present=True):
    return {"id": case_id, "arm": arm}


def _gold(serial="24681097"):
    return {"serial": serial, "present": serial is not None, "origin": "test"}


def _result(*lines, barcodes=()):
    return {"lines": [{"text": t, "box": b} for t, b in lines], "barcodes": list(barcodes)}


def test_top1():
    record = evaluate_case(
        _case("c1", "observed"),
        _result(("No. 24681097", [60, 120, 330, 46]), ("CL 200", [60, 260, 96, 20])),
        _gold(),
    )
    assert record["outcome"] == "top1" and record["rank"] == 1 and record["top3"] == 1


def test_top3_on_a_tie():
    record = evaluate_case(
        _case("c2", "observed"),
        _result(("No. 24681097", [60, 170, 300, 28]), ("No. 13572468", [60, 80, 360, 52])),
        _gold(),
    )
    assert record["outcome"] == "top3" and record["rank"] == 2 and record["top3"] == 1


def test_rank_miss_when_three_distractors_rank_above():
    lines = [(f"No. 1357246{i}", [60, 80 + 40 * i, 300, 40]) for i in range(3)]
    record = evaluate_case(
        _case("c3", "observed"),
        _result(*lines, ("No. 24681097", [60, 200, 300, 40])),
        _gold(),
    )
    assert record["outcome"] == "rank_miss" and record["rank"] == 4 and record["top3"] == 0


def test_filtered_miss_is_a_ranking_cause():
    record = evaluate_case(
        _case("c4", "observed"),
        _result(
            ("No. 55551024", [520, 80, 34, 160]),  # vertical print, filtered from offers
            ("20(60)A 50Hz", [60, 220, 190, 22]),
        ),
        _gold("55551024"),
    )
    assert record["outcome"] == "filtered_miss"
    assert record["candidates_total"] > 0 and record["candidates_offered"] == 0


def test_recognition_miss_is_a_recognition_cause():
    record = evaluate_case(
        _case("c5", "observed"),
        _result(("No. 482O1637", [60, 120, 330, 46])),
        _gold("48201637"),
    )
    assert record["outcome"] == "recognition_miss"


def test_correct_rejection_and_false_offer():
    rejected = evaluate_case(
        _case("c6", "observed"), _result(("CL 200", [0, 0, 96, 20])), _gold(None)
    )
    assert rejected["outcome"] == "correct_rejection"
    offered = evaluate_case(
        _case("c7", "observed"), _result(("No. 482O1637", [60, 120, 330, 46])), _gold(None)
    )
    assert offered["outcome"] == "false_offer"


def test_aggregate_splits_causes():
    records = [
        evaluate_case(_case(f"c{i}", "observed"), result, _gold())
        for i, result in enumerate(
            [
                _result(("No. 24681097", [0, 0, 100, 40])),
                _result(("No. 482O1637", [0, 0, 100, 40])),
            ]
        )
    ]
    counts = aggregate(records)["observed"]
    assert counts["cases"] == 2 and counts["top1"] == 1
    assert counts["recognition_cause"] == 1 and counts["ranking_cause"] == 0
