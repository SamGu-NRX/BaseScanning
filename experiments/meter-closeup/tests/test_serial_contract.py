"""Sam's complete OCR contract: every row must agree on reading and ranking."""

import pytest

from meter_eval.fieldtest import reads_number
from meter_eval.locate import candidates, evaluate, ranked
from meter_eval.match import digest, number_core

CASES = [
    ("1234567", "1234567", True),
    ("1234567", "123 4567", True),
    ("1234567", "123-4567", True),
    ("1234567", "123 4567 240 V", True),
    ("1234567", "1234567 240V", True),
    ("1234567", "CL200 1234567", True),
    ("1234567", "SN 1234567 MADE IN USA", True),
    ("1234567", "1234567 8", False),
    ("1234567", "9 1234567", False),
    ("1234567", "12345678", False),
    ("1234567", "7654321", False),
    ("ABC1234567", "ABC1234567", True),
    ("ABC1234567", "ABC 1234567", True),
    ("ABC1234567", "abc 1234567", True),
    ("ABC1234567", "ABC|1234567", True),
    ("ABC1234567", "1234567", False),
    ("No. 1234567", "1234567", True),
    ("Nr. 1234567", "1234567", True),
    ("N° 1234567", "1234567", True),
    ("S/N: AB-12345", "AB12345", True),
    ("S/N: AB-12345", "ab 12345", True),
    ("A1234567", "SN A 1234567", True),
    ("1234567A", "SN 1234567A", True),
    ("1234567", "1234567 240.0V", True),
    ("1234567", "240.0V 1234567", True),
    ("1234567", "٩1234567", False),
    ("1234567", "1234567８", False),
    ("1234567", "А1234567", False),
    ("１２３４５６７", "１２３４５６７", True),
    ("１２３４５６７", "1234567", True),
    ("ABSN12345", "AB SN 12345", False),
    ("ABSN12345", "ABSN12345", True),
]


def observations(text):
    pieces = text.split("|")
    return [
        {"text": piece, "box": [0.1 + i * 0.3, 0.2, 0.25, 0.1]} for i, piece in enumerate(pieces)
    ]


@pytest.mark.parametrize("target,text,expected", CASES)
def test_read_and_rank_follow_the_complete_contract(target, text, expected):
    lines = observations(text)
    read = reads_number(lines, target)
    found = candidates({"lines": lines, "barcodes": []}, target)
    order = ranked(found)
    rank = next(
        (i + 1 for i, candidate in enumerate(order) if candidate == number_core(target)), None
    )
    ranked_read = rank is not None
    assert read is expected
    assert ranked_read is expected
    assert read == ranked_read
    result = evaluate(
        {
            "id": "synthetic",
            "class_kind": "",
            "number_agreed_strict": "yes",
            "number_agreed": "yes",
            "number_hmac": digest(number_core(target)),
            "number_core_hmac": digest(number_core(target)),
            "number_core_len": len(number_core(target)),
        },
        {"lines": lines, "barcodes": []},
        "test",
    )
    assert bool(result["read"]) is expected
    assert (result["rank"] is not None) is expected
    assert result["rank"] == rank
    if text == "ABC|1234567":
        exact = next(c for c in found if c["core"] == "ABC1234567")
        assert exact["boxes"] == [line["box"] for line in lines]
        assert exact["box"] == pytest.approx([0.1, 0.2, 0.55, 0.1])
        # Input order must not change the serial's x-order concatenation.
        assert reads_number(list(reversed(lines)), target)


@pytest.mark.parametrize(
    "rating",
    [
        "240 V",
        "240VAC",
        "10 A",
        "10AMP",
        "10 AMPS",
        "60HZ",
        "5 W",
        "2KW",
        "3 KWH",
        "240VA",
        "1 KH",
        "1TA",
        "3 PH",
        "4WIRE",
        "CL200",
        "CL 200",
        "FM 12",
        "2S",
        "3W",
    ],
)
def test_ratings_are_boundaries_on_both_sides(rating):
    for text in (f"{rating} 1234567", f"1234567 {rating}"):
        lines = observations(text)
        assert reads_number(lines, "1234567")
        assert ranked(candidates({"lines": lines, "barcodes": []}, "1234567"))[0] == "1234567"


