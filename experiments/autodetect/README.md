# Autodetect: can the phone propose windows and doors?

Today the homeowner draws each window box and taps its corners. This experiment asks which detector could propose windows and doors for the homeowner to confirm, how fast it runs, and whether a detected box, lifted onto the wall, is accurate enough to replace the taps.

## Pass criteria (fixed before any scored run)

1. **Proposals.** A candidate is good enough to propose window or door when, at its operating threshold, recall is at least 80% and precision at least 60% at IoU 0.5. The threshold is chosen per class on a 150-image Open Images tune set, then applied unchanged to each held-out set: 300 Open Images images and the 378 CMP Facade base images. The verdict is given per set. Open Images scoring follows its own rules for unverified classes and group-of boxes (`autodetect/oieval.py`). Both sets differ from the app's view: they show whole buildings from a distance, and CMP's facades are frontal and rectified. The app sees one wall at an angle from 1 to 3 m.
2. **Speed class**, from Mac (M4 Pro) timings: live (at most 100 ms a frame), keyframe (at most 1 s), or offline (over 1 s).
3. **3D extent.** A detected edge lifted to the wall replaces a tap when its p90 error is at most 0.5 ft. Ground truth is each door's or window's edges measured once in the ETH3D electro laser scan. Separately, the spread of one object's lifted edges across photos is reported.

Prompts, thresholds and detector settings are in `autodetect/config.py`.

## Rerun

Needs about 300 MB in `~/house-scanning-data/autodetect/`, plus one model's weights at a time (up to 360 MB) and 230 MB of Create ML checkpoints while the student trains. The 3D step needs the ETH3D electro packet and scan from the evals lane. `results/resources.md` has the time and memory of each step.

```sh
uv sync && uv run pytest
uv run python -m autodetect.openimages select && uv run python -m autodetect.openimages download
curl --fail --location https://cmp.felk.cvut.cz/~tylecr1/facade/CMP_facade_DB_base.zip -o /tmp/CMP_facade_DB_base.zip
mkdir -p ~/house-scanning-data/autodetect/cmp
unzip /tmp/CMP_facade_DB_base.zip -d ~/house-scanning-data/autodetect/cmp
uv run python -m autodetect.cmp
uv run python -m autodetect.sets                              # downscale the installed electro packet photos
swift build -c release --package-path vision   # copy rects, coremldet, trainod to ~/house-scanning-data/autodetect/bin
uv run python -m autodetect.run_vision oi_tune oi_eval cmp electro
uv run python -m autodetect.owl oi_tune oi_eval cmp electro      # needs weights/owlv2/model_fp16.onnx, then delete it
uv run python -m autodetect.student prepare && uv run python -m autodetect.student train transfer 1000
uv run python -m autodetect.student crop transfer                # tune set only; scaleFill won
uv run python -m autodetect.student predict transfer scaleFill
uv run python -m autodetect.score                                # results/proposals.md
uv run python -m autodetect.extent_gt save && uv run python -m autodetect.extent   # results/extent.md
```

Written but not run to completion, for lack of time on the shared Mac: `autodetect.gdino` (Grounding DINO tiny) and `autodetect.dfine` (D-FINE small fine-tune). Each module's docstring says why.

## Result

No candidate passes, so the app should not propose walls' windows and doors from a model trained on public photos. [PLAN.md](PLAN.md) has the recommendation, the confirm flow and the integration plan.

| Candidate | Size, Mac speed | Window P / R, Open Images | Door P / R, Open Images | Door edges lifted, p90 |
|---|---|---|---|---|
| OWLv2, zero-shot | 308 MB, 481 ms (GPU) | 55% / 23% | 75% / 58% | 1.62 ft |
| Create ML student | 6.8 MB, 50 ms | 56% / 7% | 53% / 8% | no door matched |
| Vision rectangles | in the OS, 12 ms | 6.5% / 15% | – | 1.18 ft |

CMP Facade gives the same verdict (`results/proposals.md`). Lifting a true door box onto the laser-scanned wall is off by 0.11 ft p90 from the box's inner corners and 0.59 ft from its side midpoints, so the lifting step works and the boxes are what fail (`results/extent.md`). The public photos show whole buildings from far away; counting only large objects, the student's window AP50 rises from 9% to 38%.

**Meter brand.** Vision's lines plus a list of meter makers (`meter_brand/`) name the brand on 17 of 19 photos where it is printed in plain letters and 22 of 42 where it is part of a logo, and name nothing on the 9 without one. No pass bar was set before this run. Details and the post-run changes are in `meter_brand/README.md`.
