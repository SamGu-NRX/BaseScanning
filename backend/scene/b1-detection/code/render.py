"""Draw detections or labels on an image for visual checks and labelling."""

from PIL import ImageDraw, ImageFont

from common import CLASS_NAMES

COLORS = {
    "electric_meter": (255, 0, 0),
    "meter_can": (255, 140, 0),
    "main_panel": (255, 0, 255),
    "gas_meter": (255, 255, 0),
    "ac_condenser": (0, 255, 255),
    "window": (0, 120, 255),
    "door": (0, 200, 0),
    "downspout": (160, 82, 45),
    "hose_bib": (0, 255, 120),
    "fence": (200, 200, 200),
    "pool": (0, 0, 180),
    "driveway": (128, 128, 128),
}
SHORT = {c: s for c, s in zip(CLASS_NAMES, ["MTR", "CAN", "PNL", "GAS", "AC", "WIN", "DOOR", "DSP", "HOSE", "FEN", "POOL", "DRV"])}


def font(size):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def draw_boxes(img, boxes, numbered=False, grid=False, width=3):
    """boxes: list of dicts with label, box (normalised xyxy), optional score and id."""
    img = img.copy()
    d = ImageDraw.Draw(img)
    W, H = img.size
    f = font(max(12, W // 70))
    if grid:
        for k in range(1, 10):
            d.line([(W * k / 10, 0), (W * k / 10, H)], fill=(255, 255, 255), width=1)
            d.line([(0, H * k / 10), (W, H * k / 10)], fill=(255, 255, 255), width=1)
            d.text((W * k / 10 + 2, 2), f"{k/10:.1f}", fill=(255, 255, 0), font=f)
            d.text((2, H * k / 10 + 2), f"{k/10:.1f}", fill=(255, 255, 0), font=f)
    for i, b in enumerate(boxes):
        x0, y0, x1, y1 = b["box"]
        c = COLORS.get(b["label"], (255, 255, 255))
        d.rectangle([x0 * W, y0 * H, x1 * W, y1 * H], outline=c, width=width)
        tag = str(b.get("id", i)) if numbered else SHORT.get(b["label"], b["label"])
        if not numbered and "score" in b:
            tag += f" {b['score']:.2f}"
        elif numbered:
            tag += " " + SHORT.get(b["label"], b["label"])
        tx, ty = x0 * W + 2, max(0, y0 * H - f.size - 2)
        d.rectangle(d.textbbox((tx, ty), tag, font=f), fill=(0, 0, 0))
        d.text((tx, ty), tag, fill=c, font=f)
    return img
