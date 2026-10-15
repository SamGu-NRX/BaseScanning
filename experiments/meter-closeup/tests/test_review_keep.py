import csv
import json
import re
import subprocess

import pytest
from PIL import Image

from meter_eval import labels, review
from meter_eval.review import TEMPLATE

PAGE_FUNCTIONS = ("startingAnswers", "keepAnswer", "answerRows")


def on_page(expression: str, **values):
    """Evaluate a JavaScript expression with the review page's own functions, in Node."""
    source = "\n".join(
        re.search(rf"function {name}\(.*?\n\}}", TEMPLATE, re.DOTALL).group(0)
        for name in PAGE_FUNCTIONS
    )
    bindings = "".join(f"const {k} = {json.dumps(v)};\n" for k, v in values.items())
    script = f"{source}\n{bindings}console.log(JSON.stringify({expression}));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def keep_answer(previous, item=None) -> dict:
    return on_page("keepAnswer(previous, item)", previous=previous, item=item or {})


def test_keep_after_a_correction_confirms_the_corrected_number():
    assert keep_answer({"verdict": "fix", "number": "ABC 7654321"}) == {
        "verdict": "keep",
        "number": "ABC 7654321",
    }


def test_keep_on_an_untouched_photo_confirms_the_readers_number():
    assert keep_answer(None, {"number": "1111111", "corrected": False}) == {
        "verdict": "keep",
        "number": "",
    }


def test_keep_in_a_new_browser_confirms_an_ingested_correction():
    item = {"id": "m01", "number": "7654321", "corrected": True}
    assert keep_answer(None, item) == {"verdict": "keep", "number": "7654321"}


def test_a_browser_keep_given_for_a_number_the_page_no_longer_shows_is_dropped():
    items = [
        {
            "id": "m01",
            "number": "7654321",
            "corrected": True,
            "answer": {"verdict": "fix", "number": "7654321"},
        },
        {"id": "m02", "number": "2222222", "corrected": False, "answer": None},
        {"id": "m03", "number": "3333333", "corrected": False, "answer": None},
        {"id": "m04", "number": "4444444", "corrected": False, "answer": None},
    ]
    stored = {
        # Kept while the page showed the readers' 1111111, before the correction was ingested.
        "m01": {"verdict": "keep", "number": "", "shown": "1111111"},
        # Kept for a number the dataset update changed.
        "m02": {"verdict": "keep", "number": "", "shown": "2220000"},
        # Kept for the number still shown.
        "m03": {"verdict": "keep", "number": "", "shown": "3333333"},
        # A Keep saved before answers recorded what they were given for.
        "m04": {"verdict": "keep", "number": ""},
    }
    saved = on_page("startingAnswers(items, stored)", items=items, stored=stored)
    assert saved == {
        "m01": {"verdict": "fix", "number": "7654321"},
        "m03": {"verdict": "keep", "number": "", "shown": "3333333"},
    }
    rows = on_page("answerRows(items, startingAnswers(items, stored))", items=items, stored=stored)
    assert rows[1:] == [
        ["m01", "fix", "7654321", "", ""],
        ["m02", "", "", "", ""],
        ["m03", "keep", "", "3333333", "yes"],
        ["m04", "", "", "", ""],
    ]


def test_a_browser_correction_survives_a_label_change_and_the_upgrade():
    items = [
        {"id": "m01", "number": "1111111", "corrected": False, "answer": None},
        {"id": "m02", "number": "2222222", "corrected": False, "answer": None},
    ]
    stored = {
        # Saved by the page before `shown` existed and never ingested: the reviewer's own number.
        "m01": {"verdict": "fix", "number": "ABC 7654321"},
        # Typed while the page showed another number, which a dataset update has since changed.
        "m02": {"verdict": "fix", "number": "2223333", "shown": "2220000"},
    }
    rows = on_page("answerRows(items, startingAnswers(items, stored))", items=items, stored=stored)
    assert rows[1:] == [
        ["m01", "fix", "ABC 7654321", "", ""],
        ["m02", "fix", "2223333", "2220000", "yes"],
    ]


def test_a_kept_correction_survives_a_label_change():
    """Fix, then Keep in the same browser, then a dataset update changes the readers' number."""
    before = [{"id": "m01", "number": "1111111", "corrected": False, "answer": None}]
    fixed = {"verdict": "fix", "number": "ABC 7654321", "shown": "1111111"}
    kept = on_page("keepAnswer(previous, items[0])", previous=fixed, items=before)
    stored = {"m01": {**kept, "shown": "1111111"}}
    after = [{"id": "m01", "number": "1112222", "corrected": False, "answer": None}]
    rows = on_page("answerRows(items, startingAnswers(items, stored))", items=after, stored=stored)
    assert rows[1:] == [["m01", "keep", "ABC 7654321", "1111111", "yes"]]


