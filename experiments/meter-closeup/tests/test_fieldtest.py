import io

import numpy as np
import pytest
from PIL import Image

from meter_eval import retake
from meter_eval.fieldtest import upright_pixels
from meter_eval.quality import gray

BOX = [0.1, 0.1, 0.7, 0.2]  # 51 px tall on 256 px: passes the line-height check


def stripes_jpeg(path) -> None:
    """A low-contrast stripe photo, stored as a quality 90 JPEG, just below the focus cut."""
    x = np.arange(256)
    row = np.round(128 + 13 * np.sin(2 * np.pi * x / 12)).astype(np.uint8)
    Image.fromarray(np.tile(row, (256, 1))).convert("RGB").save(path, quality=90)


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


@pytest.mark.parametrize(
    "number, text, expected, expected_rank",
    [
        ("12-34567", "No. 1234567", "yes", 1),
        ("No. 1234567", "1234567", "yes", 1),
        ("1234567", "12345678", "no", None),
        ("1234567", "91234567", "no", None),
        ("1234567", "No. 123 4567", "yes", 1),
        ("1234567", "1234567 240 V", "yes", None),
        ("1234567", "123 4567 240 V", "yes", None),
        ("1234567", "1234567 8", "yes", None),
        ("1234567", "123 4567", "yes", 1),
        ("1234567", "1234567", "yes", 1),
    ],
)
def test_fieldtest_scores_reads_and_ranks_without_a_dataset_key(
    tmp_path, monkeypatch, capsys, number, text, expected, expected_rank
):
    import sys

    from meter_eval import fieldtest, match

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def read(self, path, barcodes):
            return {"lines": [{"text": text, "box": BOX}], "barcodes": []}

    def no_key():
        raise AssertionError("field test must not load the dataset key")

    Image.new("RGB", (256, 256), "white").save(tmp_path / "photo.jpg")
    monkeypatch.setattr(fieldtest, "Reader", Reader)
    monkeypatch.setattr(sys, "argv", ["fieldtest", str(tmp_path), "--number", number])
    with monkeypatch.context() as without_key:
        without_key.setattr(match, "key", no_key)
        fieldtest.main()
    output = capsys.readouterr().out
    assert f"| photo.jpg | {expected} | {expected_rank or '–'} |" in output
    assert f"1 photos; {int(expected == 'yes')} retakes asked for photos that read" in output


@pytest.mark.parametrize("text", ["123 V 4567", "123-4567", "12345678"])
def test_read_number_does_not_join_across_non_digits(text):
    from meter_eval.fieldtest import reads_number

    assert not reads_number(text, "1234567")
