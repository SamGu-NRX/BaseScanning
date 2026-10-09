from meter_eval.locate import candidates, near_barcode, ranked, rules, tokens
from meter_eval.match import core


def line(text, box=(0.4, 0.5, 0.2, 0.03)):
    return {"text": text, "box": list(box), "confidence": 1.0}


def test_core_drops_letters_before_the_first_digit():
    assert core("NO. 12345678") == "12345678"
    assert core("ABC 123456") == "123456"
    assert core("1 ABC00 1234 5678") == "1ABC0012345678"
    assert core("#:00-01-2345-67") == "0001234567"


def test_tokens_merge_digit_groups_and_keep_mixed_groups():
    assert tokens("12 345 678") == ["12 345 678"]
    assert tokens("NO. 12345678") == ["12345678"]
    # A line of two to four groups is also offered whole.
    assert tokens("Type: SCS1321") == ["SCS1321", "Type: SCS1321"]
    # "1" and "ABC00" have fewer than 4 digits; the digit run and the whole line remain.
    assert tokens("1 ABC00 1234 5678") == ["1234 5678", "1 ABC00 1234 5678"]


def test_tokens_skip_short_numbers():
    assert tokens("CL 200") == []


def test_barcode_equal_to_a_text_token_confirms_it():
    result = {
        "lines": [line("123ABC456789")],
        "barcodes": [{"payload": "123ABC456789", "box": [0.4, 0.6, 0.2, 0.03]}],
    }
    [only] = candidates(result)
    assert only["barcode_confirmed"] and only["core"] == "123ABC456789"


def test_partial_text_read_takes_the_barcodes_full_string():
    # Vision dropped the leading 1; the QR payload has the whole number.
    result = {
        "lines": [line("2345678")],
        "barcodes": [{"payload": "X0YZ01234567;12345678", "box": [0.8, 0.3, 0.1, 0.05]}],
    }
    [only] = candidates(result)
    assert only["core"] == "12345678" and only["barcode_confirmed"]


def test_short_token_inside_a_payload_is_not_confirmed():
    result = {
        "lines": [line("12345")],
        "barcodes": [{"payload": "9912345000", "box": [0.8, 0.3, 0.1, 0.05]}],
    }
    [only] = candidates(result)
    assert not only["barcode_confirmed"] and only["core"] == "12345"


def test_spec_line_ranks_below_a_bare_number():
    result = {
        "lines": [
            line("220V 30(200)A 60Hz", (0.3, 0.5, 0.3, 0.05)),
            line("12345678", (0.4, 0.7, 0.2, 0.03)),
        ],
        "barcodes": [],
    }
    assert ranked(candidates(result))[0] == "12345678"


def test_near_barcode_needs_overlap_and_a_small_gap():
    barcode = [{"payload": "x", "box": [0.40, 0.60, 0.20, 0.03]}]
    above = {"box": [0.42, 0.55, 0.15, 0.03]}  # gap 0.02 <= 1.5 * 0.03
    far = {"box": [0.42, 0.40, 0.15, 0.03]}  # gap 0.17
    beside = {"box": [0.70, 0.60, 0.10, 0.03]}  # no horizontal overlap
    assert near_barcode(above, barcode)
    assert not near_barcode(far, barcode)
    assert not near_barcode(beside, barcode)


def test_rules_do_not_fire_without_their_cue():
    picks = rules(candidates({"lines": [line("12345678")], "barcodes": []}), [])
    assert picks["barcode confirms a text line"] is None
    assert picks["after a No./Nr./#: keyword"] is None
    assert picks["tallest digit line (phase 1)"]["core"] == "12345678"


def test_meter_splits_keep_every_photo_of_a_meter_on_one_side():
    from meter_eval.locate import meter_splits

    rows = [
        {"id": "m05", "number_core_hmac": "a"},  # odd: the rules saw meter a
        {"id": "m06", "number_core_hmac": "a"},  # even, but the same meter: design side
        {"id": "m08", "number_core_hmac": "b"},  # even, a meter the rules never saw
        {"id": "m10", "number_core_hmac": "b"},
        {"id": "m12", "number_core_hmac": ""},  # no labelled number: meter unknown
        {"id": "m13", "number_core_hmac": ""},
    ]
    assert meter_splits(rows) == {
        "m05": "dev",
        "m06": "dev",
        "m08": "test",
        "m10": "test",
        "m12": "unknown",
        "m13": "unknown",
    }


def test_a_tie_between_two_payloads_is_not_promoted():
    # Both payload parts contain the read and have the same length: ambiguous.
    result = {
        "lines": [line("2345678")],
        "barcodes": [
            {"payload": "A12345678", "box": [0.8, 0.3, 0.1, 0.05]},
            {"payload": "B92345678", "box": [0.8, 0.4, 0.1, 0.05]},
        ],
    }
    [only] = candidates(result)
    assert only["core"] == "2345678" and not only["barcode_confirmed"]


def test_a_single_closest_payload_is_still_promoted():
    result = {
        "lines": [line("2345678")],
        "barcodes": [
            {"payload": "12345678", "box": [0.8, 0.3, 0.1, 0.05]},
            {"payload": "Q192345678", "box": [0.8, 0.4, 0.1, 0.05]},
        ],
    }
    [only] = candidates(result)
    assert only["core"] == "12345678" and only["barcode_confirmed"]


def test_scan_creates_the_ocr_folder_on_a_fresh_data_dir(tmp_path, monkeypatch):
    import json

    from meter_eval import locate

    data = tmp_path / "meter"  # a fresh data directory: no ocr/ yet
    monkeypatch.setattr(locate, "DATA_DIR", data)

    class FakeReader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def read(self, path, barcodes=False):
            return {"lines": [], "barcodes": []}

    monkeypatch.setattr(locate, "Reader", FakeReader)
    locate.scan([{"id": "p01"}])
    lines = (data / "ocr" / "scan.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"lines": [], "barcodes": []}
