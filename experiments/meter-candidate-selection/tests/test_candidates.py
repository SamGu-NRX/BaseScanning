"""Candidate extraction behaves like the meter_eval.locate interface it mirrors."""

from meter_candidates.candidates import candidates, core, digits, normalize, tokens


def test_core_and_normalize_match_match_py():
    assert normalize("No. 12345678") == "NO12345678"
    assert core("NO. 12345678") == "12345678"
    assert core("ABC 123456") == "123456"
    # A barcode line that merely contains the number does not smuggle a prefix in.
    assert core("*XYZ1234567890*") == "1234567890"


def test_tokens_merge_digit_runs_and_strip_the_label_word():
    assert tokens("No. 12345678") == ["12345678"]
    assert tokens("S/N 90274815") == ["90274815"]
    assert tokens("12 345 678") == ["12 345 678"]
    assert "1234 5678" in tokens("1 ABC00 1234 5678")
    assert tokens("KWH") == []
    assert digits("12 345 678") == 8


def test_features_flag_keyword_spec_zeros_and_vertical():
    result = {
        "lines": [
            {"text": "No. 24681097", "box": [0, 0, 300, 40]},
            {"text": "20(60)A 50Hz", "box": [0, 100, 190, 20]},
            {"text": "00000000", "box": [0, 200, 400, 60]},
            {"text": "No. 55551024", "box": [520, 80, 34, 160]},
        ],
        "barcodes": [],
    }
    by_core = {c["core"]: c for c in candidates(result)}
    gold = by_core["24681097"]
    assert gold["keyword"] and gold["alone"] and gold["length_ok"]
    assert by_core["2060A"]["spec"]
    assert by_core["00000000"]["zeros"]
    assert by_core["55551024"]["vertical"]


def test_a_token_inside_one_payload_takes_the_payload_core():
    result = {
        "lines": [{"text": "6192 073", "box": [60, 130, 260, 36]}],
        "barcodes": [{"payload": "61920734", "box": [60, 130, 260, 36]}],
    }
    (candidate,) = candidates(result)
    assert candidate["core"] == "61920734"
    assert candidate["barcode_confirmed"]


def test_an_ambiguous_partial_read_stays_unpromoted():
    result = {
        "lines": [{"text": "6192 073", "box": [60, 130, 260, 36]}],
        "barcodes": [
            {"payload": "A61920734B", "box": [0, 0, 10, 10]},
            {"payload": "61920734C", "box": [0, 10, 10, 10]},
        ],
    }
    (candidate,) = candidates(result)
    assert candidate["core"] == "6192073"
    assert not candidate["barcode_confirmed"]
