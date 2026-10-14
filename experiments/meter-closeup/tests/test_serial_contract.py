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
    ranked_read = bool(order) and order[0] == number_core(target)
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
    assert (result["rank"] == 1) is expected
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


@pytest.mark.parametrize("text", ["CL 1234567", "FM 1234567", "1234567V"])
def test_class_or_unit_number_is_not_a_serial(text):
    assert not reads_number(observations(text), "1234567")


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