def test_other_rows_do_not_complete_a_serial():
    lines = observations("ABC|1234567")
    lines[1]["box"][1] = 0.6
    assert not reads_number(lines, "ABC1234567")


@pytest.mark.parametrize("text", ["CL 200", "FM 2S", "240V"])
def test_class_or_unit_number_is_not_a_serial(text):
    assert not candidates({"lines": observations(text), "barcodes": []})


@pytest.mark.parametrize("target,text,expected", CASES)
def test_field_cli_reports_the_contract_table(
    tmp_path, monkeypatch, capsys, target, text, expected
):
    import sys

    from PIL import Image

    from meter_eval import fieldtest

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def read(self, path, barcodes):
            return {"lines": observations(text), "barcodes": []}

    Image.new("RGB", (128, 128), "white").save(tmp_path / "photo.jpg")
    monkeypatch.setattr(fieldtest, "Reader", Reader)
    monkeypatch.setattr(sys, "argv", ["fieldtest", str(tmp_path), "--number", target])
    fieldtest.main()
    output = capsys.readouterr().out
    assert f"| photo.jpg | {'yes' if expected else 'no'} | {'1' if expected else '–'} |" in output


def test_maker_only_observation_requires_a_number_retake():
    import numpy as np

    from meter_eval import retake
    from meter_eval.locate import top_candidate

    lines = observations("ITRON")
    result = {"lines": lines, "barcodes": []}
    assert top_candidate(result) is None
    assert not reads_number(lines, "1234567")
    sharp = np.random.default_rng(0).integers(0, 256, (128, 128), dtype=np.uint8)
    assert retake.reasons(sharp, None) == ["no number found"]


def test_maker_on_another_row_cannot_be_the_number_candidate():
    from meter_eval.locate import top_candidate

    lines = [
        {"text": "ITRON", "box": [0.1, 0.1, 0.7, 0.2]},
        {"text": "1234567", "box": [0.1, 0.6, 0.4, 0.1]},
    ]
    found = candidates({"lines": lines, "barcodes": []})
    assert [candidate["core"] for candidate in found] == ["1234567"]
    assert top_candidate({"lines": lines, "barcodes": []})["core"] == "1234567"


def test_two_number_rows_are_each_alone_and_keep_the_prior_rule_pick():
    from meter_eval.locate import rules

    lines = [
        {"text": "1234567", "box": [0.1, 0.1, 0.4, 0.1]},
        {"text": "7654321", "box": [0.1, 0.6, 0.4, 0.2]},
    ]
    found = candidates({"lines": lines, "barcodes": []})
    assert all(candidate["alone"] for candidate in found)
    assert len({candidate["row_id"] for candidate in found}) == 2
    pick = rules(found, [])["alone on its line, 6-14 characters, not a spec line"]
    assert pick["core"] == "7654321"


def test_row_features_use_the_source_row_and_keep_candidate_box_separate():
    from meter_eval.locate import near_barcode

    lines = [
        {"text": "SN", "box": [0.1, 0.2, 0.1, 0.1]},
        {"text": "ABC", "box": [0.25, 0.2, 0.1, 0.1]},
        {"text": "1234567", "box": [0.4, 0.2, 0.2, 0.1]},
    ]
    found = candidates({"lines": lines, "barcodes": []}, "ABC1234567")
    assert {candidate["core"] for candidate in found} == {"ABC1234567", "1234567"}
    assert all(not candidate["alone"] for candidate in found)
    assert all(candidate["keyword"] and not candidate["spec"] for candidate in found)
    assert all(
        candidate["height"] == pytest.approx(0.1) and not candidate["vertical"]
        for candidate in found
    )
    exact = next(candidate for candidate in found if candidate["matches_target"])
    assert exact["row_box"] == pytest.approx([0.1, 0.2, 0.5, 0.1])
    assert exact["box"] == pytest.approx([0.25, 0.2, 0.35, 0.1])
    assert near_barcode(exact, [{"payload": "x", "box": [0.1, 0.31, 0.1, 0.03]}])


def test_keyword_scope_is_the_row_prefix_not_a_later_group():
    lines = observations("MADE SN 1234567")
    assert all(
        not candidate["keyword"] for candidate in candidates({"lines": lines, "barcodes": []})
    )


