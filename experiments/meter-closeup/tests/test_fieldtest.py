import io
import sys

import numpy as np
from PIL import Image

from meter_eval import fieldtest, match, retake
from meter_eval.fieldtest import upright_pixels
from meter_eval.quality import gray

BOX = [0.1, 0.1, 0.7, 0.2]  # 51 px tall on 256 px: passes the line-height check


def stripes_jpeg(path) -> None:
    """A low-contrast stripe photo, stored as a quality 90 JPEG, just below the focus cut."""
    x = np.arange(256)
    row = np.round(128 + 13 * np.sin(2 * np.pi * x / 12)).astype(np.uint8)
    Image.fromarray(np.tile(row, (256, 1))).convert("RGB").save(path, quality=90)


def vision_result(*texts) -> dict:
    """A synthetic Vision result: one line per text, each on its own row, equal heights."""
    return {
        "lines": [
            {"text": text, "box": [0.1, 0.1 + 0.3 * i, 0.7, 0.2]} for i, text in enumerate(texts)
        ],
        "barcodes": [],
    }


class FakeReader:
    """Stands in for ocr.Reader, handing out one prepared result per read, in call order."""

    def __init__(self, results):
        self.results = list(results)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, image, barcodes):
        return self.results.pop(0)


def test_checks_run_on_the_photos_own_pixels(tmp_path):
    photo = tmp_path / "stripes.jpg"
    stripes_jpeg(photo)
    with Image.open(photo) as original:
        own = gray(original.convert("RGB"))
    measured = gray(upright_pixels(photo, tmp_path))
    assert np.array_equal(measured, own)
    assert retake.whole_photo_sharpness(measured) < retake.MIN_SHARPNESS
    assert retake.reasons(measured, BOX) == ["out of focus"]


def test_a_jpeg_round_trip_would_have_passed_the_same_photo(tmp_path):
    # Why the command never re-encodes: the old quality 95 copy crossed the threshold.
    photo = tmp_path / "stripes.jpg"
    stripes_jpeg(photo)
    copy = io.BytesIO()
    upright_pixels(photo, tmp_path).save(copy, format="JPEG", quality=95)
    recompressed = gray(Image.open(copy).convert("RGB"))
    assert retake.whole_photo_sharpness(recompressed) > retake.MIN_SHARPNESS
    assert retake.reasons(recompressed, BOX) == []


def test_the_copy_vision_reads_holds_the_same_pixels(tmp_path):
    photo = tmp_path / "stripes.jpg"
    stripes_jpeg(photo)
    image = upright_pixels(photo, tmp_path)
    image.save(tmp_path / "upright.png")
    with Image.open(tmp_path / "upright.png") as saved:
        assert np.array_equal(np.asarray(saved.convert("RGB")), np.asarray(image))


def test_the_orientation_tag_is_applied(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    Image.new("RGB", (300, 100), "white").save(tmp_path / "turned.jpg", exif=exif.tobytes())
    assert upright_pixels(tmp_path / "turned.jpg", tmp_path).size == (100, 300)


def test_main_runs_end_to_end_without_the_hmac_key(tmp_path, monkeypatch, capsys):
    # The field test compares plain strings in memory, so it must run where no key exists.
    monkeypatch.delenv("METER_HMAC_KEY", raising=False)
    monkeypatch.setattr(match, "KEY_PATH", tmp_path / "no-such-key")
    match.key.cache_clear()
    folder = tmp_path / "photos"
    folder.mkdir()
    stripes_jpeg(folder / "one.jpg")
    reader = FakeReader([vision_result("NO. 12 345 678")])
    monkeypatch.setattr(fieldtest, "Reader", lambda: reader)
    monkeypatch.setattr(sys, "argv", ["fieldtest", str(folder), "--number", "12 345 678"])
    fieldtest.main()
    out = capsys.readouterr().out
    assert "| one.jpg | yes | 1 | yes |" in out
    assert "1 read the number exactly" in out
    assert "1 with the correct number in the top three candidates" in out


def test_the_summary_counts_the_top_three_separately(tmp_path, monkeypatch, capsys):
    # The app shows the homeowner the top three candidates, so rank 2 counts there, in a
    # metric of its own next to the exact reads.
    folder = tmp_path / "photos"
    folder.mkdir()
    stripes_jpeg(folder / "one.jpg")
    # Keyword line: 3 plus 2 plus 1. Bare number: 2 plus 1. All zeros: minus 6 plus 2 plus 1.
    reader = FakeReader([vision_result("NO. 99999999", "12345678", "00000000")])
    monkeypatch.setattr(fieldtest, "Reader", lambda: reader)
    monkeypatch.setattr(sys, "argv", ["fieldtest", str(folder), "--number", "12345678"])
    fieldtest.main()
    out = capsys.readouterr().out
    assert "| yes | 2 | no |" in out  # rank 2, so not the top candidate
    assert "1 with the correct number in the top three candidates" in out
    assert "1 read the number exactly" in out
