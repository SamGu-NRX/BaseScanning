import csv
import json
import re
import subprocess

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
    assert rows[1] == ["m01", "keep", "7654321"]
    download = tmp_path / "answers.csv"
    with download.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    review.ingest(str(download))

    row = rebuild_manifest()
    assert row["number_hmac"] == labels.digest("7654321")
    assert row["number_agreed"] == row["number_agreed_strict"] == "yes"
    assert row["human_check"] == "keep"


def test_ingest_keeps_earlier_answers_and_a_bare_keep_keeps_the_correction():
    earlier = {
        "m01": {"verdict": "fix", "number": "7654321"},
        "m02": {"verdict": "keep", "number": ""},
    }
    new = {"m01": {"verdict": "keep", "number": ""}}
    assert review.merge(earlier, new) == {
        "m01": {"verdict": "keep", "number": "7654321"},
        "m02": {"verdict": "keep", "number": ""},
    }
