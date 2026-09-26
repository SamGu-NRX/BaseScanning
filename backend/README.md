# house-scanning-server

The server side of the Base Power battery site survey. The iOS app ([SamGu-NRX/house-scanning](https://github.com/SamGu-NRX/house-scanning)) walks a homeowner around their house and sends a capture packet; this repo decides, in one session, where a battery can go and what views are still missing.

## Pipeline

```
capture packet ──► metric 3D asset ──► labeled scene ──► criteria verdicts
 (photos + ARKit)     (recon/)          (scene/)          (criteria/)
                                                              │
                                                              └──► needed views, back to the iOS app
```

1. **Capture packet in** (`packet/`, spec in `docs/capture-packet-spec.md`). Photos plus what the iPhone and ARKit provide: camera pose per photo, lens intrinsics, gravity, IMU, compass, GPS, optional LiDAR depth, and user taps such as the meter anchor. Until real packets arrive, `packetgen` builds synthetic ones from real image sets with ARKit-like scale error and drift. Synthetic packets are always labeled `source.kind = "synthetic"` and are never evidence.
2. **Metric 3D asset** (`recon/`). One subfolder per candidate path: A1 classical SfM/MVS, A2 feed-forward multi-view, A3 metric depth plus fusion, A4 Gaussian splatting, A5 world models.
3. **Scene** (`scene/`). Walls, corners, ground, equipment and obstacles, plus what was and wasn't observed. B1 finds the criteria objects; B2 reads close-up equipment (meter, CL320 label, panel).
4. **Criteria verdicts plus needed views** (`criteria/`). Each criterion gets pass, fail or unsure with a reason; unseen means unsure. Paths: C1 structured rules, C2 volumetric fit, C3 vision-language judge. Thresholds live in `criteria/spec/criteria.yaml`, never in code.
5. **Evaluation** (`eval/`). One scene and verdict format every path writes, and shared scorers: error on criteria-relevant distances, completeness of the regions the criteria need, scale error, unsafe passes, agreement with hand labels, manual-review rate.

Every path gets the same inputs and the same scorer, so results are comparable. Real data comes first; every result is labeled real or synthetic.

## Layout

| Folder | Contents |
| --- | --- |
| `packet/` | Capture packet schema, examples and the synthetic packet generator (`packetgen`). |
| `recon/` | Track A: one subfolder per reconstruction path. |
| `scene/` | Track B: object detection and equipment reading. |
| `criteria/` | Track C paths; `criteria/spec/` holds thresholds and draft labels. |
| `eval/` | Scoring harness, scene/verdict/label schemas, ETH3D ground truth builders. |
| `data/` | Dataset scripts and `manifest.json` only. Frames and packets are not committed. |
| `docs/` | Capture packet spec and criteria spec. |
| `scripts/` | Repo tooling, including `sync_from_store.sh`. |

`packet/`, `eval/`, `criteria/spec/`, `data/scripts/`, `data/manifest.json` and the files in `docs/` are snapshots of the team's working store; see `CONTRIBUTING.md` before editing them.

## Setup

Needs [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest            # tests
uv run ruff check .      # lint
uv run ruff format .     # format
(cd packet && uv run python -m packetgen --help)
```