@pytest.mark.parametrize("rating", ["240.0V", "240.0 V", "2.5A", "KH 7.2", "KH7.2", "CL200"])
def test_complete_decimal_ratings_leave_no_partial_candidate(rating):
    for text in (f"1234567 {rating}", f"{rating} 1234567"):
        found = candidates({"lines": observations(text), "barcodes": []}, "1234567")
        assert {candidate["core"] for candidate in found} == {"1234567"}


@pytest.mark.parametrize("text", ["٩ 1234567", "1234567 А", "Α 1234567"])
def test_invalid_ocr_groups_are_numeric_like_neighbours(text):
    assert not reads_number(observations(text), "1234567")


@pytest.mark.parametrize("target", ["٩1234567", "А1234567", "Α1234567"])
def test_invalid_target_is_a_loud_argument_error(tmp_path, monkeypatch, capsys, target):
    import sys

    from meter_eval import fieldtest

    monkeypatch.setattr(sys, "argv", ["fieldtest", str(tmp_path), "--number", target])
    with pytest.raises(SystemExit) as error:
        fieldtest.main()
    assert error.value.code == 2
    assert "--number contains an unsupported character" in capsys.readouterr().err


def test_standalone_label_shape_inside_a_split_serial_is_unsupported():
    # SN is always a label boundary when printed separately, even within a serial.
    assert not reads_number(observations("AB SN 12345"), "ABSN12345")
    assert reads_number(observations("ABSN12345"), "ABSN12345")


@pytest.mark.parametrize(
    "target,text",
    [
        ("ABO123456", "AB0123456"),
        ("ABI123456", "AB1123456"),
        ("A1234567", "А1234567"),
    ],
)
def test_confusable_characters_are_not_mapped(target, text):
    assert not reads_number(observations(text), target)


def test_decimal_rating_spanning_observations_keeps_the_serial_box():
    lines = observations("1234567|240.0|V")
    found = candidates({"lines": lines, "barcodes": []}, "1234567")
    assert {candidate["core"] for candidate in found} == {"1234567"}
    assert found[0]["boxes"] == [lines[0]["box"]]


@pytest.mark.parametrize("text", ["SN.240V 1234567", "240V. 1234567", "SN:2.5A 1234567"])
def test_complete_ratings_at_label_and_punctuation_boundaries(text):
    found = candidates({"lines": observations(text), "barcodes": []}, "1234567")
    assert {candidate["core"] for candidate in found} == {"1234567"}


@pytest.mark.parametrize("serial", ["12345V", "1234.5A", "CL1234", "FM12345"])
def test_long_rating_shaped_tokens_remain_serials(serial):
    assert reads_number(observations(f"SN {serial}"), serial)


@pytest.mark.parametrize("unsupported,ascii_serial", [("ß1234", "SS1234"), ("ﬀ1234", "FF1234")])
def test_unicode_expansions_are_invalid_as_targets_and_ocr(unsupported, ascii_serial):
    from meter_eval.match import read_target

    assert number_core(unsupported) is None
    assert read_target(unsupported) is None
    assert not reads_number(observations(ascii_serial), unsupported)
    assert not candidates({"lines": observations(ascii_serial), "barcodes": []}, unsupported)
    assert not reads_number(observations(unsupported), ascii_serial)
    assert not candidates({"lines": observations(unsupported), "barcodes": []}, ascii_serial)


@pytest.mark.parametrize(
    "target,text",
    [
        ("1234567", "１２３４５６７"),
        ("１２３４５６７", "1234567"),
        ("ABC1234567", "ＡＢＣ１２３４５６７"),
        ("ａｂｃ１２３４５６７", "ABC1234567"),
    ],
)
def test_only_fullwidth_ascii_is_mapped(target, text):
    assert reads_number(observations(text), target)
    assert ranked(candidates({"lines": observations(text), "barcodes": []}, target))[
        0
    ] == number_core(target)


