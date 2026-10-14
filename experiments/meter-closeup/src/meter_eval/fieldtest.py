"""Score a folder of real close-ups against the retake checks and the number finder.

    uv run python -m meter_eval.fieldtest PHOTO_DIR --number "12 345 678"

For each photo: whether Vision read the number, where the number-finding ranking put it, the
retake check values and whether they would ask for a retake. Candidates and the retake box
come from the blind heuristic ranking; --number only scores the read and the number's rank.
The summary counts a retake asked for a photo that read (a wasted retake) and a photo
accepted that did not read (a re-request later). The meter number is taken from the command
line and is never written to disk.
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from meter_eval import retake
from meter_eval.locate import candidates, ranked, top_candidate
from meter_eval.match import match_serial, number_core
from meter_eval.ocr import Reader
from meter_eval.quality import gray

SUFFIXES = {".jpg", ".jpeg", ".png", ".heic"}


def reads_number(text: str | list[dict], target: str) -> bool:
    lines = [{"text": text, "box": [0, 0, 1, 1]}] if isinstance(text, str) else text
    return any(candidate["matches_target"] for candidate in match_serial(lines, target))


def upright_pixels(photo: Path, scratch: Path) -> Image.Image:
    """The photo's own decoded pixels, turned upright by its orientation tag.

    Never re-encoded lossily: a JPEG round trip smooths fine detail, and on a synthetic low
    contrast photo it lifted sharpness from 6.45 to 6.71, across the 6.68 retake threshold.
    PIL cannot decode HEIC, so macOS sips decodes it to PNG, which is lossless and keeps the
    orientation tag.
    """
    source = photo
    if photo.suffix.lower() == ".heic":
        source = scratch / "decoded.png"
        subprocess.run(
            ["sips", "-s", "format", "png", str(photo), "--out", str(source)],
            check=True,
            capture_output=True,
        )
    with Image.open(source) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("folder", type=Path)
    parser.add_argument("--number", required=True, help="the meter number as printed")
    args = parser.parse_args()

    target_core = number_core(args.number)
    if target_core is None:
        parser.error("--number contains an unsupported character in a serial group")
    if not target_core:
        parser.error("--number must contain letters or digits")
    photos = sorted(p for p in args.folder.iterdir() if p.suffix.lower() in SUFFIXES)
    if not photos:
        raise SystemExit(f"no .jpg, .png or .heic photos in {args.folder}")

    print(
        "| Photo | Read | Rank | Top candidate is the number | Top line px | Sharpness | "
        "Retake because |"
    )
    print("|---|---|---|---|---|---|---|")
    wasted = missed = 0
    # Working copies live in a private temporary folder, never in the photo folder, so no
    # file of the user's is overwritten, deleted or scored twice.
    with tempfile.TemporaryDirectory() as scratch, Reader() as reader:
        # Vision ignores the orientation tag, so it reads the upright pixels, saved as PNG
        # (lossless) so that it sees exactly the pixels the checks measure.
        upright = Path(scratch) / "upright.png"
        for photo in photos:
            image = upright_pixels(photo, Path(scratch))
            image.save(upright)
            g = gray(image)
            result = reader.read(upright, barcodes=True)
            read = reads_number(result["lines"], args.number)
            order = ranked(candidates(result))
            rank = next((i + 1 for i, c in enumerate(order) if c == target_core), None)
            guess = top_candidate(result)
            box = guess and guess["box"]
            why = retake.reasons(g, box)
            wasted += read and bool(why)
            missed += not read and not why
            height_px = f"{retake.line_height_px(box, g.shape[0]):.0f}" if box else "–"
            print(
                f"| {photo.name} | {'yes' if read else 'no'} | {rank or '–'} | "
                f"{'yes' if rank == 1 else 'no'} | {height_px} | "
                f"{retake.whole_photo_sharpness(g):.1f} | {', '.join(why) or '–'} |"
            )
    print(
        f"\n{len(photos)} photos; {wasted} retakes asked for photos that read; "
        f"{missed} photos accepted that did not read."
    )


if __name__ == "__main__":
    main()
