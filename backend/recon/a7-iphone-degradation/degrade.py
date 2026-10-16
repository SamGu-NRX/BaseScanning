"""Turn ETH3D DSLR photos into what a regular (non-Pro, no LiDAR) iPhone's ARKit capture hands the server.

    python degrade.py <scene> <condition> [<condition> ...]      # writes ~/a7/work/<scene>/<cond>/

Target: iPhone 15 main wide camera (26 mm equivalent, f/1.6), ARKit `capturedImage` at 1920 x 1440 (4:3),
saved as JPEG quality 0.9 (docs/capture-packet-spec.md). Every condition starts from `base`:

  base    centred crop of the undistorted DSLR frame to the iPhone's field of view (f = 1442 px at 1920 px,
          67.3 x 53.0 deg), area-resampled to 1920 x 1440, a small-lens/demosaic MTF (Gaussian sigma 0.6 px),
          JPEG q90. Pinhole intrinsics are exact for the saved pixels.

and adds one or more stages, applied in physical order (linear light: motion blur, rolling shutter,
sensor noise; then ISP: denoise, tone map, sharpen; then encode). Per-frame hand motion is one random
draw per image (seeded by scene + image name), shared by every condition, so blur and rolling shutter
in a combined condition come from the same angular velocity.

Assumptions (not measured on a phone; see A7 report section 2):
  - Sensor: 12 MP-equivalent 2.0 um binned pixels, full well 10 000 e- at ISO 50, read noise 2.5 e-;
    each 1920 px output pixel integrates 4.41 of them (optimistic if ARKit's video mode skips lines).
  - Colour correction amplifies noise (matrix applied to the noise only).
  - ISP denoise: non-local means, strength tied to the expected noise level (chroma much stronger).
  - Rolling-shutter readout 16 ms top to bottom. Hand motion: angular rate magnitude lognormal
    (median = nominal, sigma 0.5), direction biased horizontal (pitch 0.5x yaw), roll ignored.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ETH3D = Path.home() / "a2/data/eth3d"
WORK = Path.home() / "a7/work"
OUT_W, OUT_H = 1920, 1440
F35 = 26.0
F_OUT = F35 * np.hypot(OUT_W, OUT_H) / np.hypot(36.0, 24.0)  # 1442 px
MTF_SIGMA = 0.6
FWC_ISO50 = 10000.0 * (4032 / OUT_W) ** 2  # electrons per output pixel (all channels) at ISO 50
READ_E = 2.5 * (4032 / OUT_W)  # read noise per output pixel, electrons
CHANNEL_SHARE = np.array([0.25, 0.5, 0.25])  # Bayer R, G, B
CCM = np.array([[1.6, -0.4, -0.2], [-0.3, 1.6, -0.3], [-0.1, -0.5, 1.6]])
READOUT_S = 0.016


@dataclass
class Cond:
    iso: float | None = None          # sensor noise at this ISO (None = no added noise)
    exposure_s: float | None = None   # exposure for motion blur (with rate_dps)
    rate_dps: float | None = None     # nominal hand angular rate, deg/s (blur and/or rolling shutter)
    blur_px: float | None = None      # direct median blur length at 1920 px (blur-only sweeps)
    rolling: bool = False             # apply rolling shutter from rate_dps
    hdr: bool = False                 # Smart-HDR-like local tone map + sharpening
    codec: str = "jpeg"
    quality: int = 90
    note: str = ""
    extra: dict = field(default_factory=dict)


# Illuminance on the wall -> exposure at f/1.6 with ARKit's 60 fps cap (t <= 1/60 s):
# t * ISO = N^2 * 100 / 2^EV100, EV100 = log2(lux / 2.5).
CONDITIONS: dict[str, Cond] = {
    "base": Cond(note="iPhone FOV + 1920x1440 + lens MTF + JPEG q90"),
    # sensor noise (+ the ISP denoise the phone would apply), no motion
    "noise_iso50": Cond(iso=50, note="daylight, ~10 000 lux, 1/1000 s"),
    "noise_iso400": Cond(iso=400, note="sunset / heavy overcast, ~200 lux at 1/60 s"),
    "noise_iso1600": Cond(iso=1600, note="dusk, ~25 lux at 1/60 s"),
    "noise_iso6400": Cond(iso=6400, note="late dusk, ~6 lux at 1/60 s (beyond analogue ISO: underexposed + gain)"),
    # walking motion blur, median length at 1920 px (lognormal spread per frame)
    "blur_4": Cond(blur_px=4, note="1/60 s at ~10 deg/s (slow, steady walk)"),
    "blur_8": Cond(blur_px=8, note="1/60 s at ~19 deg/s (normal walk)"),
    "blur_16": Cond(blur_px=16, note="1/60 s at ~38 deg/s or 1/30 s at ~19 deg/s"),
    "blur_32": Cond(blur_px=32, note="1/30 s at ~38 deg/s (walk + pan at dusk)"),
    # compression
    "jpeg_q50": Cond(quality=50, note="JPEG q50"),
    "jpeg_q25": Cond(quality=25, note="JPEG q25"),
    "heic_q30": Cond(codec="heic", quality=30, note="HEIC q30 (x265), decoded"),
    # ISP look
    "hdr_sharp": Cond(hdr=True, note="Smart-HDR-like local tone map + unsharp mask"),
    # rolling shutter (16 ms readout)
    "rs_30dps": Cond(rate_dps=30, rolling=True, note="walk + gentle pan, ~12 px median skew"),
    "rs_90dps": Cond(rate_dps=90, rolling=True, note="fast pan, ~36 px median skew"),
    # combined, physically consistent: exposure from light, blur and skew from the same hand motion
    "combo_day": Cond(iso=50, exposure_s=1 / 1000, rate_dps=30, rolling=True, hdr=True,
                      note="daylight walk: ISO 50, 1/1000 s, 30 deg/s, HDR+sharpen, RS, q90"),
    "combo_dusk": Cond(iso=1600, exposure_s=1 / 60, rate_dps=30, rolling=True, hdr=True,
                       note="dusk walk: ISO 1600, 1/60 s, 30 deg/s, HDR+sharpen, RS, q90"),
    "combo_dusk_slow": Cond(iso=1600, exposure_s=1 / 60, rate_dps=10, rolling=True, hdr=True,
                            note="dusk, slow deliberate walk: ISO 1600, 1/60 s, 10 deg/s"),
    "combo_night": Cond(iso=6400, exposure_s=1 / 30, rate_dps=30, rolling=True, hdr=True,
                        note="late dusk walk: ISO 6400-eq, 1/30 s, 30 deg/s"),
}


def srgb_to_lin(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def rng_for(scene: str, name: str, stage: str) -> np.random.Generator:
    h = hashlib.sha256(f"{scene}/{name}/{stage}".encode()).hexdigest()
    return np.random.default_rng(int(h[:12], 16))


def hand_motion(scene: str, name: str) -> tuple[float, np.ndarray]:
    """Unit-free per-frame draw: magnitude multiplier (lognormal, median 1) and image-plane direction."""
    r = rng_for(scene, name, "motion")
    mag = float(np.exp(r.normal(0, 0.5)))
    phi = r.uniform(0, 2 * np.pi)
    return mag, np.array([np.cos(phi), 0.5 * np.sin(phi)])


def base_frame(scene: str, name: str, K: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """DSLR (undistorted) -> iPhone FOV crop -> 1920x1440 linear RGB float, and its [fx, fy, cx, cy]."""
    """K and the returned intrinsics use COLMAP's convention (pixel corners at integers)."""
    img = cv2.imread(str(ETH3D / scene / "images/dslr_images_undistorted" / name), cv2.IMREAD_COLOR)
    fx, fy, cx, cy = K
    sx, sy = F_OUT / fx, F_OUT / fy  # output px per source px
    cw, ch = OUT_W / sx, OUT_H / sy
    x0, y0 = cx - cw / 2, cy - ch / 2
    assert x0 >= 0 and y0 >= 0 and x0 + cw <= img.shape[1] and y0 + ch <= img.shape[0], (name, x0, y0)
    # Area-like downsample: pre-blur by the scale factor, then affine-resample (OpenCV pixel centres).
    sig = 0.5 * np.sqrt(max((1 / sx) ** 2 - 1, 0))
    src = cv2.GaussianBlur(img, (0, 0), sig) if sig > 0.3 else img
    M = np.array([[sx, 0, sx * (0.5 - x0) - 0.5], [0, sy, sy * (0.5 - y0) - 0.5]], np.float64)
    out = cv2.warpAffine(src, M, (OUT_W, OUT_H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    lin = srgb_to_lin(out[..., ::-1].astype(np.float32) / 255.0)
    lin = cv2.GaussianBlur(lin, (0, 0), MTF_SIGMA)
    return lin, np.array([F_OUT, F_OUT, OUT_W / 2, OUT_H / 2])


def motion_blur(lin: np.ndarray, vec: np.ndarray) -> np.ndarray:
    L = float(np.linalg.norm(vec))
    if L < 0.5:
        return lin
    n = int(np.ceil(L)) * 2 + 3
    k = np.zeros((n, n), np.float32)
    c = n // 2
    for t in np.linspace(-0.5, 0.5, max(int(L * 4), 2)):
        x, y = c + t * vec[0], c + t * vec[1]
        x0, y0 = int(np.floor(x)), int(np.floor(y))
        fx, fy = x - x0, y - y0
        for dx, dy, w in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
            k[y0 + dy, x0 + dx] += w
    k /= k.sum()
    return cv2.filter2D(lin, -1, k, borderType=cv2.BORDER_REFLECT)


def rolling_shutter(lin: np.ndarray, vel_px_s: np.ndarray) -> np.ndarray:
    """Row y is exposed at tau(y) = READOUT_S * (y / H - 0.5); the pose timestamp is mid-frame."""
    h, w = lin.shape[:2]
    tau = READOUT_S * (np.arange(h, dtype=np.float32) / h - 0.5)
    mx = np.arange(w, dtype=np.float32)[None, :] + (vel_px_s[0] * tau)[:, None]
    my = np.arange(h, dtype=np.float32)[:, None] + (vel_px_s[1] * tau)[:, None] + 0 * mx
    return cv2.remap(lin, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def sensor_noise(lin: np.ndarray, iso: float, r: np.random.Generator) -> tuple[np.ndarray, float]:
    """Poisson-Gaussian noise per Bayer channel, colour-correction amplification of the noise.
    Returns the noisy image and the expected mid-grey (18 %) luma noise in 8-bit sRGB units."""
    gain = 50.0 / iso
    e_sat = FWC_ISO50 * gain * CHANNEL_SHARE  # electrons at saturation per channel
    read = READ_E * np.sqrt(CHANNEL_SHARE)
    e = np.clip(lin, 0, 1) * e_sat
    noisy = r.poisson(e).astype(np.float32) + r.normal(0, 1, e.shape).astype(np.float32) * read
    dev = noisy / e_sat - lin
    dev = dev @ CCM.T.astype(np.float32)
    out = lin + dev
    # expected luma sigma at 18 % grey, in sRGB 8-bit
    e18 = 0.18 * e_sat
    sig_lin = np.sqrt((e18 + read**2)) / e_sat
    sig_lin = float(np.sqrt(((CCM**2) @ (sig_lin**2)).mean()))
    slope = (lin_to_srgb(0.18 + 1e-3) - lin_to_srgb(0.18)) / 1e-3
    return out, float(sig_lin * slope * 255)


def isp_denoise(srgb8: np.ndarray, sigma8: float) -> np.ndarray:
    if sigma8 < 0.8:
        return srgb8
    h_l = float(np.clip(0.9 * sigma8, 1, 30))
    h_c = float(np.clip(2.0 * sigma8, 2, 40))
    return cv2.fastNlMeansDenoisingColored(srgb8, None, h_l, h_c, 5, 13)


def hdr_tone_sharpen(lin: np.ndarray) -> np.ndarray:
    """Local tone mapping (log-luma base/detail split with a wide Gaussian base -> phone-like halos),
    shadow lift, highlight compression, then luma unsharp mask."""
    Y = np.clip(lin @ np.array([0.2126, 0.7152, 0.0722], np.float32), 1e-5, None)
    logY = np.log2(Y)
    small = cv2.resize(logY, (OUT_W // 8, OUT_H // 8), interpolation=cv2.INTER_AREA)
    base = cv2.resize(cv2.GaussianBlur(small, (0, 0), 4.0), (OUT_W, OUT_H), interpolation=cv2.INTER_LINEAR)
    anchor = float(np.median(base))
    out_log = anchor + 0.6 * (base - anchor) + 1.25 * (logY - base) + 0.25  # compress range, boost detail
    Y2 = np.power(2.0, out_log)
    Y2 = Y2 / (1 + 0.35 * Y2)  # soft highlight roll-off
    Y2 = Y2 * (1.35)
    ratio = (Y2 / Y)[..., None]
    out = np.clip(lin * ratio, 0, 1)
    s = lin_to_srgb(out).astype(np.float32)
    ys = s @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    blur = cv2.GaussianBlur(ys, (0, 0), 1.2)
    detail = ys - blur
    s = s + (0.8 * detail)[..., None]
    return np.clip(s, 0, 1)


def encode(srgb: np.ndarray, cond: Cond, path_stem: Path) -> Path:
    bgr8 = np.clip(np.round(srgb[..., ::-1] * 255), 0, 255).astype(np.uint8)
    if cond.codec == "jpeg":
        p = path_stem.with_suffix(".jpg")
        cv2.imwrite(str(p), bgr8, [cv2.IMWRITE_JPEG_QUALITY, cond.quality])
        return p
    import pillow_heif  # noqa: PLC0415  (optional codec, only for HEIC conditions)
    from PIL import Image  # noqa: PLC0415

    heif = pillow_heif.from_pillow(Image.fromarray(bgr8[..., ::-1]))
    tmp = path_stem.with_suffix(".heic")
    heif.save(str(tmp), quality=cond.quality)
    dec = np.asarray(pillow_heif.open_heif(str(tmp)).to_pillow().convert("RGB"))
    p = path_stem.with_suffix(".png")
    cv2.imwrite(str(p), dec[..., ::-1])
    return p


def read_cameras(scene: str) -> tuple[dict, dict]:
    d = ETH3D / scene / "dslr_calibration_undistorted"
    cams = {}
    for ln in (d / "cameras.txt").read_text().splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        p = ln.split()
        cams[int(p[0])] = np.array(p[4:8], float)
    imgs = {}
    lines = [ln for ln in (d / "images.txt").read_text().splitlines() if not ln.startswith("#")]
    for i in range(0, len(lines), 2):
        p = lines[i].split()
        if len(p) < 10:
            continue
        imgs[Path(p[9]).name] = {"camera_id": int(p[8]), "q": [float(x) for x in p[1:5]], "t": [float(x) for x in p[5:8]]}
    return cams, imgs


def make_dslr_ref(scene: str, out: Path) -> Path:
    """A1/A2's original input: the full DSLR field of view at 2048 px wide, JPEG q95."""
    cams, imgs = read_cameras(scene)
    frames = {}
    for name in sorted(imgs):
        img = cv2.imread(str(ETH3D / scene / "images/dslr_images_undistorted" / name))
        h, w = img.shape[:2]
        W, H = 2048, int(round(h * 2048 / w))
        cv2.imwrite(str(out / "images" / (Path(name).stem + ".jpg")), cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA),
                    [cv2.IMWRITE_JPEG_QUALITY, 95])
        fx, fy, cx, cy = cams[imgs[name]["camera_id"]]
        frames[name] = {"file": Path(name).stem + ".jpg", "K": [fx * W / w, fy * H / h, cx * W / w, cy * H / h], "w": W, "h": H}
    (out / "frames.json").write_text(json.dumps({"scene": scene, "condition": "dslr_ref", "frames": frames}, indent=1))
    return out


def make(scene: str, cname: str) -> Path:
    out = WORK / scene / cname
    done = out / "frames.json"
    if done.exists():
        return out
    (out / "images").mkdir(parents=True, exist_ok=True)
    if cname == "dslr_ref":
        return make_dslr_ref(scene, out)
    cond = CONDITIONS[cname]
    cams, imgs = read_cameras(scene)
    frames = {}
    for name in sorted(imgs):
        lin, K = base_frame(scene, name, cams[imgs[name]["camera_id"]])
        mag, d = hand_motion(scene, name)
        meta: dict = {}
        rate = None
        if cond.rate_dps is not None:
            rate = np.radians(cond.rate_dps) * mag * F_OUT * d  # px/s in the image, [x, y]
        if cond.blur_px is not None:
            vec = cond.blur_px * mag * d
            lin = motion_blur(lin, vec)
            meta["blur_px"] = float(np.linalg.norm(vec))
        elif cond.exposure_s is not None and rate is not None:
            vec = rate * cond.exposure_s
            lin = motion_blur(lin, vec)
            meta["blur_px"] = float(np.linalg.norm(vec))
        if cond.rolling and rate is not None:
            lin = rolling_shutter(lin, rate)
            meta["rs_skew_px"] = float(np.linalg.norm(rate) * READOUT_S)
        sigma8 = 0.0
        if cond.iso is not None:
            lin, sigma8 = sensor_noise(lin, cond.iso, rng_for(scene, name, f"noise{cond.iso}"))
            meta["iso"], meta["noise_sigma8_mid"] = cond.iso, sigma8
        if sigma8 > 0:
            srgb8 = np.clip(np.round(lin_to_srgb(lin) * 255), 0, 255).astype(np.uint8)
            lin = srgb_to_lin(isp_denoise(srgb8, sigma8).astype(np.float32) / 255.0)
        if cond.hdr:
            srgb = hdr_tone_sharpen(np.clip(lin, 0, None))
        else:
            srgb = lin_to_srgb(lin).astype(np.float32)
        p = encode(srgb, cond, out / "images" / Path(name).stem)
        frames[name] = {"file": p.name, "K": K.tolist(), "w": OUT_W, "h": OUT_H, **meta}
    done.write_text(json.dumps({"scene": scene, "condition": cname, "cond": cond.__dict__, "frames": frames}, indent=1))
    return out


if __name__ == "__main__":
    sc = sys.argv[1]
    for c in sys.argv[2:]:
        print(make(sc, c), flush=True)