@pytest.mark.parametrize("payload", ["é1234567", "序號1234", "ß1234", "ﬀ1234", "e\u03011234567"])
def test_invalid_barcodes_do_not_abort_photo_scoring(tmp_path, monkeypatch, capsys, payload):
    import sys

    from PIL import Image

    from meter_eval import fieldtest
    from meter_eval.locate import barcode_stats, payload_tokens

    result = {
        "lines": observations("1234567"),
        "barcodes": [{"payload": payload, "box": [0.1, 0.5, 0.4, 0.1]}],
    }
    row = {
        "id": "synthetic",
        "class_kind": "",
        "number_agreed_strict": "yes",
        "number_agreed": "yes",
        "number_hmac": digest("1234567"),
        "number_core_hmac": digest("1234567"),
        "number_core_len": 7,
    }
    assert payload_tokens(result["barcodes"]) == set()
    assert barcode_stats(row, result) == {
        "barcode_found": 1,
        "barcode_decoded": 1,
        "barcode_has_number": 0,
    }
    offline = evaluate(row, result, "test")
    assert offline["read"] == 1
    assert offline["rank"] == 1

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def read(self, path, barcodes):
            return result

    Image.new("RGB", (128, 128), "white").save(tmp_path / "photo.jpg")
    monkeypatch.setattr(fieldtest, "Reader", Reader)
    monkeypatch.setattr(sys, "argv", ["fieldtest", str(tmp_path), "--number", "1234567"])
    fieldtest.main()
    assert "| photo.jpg | yes | 1 |" in capsys.readouterr().out


@pytest.mark.parametrize("target", ["ß1234", "ﬀ1234", "e\u03011234567", "１２３４５６７\u0301"])
def test_unicode_expansion_target_is_a_cli_argument_error(tmp_path, monkeypatch, capsys, target):
    import sys

    from meter_eval import fieldtest

    monkeypatch.setattr(sys, "argv", ["fieldtest", str(tmp_path), "--number", target])
    with pytest.raises(SystemExit) as error:
        fieldtest.main()
    assert error.value.code == 2
    assert "--number contains an unsupported character" in capsys.readouterr().err


@pytest.mark.parametrize(
    "target,text",
    [
        ("E1234567", "e\u03011234567"),
        ("1234567", "1234567\u200b8"),
        ("1234567", "１２３４５６７\u0301"),
    ],
)
def test_undefined_characters_invalidate_the_entire_ocr_group(target, text):
    assert not reads_number(observations(text), target)
    # No longer serial may be assembled by dropping the unsupported character either.
    assert candidates({"lines": observations(text), "barcodes": []}, target) == []
    assert number_core(text) is None


@pytest.mark.parametrize("character", ["\u0301", "\u200b", "\u200d", "\ufeff", "😀", "♥", "\0"])
def test_unsupported_marks_symbols_and_format_characters_are_not_separators(character):
    for text in (f"123{character}4567", f"{character} 1234567", f"1234567 {character}"):
        assert not reads_number(observations(text), "1234567")
    assert number_core(f"No. {character}1234567") is None


@pytest.mark.parametrize("separator", [" ", "\t", "\n", "\u00a0", "\u2003", "-", "/", ".", ":"])
def test_only_defined_separators_split_serial_groups(separator):
    text = f"123{separator}4567"
    assert reads_number(observations(text), "1234567")
    assert number_core(text) == "1234567"


@pytest.mark.parametrize("text", ["240V\u200b", "\u0301240V", "240V😀"])
def test_invalid_rating_shaped_group_is_not_extracted_as_a_boundary(text):
    assert not reads_number(observations(f"1234567 {text}"), "1234567")


@pytest.mark.parametrize(
    "label",
    [
        "#",
        "№",
        "N°",
        "No",
        "No.",
        "Nr",
        "Nr.",
        "S/N",
        "SN",
        "Serial",
        "Meter",
        "Serial No",
        "Meter No",
        "Meter #",
    ],
)
def test_attached_label_prefix_is_a_boundary_before_validating_the_suffix(label):
    text = f"{label}1234567"
    lines = observations(text)
    assert reads_number(lines, "1234567")
    assert number_core(text) == "1234567"
    found = candidates({"lines": lines, "barcodes": []}, "1234567")
    assert ranked(found)[0] == "1234567"
    assert next(candidate for candidate in found if candidate["core"] == "1234567")["boxes"] == [
        lines[0]["box"]
    ]


@pytest.mark.parametrize(
    "text", ["#é1234567", "№e\u03011234567", "N°123\u200b4567", "No.123\u200d4567", "SN1234567😀"]
)
def test_attached_label_does_not_erase_an_invalid_suffix(text):
    assert number_core(text) is None
    assert not reads_number(observations(text), "1234567")
    assert not candidates({"lines": observations(text), "barcodes": []}, "1234567")