def write(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_build_treats_a_kept_correction_as_human_confirmed(tmp_path, monkeypatch):
    reader = {
        "id": "m01",
        "meter_number": "1111111",
        "other_numbers": "",
        "number_sure": "sure",
        "class_label": "NONE",
        "class_sure": "",
        "notes": "",
    }
    source = {
        "id": "m01",
        "title": "t",
        "page_url": "p",
        "image_url": "i",
        "license": "CC0",
        "license_url": "",
        "author": "a",
    }
    write(tmp_path / "r1.csv", [reader])
    write(tmp_path / "r2.csv", [reader])
    write(tmp_path / "src.csv", [source])
    write(tmp_path / "human.csv", [{"id": "m01", "verdict": "keep", "number": "ABC 7654321"}])
    monkeypatch.setattr(labels, "READER1", tmp_path / "r1.csv")
    monkeypatch.setattr(labels, "READER2", [tmp_path / "r2.csv"])
    monkeypatch.setattr(labels, "SOURCES", tmp_path / "src.csv")
    monkeypatch.setattr(labels, "HUMAN", tmp_path / "human.csv")
    [row] = labels.build()
    assert row["number_hmac"] == labels.digest("ABC7654321")
    assert row["number_core_hmac"] == labels.digest("ABC7654321")
    assert row["number_core_len"] == len("ABC7654321")
    assert row["number_agreed"] == row["number_agreed_strict"] == "yes"
    assert row["human_check"] == "keep"
    assert "ABC7654321" in labels.known_numbers()


def test_a_correction_survives_rebuild_keep_in_a_new_browser_and_ingest(tmp_path, monkeypatch):
    reader = {
        "id": "m01",
        "meter_number": "1111111",
        "other_numbers": "",
        "number_sure": "sure",
        "class_label": "NONE",
        "class_sure": "",
        "notes": "",
    }
    source = {
        "id": "m01",
        "title": "t",
        "page_url": "p",
        "image_url": "i",
        "license": "CC0",
        "license_url": "",
        "author": "a",
    }
    (tmp_path / "images").mkdir()
    Image.new("RGB", (40, 30), "white").save(tmp_path / "images" / "m01.jpg")
    for name in ("labels_reader1.csv", "labels_reader2a.csv", "labels_reader2b.csv"):
        write(tmp_path / name, [reader])
    write(tmp_path / "src.csv", [source])
    write(tmp_path / "clean_per_image.csv", [{"id": "m01", "number_box": "[0.1, 0.1, 0.5, 0.2]"}])
    human = tmp_path / "labels_human.csv"
    write(human, [{"id": "m01", "verdict": "fix", "number": "7654321"}])
    monkeypatch.setattr(labels, "READER1", tmp_path / "labels_reader1.csv")
    monkeypatch.setattr(labels, "READER2", [tmp_path / "labels_reader2a.csv"])
    monkeypatch.setattr(labels, "SOURCES", tmp_path / "src.csv")
    monkeypatch.setattr(labels, "HUMAN", human)
    for name, value in {
        "DATA_DIR": tmp_path,
        "RESULTS_DIR": tmp_path,
        "REVIEW_DIR": tmp_path / "review",
        "MANIFEST": tmp_path / "manifest.csv",
        "HUMAN_LABELS": human,
    }.items():
        monkeypatch.setattr(review, name, value)

    def rebuild_manifest() -> dict:
        [row] = labels.build()
        with (tmp_path / "manifest.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=labels.FIELDS, restval="")
            writer.writeheader()
            writer.writerow(row)
        return row

    assert rebuild_manifest()["number_hmac"] == labels.digest("7654321")
    review.build()
    page = (tmp_path / "review" / "review.html").read_text()
    items = json.loads(re.search(r'id="items">(.*?)</script>', page, re.DOTALL).group(1))
    assert items[0]["number"] == "7654321"  # the page shows the correction, not the reader

    # A new browser: nothing in localStorage. Keep, then download the answers.
    rows = on_page(
        "(() => { const saved = startingAnswers(items, {});"
        " saved.m01 = keepAnswer(saved.m01, items[0]); return answerRows(items, saved); })()",
        items=items,
    )
    assert rows[1] == ["m01", "keep", "7654321", "", ""]
    download = tmp_path / "answers.csv"
    with download.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    review.ingest(str(download))

    row = rebuild_manifest()
    assert row["number_hmac"] == labels.digest("7654321")
    assert row["number_agreed"] == row["number_agreed_strict"] == "yes"
    assert row["human_check"] == "keep"


def test_unbound_legacy_keep_preserves_the_earlier_correction_verdict():
    earlier = {
        "m01": {"verdict": "fix", "number": "7654321"},
        "m02": {"verdict": "keep", "number": ""},
    }
    new = {"m01": {"verdict": "keep", "number": ""}}
    assert review.merge(earlier, new) == {
        "m01": {"verdict": "fix", "number": "7654321"},
        "m02": {"verdict": "keep", "number": ""},
    }


@pytest.mark.parametrize("legacy_csv", [False, True])
@pytest.mark.parametrize("new_number", ["2222222", "", "NONE"])
def test_download_ingest_label_change_and_reload_reopens_a_bare_keep(
    tmp_path, monkeypatch, legacy_csv, new_number
):
    reader = {
        "id": "m01",
        "meter_number": "1111111",
        "other_numbers": "",
        "number_sure": "sure",
        "class_label": "NONE",
        "class_sure": "",
        "notes": "",
    }
    source = {
        "id": "m01",
        "title": "t",
        "page_url": "p",
        "image_url": "i",
        "license": "CC0",
        "license_url": "",
        "author": "a",
    }
    for name in ("labels_reader1.csv", "labels_reader2a.csv", "labels_reader2b.csv"):
        write(tmp_path / name, [reader])
    write(tmp_path / "src.csv", [source])
    write(tmp_path / "clean_per_image.csv", [{"id": "m01", "number_box": ""}])
    (tmp_path / "images").mkdir()
    Image.new("RGB", (40, 30), "white").save(tmp_path / "images" / "m01.jpg")
    human = tmp_path / "labels_human.csv"
    monkeypatch.setattr(labels, "READER1", tmp_path / "labels_reader1.csv")
    monkeypatch.setattr(labels, "READER2", [tmp_path / "labels_reader2a.csv"])
    monkeypatch.setattr(labels, "SOURCES", tmp_path / "src.csv")
    monkeypatch.setattr(labels, "HUMAN", human)
    for name, value in {
        "DATA_DIR": tmp_path,
        "RESULTS_DIR": tmp_path,
        "REVIEW_DIR": tmp_path / "review",
        "MANIFEST": tmp_path / "manifest.csv",
        "HUMAN_LABELS": human,
    }.items():
        monkeypatch.setattr(review, name, value)

    def reload():
        [row] = labels.build()
        with (tmp_path / "manifest.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=labels.FIELDS, restval="")
            writer.writeheader()
            writer.writerow(row)
        review.build()
        page = (tmp_path / "review" / "review.html").read_text()
        items = json.loads(re.search(r'id="items">(.*?)</script>', page, re.DOTALL).group(1))
        return row, items

    _, items = reload()
    stored = {"m01": {**keep_answer(None, items[0]), "shown": items[0]["number"]}}
    rows = on_page("answerRows(items, stored)", items=items, stored=stored)
    assert rows == [
        ["id", "verdict", "number", "shown", "shown_known"],
        ["m01", "keep", "", "1111111", "yes"],
    ]
    if legacy_csv:
        rows = [row[:3] for row in rows]
    download = tmp_path / "answers.csv"
    with download.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    review.ingest(str(download))
    imported = review.read_answers(human)["m01"]
    assert imported.get("shown", "") == ("" if legacy_csv else "1111111")
    if not legacy_csv:
        before, items = reload()
        assert before["human_check"] == "keep"
        assert on_page("startingAnswers(items, {})", items=items) == {"m01": imported}

    write(tmp_path / "labels_reader1.csv", [{**reader, "meter_number": new_number}])
    after, items = reload()
    assert items[0]["number"] == ("" if new_number == "NONE" else new_number)
    assert after.get("human_check", "") == ""
    assert after.get("number_agreed", "no") == after.get("number_agreed_strict", "no") == "no"
    # Both a fresh browser and the stale original browser must reopen the changed label.
    assert on_page("startingAnswers(items, {})", items=items) == {}
    assert on_page("startingAnswers(items, stored)", items=items, stored=stored) == {}
    assert on_page("answerRows(items, startingAnswers(items, stored))", items=items, stored=stored)[
        1
    ] == [
        "m01",
        "",
        "",
        "",
        "",
    ]


def test_a_keep_for_a_different_shown_number_does_not_inherit_an_earlier_correction():
    old = {"m01": {"verdict": "fix", "number": "1111111"}}
    new = {"m01": {"verdict": "keep", "number": "", "shown": "2222222"}}
    assert review.merge(old, new) == new


@pytest.mark.parametrize("legacy_csv", [False, True])
def test_csv_distinguishes_a_reviewed_empty_label_from_missing_binding(
    tmp_path, monkeypatch, legacy_csv
):
    items = [{"id": "m01", "number": "", "corrected": False, "answer": None}]
    kept = {"m01": {"verdict": "keep", "number": "", "shown": ""}}
    rows = on_page("answerRows(items, kept)", items=items, kept=kept)
    if legacy_csv:
        rows = [row[:3] for row in rows]
    download = tmp_path / "download.csv"
    with download.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    human = tmp_path / "human.csv"
    monkeypatch.setattr(review, "HUMAN_LABELS", human)
    review.ingest(str(download))
    imported = review.read_answers(human)
    items[0]["answer"] = imported["m01"]
    if legacy_csv:
        assert "shown" not in imported["m01"]
        assert on_page("startingAnswers(items, {})", items=items) == {}
    else:
        assert imported == kept
        assert on_page("startingAnswers(items, {})", items=items) == kept
    # Writing and re-ingesting the human file must not invent a binding on a second pass.
    review.ingest(str(human))
    assert review.read_answers(human) == imported


def test_a_bound_keep_of_the_prior_correction_retains_its_label_binding():
    old = {"m01": {"verdict": "fix", "number": "1111111"}}
    new = {"m01": {"verdict": "keep", "number": "", "shown": "1111111"}}
    assert review.merge(old, new) == {
        "m01": {"verdict": "keep", "number": "1111111", "shown": "1111111"}
    }