@pytest.mark.parametrize(
    "payload,target",
    [
        ("X0YZ01234567;12345678", "12345678"),
        ("ABC1234567;XYZ7654321", "ABC1234567"),
        ("ABC1234567;XYZ7654321", "XYZ7654321"),
        ("*1234567*", "1234567"),
        ("{1234567}", "1234567"),
        ("unrelated,1234567", "1234567"),
        ("é1234567;1234567", "1234567"),
        ("e\u03011234567;1234567", "1234567"),
    ],
)
def test_barcode_fields_are_validated_independently(payload, target):
    from meter_eval.locate import barcode_stats, payload_tokens

    barcodes = [{"payload": payload, "box": [0.1, 0.5, 0.4, 0.1]}]
    assert target in payload_tokens(barcodes)
    result = {"lines": observations(target), "barcodes": barcodes}
    found = candidates(result, target)
    assert next(candidate for candidate in found if candidate["core"] == target)[
        "barcode_confirmed"
    ]
    row = {
        "number_hmac": digest(target),
        "number_core_hmac": digest(target),
        "number_core_len": len(target),
    }
    assert barcode_stats(row, result) == {
        "barcode_found": 1,
        "barcode_decoded": 1,
        "barcode_has_number": 1,
    }


def test_barcode_fields_cannot_join_across_a_delimiter():
    from meter_eval.locate import barcode_stats, payload_tokens

    barcodes = [{"payload": "1234;567"}]
    assert "1234567" not in payload_tokens(barcodes)
    row = {
        "number_hmac": digest("1234567"),
        "number_core_hmac": digest("1234567"),
        "number_core_len": 7,
    }
    assert barcode_stats(row, {"barcodes": barcodes})["barcode_has_number"] == 0


def blind_ranking_photo():
    return {
        "lines": [
            {"text": "7654321", "box": [0.1, 0.1, 0.4, 0.2]},
            {"text": "1234567", "box": [0.1, 0.6, 0.4, 0.1]},
        ],
        "barcodes": [],
    }


def test_evaluator_scores_a_readable_serial_at_its_blind_rank():
    from meter_eval.locate import rule_table, rules

    result = blind_ranking_photo()
    row = {
        "id": "m02",
        "class_kind": "",
        "number_agreed_strict": "yes",
        "number_agreed": "yes",
        "number_hmac": digest("1234567"),
        "number_core_hmac": digest("1234567"),
        "number_core_len": 7,
    }
    assert ranked(candidates(result)) == ["7654321", "1234567"]
    scored = evaluate(row, result, "test")
    assert scored["read"] == 1
    assert scored["rank"] == 2
    row |= {"number_hmac": digest("9999999"), "number_core_hmac": digest("9999999")}
    missed = evaluate(row, result, "test")
    assert missed["read"] == 0
    assert missed["rank"] is None
    table = rule_table([scored, missed], list(rules([], [])))
    assert "ranking, top 1, over read photos only | 1 | – | 0/1 = 0%" in table
    assert "ranking, top 3, over read photos only | 1 | – | 1/1 = 100%" in table


def test_best_and_all_rules_are_blind_to_the_target_digest():
    from meter_eval.locate import best, rules

    result = blind_ranking_photo()
    result["barcodes"] = [
        {"payload": number, "box": [0.1, 0.85, 0.4, 0.05]} for number in ("7654321", "1234567")
    ]
    blind = candidates(result)
    labelled = candidates(result, target_digest=digest("1234567"))
    assert best(blind)["core"] == "7654321"
    assert best(labelled)["core"] == best(blind)["core"]
    assert ranked(labelled) == ranked(blind)
    blind_picks = rules(blind, result["barcodes"])
    labelled_picks = rules(labelled, result["barcodes"])
    assert {name: pick and pick["core"] for name, pick in labelled_picks.items()} == {
        name: pick and pick["core"] for name, pick in blind_picks.items()
    }


def test_target_annotations_do_not_change_source_order_or_blind_ties():
    result = blind_ranking_photo()
    result["lines"][1]["box"][3] = result["lines"][0]["box"][3]
    blind = candidates(result)
    for labelled in (
        candidates(result, "1234567"),
        candidates(result, target_digest=digest("1234567")),
    ):
        assert [candidate["core"] for candidate in labelled] == [
            candidate["core"] for candidate in blind
        ]
        assert ranked(labelled) == ranked(blind)


def test_field_cli_reports_the_blind_second_rank_and_uses_the_blind_box(
    tmp_path, monkeypatch, capsys
):
    import sys

    from PIL import Image

    from meter_eval import fieldtest

    result = blind_ranking_photo()

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def read(self, path, barcodes):
            return result

    selected_boxes = []

    def reasons(gray, box):
        selected_boxes.append(box)
        return []

    Image.new("RGB", (128, 128), "white").save(tmp_path / "photo.jpg")
    monkeypatch.setattr(fieldtest, "Reader", Reader)
    monkeypatch.setattr(fieldtest.retake, "reasons", reasons)
    monkeypatch.setattr(sys, "argv", ["fieldtest", str(tmp_path), "--number", "1234567"])
    fieldtest.main()
    assert "| photo.jpg | yes | 2 | no |" in capsys.readouterr().out
    assert len(selected_boxes) == 1
    assert selected_boxes[0] == pytest.approx(result["lines"][0]["box"])


@pytest.mark.parametrize(
    "target,text,expected",
    [
        ("Serial No: SN1234567", "Serial No: SN1234567", True),
        ("SN1234567", "SN 1234567", True),
        ("No. 1234567", "1234567", True),
        ("NoSN1234567", "NoSN1234567", True),
        ("Serial No: NoSN1234567", "#1234567", True),
        ("123 SN4567", "123 SN4567", False),
    ],
)
def test_targets_and_ocr_use_the_same_repeated_label_stripping(target, text, expected):
    assert number_core(target) == "1234567"
    # The last case retains a label boundary between numeric groups in the OCR.
    assert reads_number(observations(text), target) is expected


@pytest.mark.parametrize("text", ["Serial No: SNé1234567", "NoSN123\u200b4567"])
def test_repeated_labels_never_remove_an_unsupported_suffix_character(text):
    assert number_core(text) is None
    assert not reads_number(observations(text), "1234567")


def test_field_help_describes_blind_ranking(tmp_path, monkeypatch, capsys):
    import sys

    from meter_eval import fieldtest

    monkeypatch.setattr(sys, "argv", ["fieldtest", "--help"])
    with pytest.raises(SystemExit) as error:
        fieldtest.main()
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "blind heuristic ranking" in help_text
    assert "--number only scores" in help_text
    assert "exact --number match first" not in help_text


@pytest.mark.parametrize("target", ["1234567-2S", "ABC 240V", "Serial No: 1234567-2S"])
def test_cli_rejects_a_serial_with_a_rating_shaped_part(tmp_path, monkeypatch, capsys, target):
    import sys

    from meter_eval import fieldtest

    monkeypatch.setattr(sys, "argv", ["fieldtest", str(tmp_path), "--number", target])
    with pytest.raises(SystemExit) as error:
        fieldtest.main()
    assert error.value.code == 2
    assert "rating-shaped part" in capsys.readouterr().err


@pytest.mark.parametrize(
    "serial,normalised",
    [
        ("1234567-2S", "12345672S"),
        ("ABC 240V", "ABC240V"),
        ("Serial No: SN1234567-2S", "12345672S"),
    ],
)
def test_supplied_serial_normalisation_never_strips_rating_shaped_parts(serial, normalised):
    assert number_core(serial) == normalised
    assert not reads_number(observations("1234567"), serial)


@pytest.mark.parametrize(
    "payload,target,confirmed",
    [
        ("1234567-2S", "1234567", False),
        ("12345678", "1234567", False),
        ("12345678", "12345678", True),
        ("1234567-2S", "12345672S", True),
    ],
)
def test_barcode_confirmation_requires_the_whole_normalised_field(payload, target, confirmed):
    from meter_eval.locate import barcode_stats, payload_tokens

    barcodes = [{"payload": payload, "box": [0.1, 0.5, 0.4, 0.1]}]
    assert (target in payload_tokens(barcodes)) is confirmed
    found = candidates({"lines": observations(target), "barcodes": barcodes})
    candidate = next(candidate for candidate in found if candidate["core"] == target)
    assert candidate["barcode_confirmed"] is confirmed
    row = {
        "number_hmac": digest(target),
        "number_core_hmac": digest(target),
        "number_core_len": len(target),
    }
    assert barcode_stats(row, {"barcodes": barcodes})["barcode_has_number"] == int(confirmed)
