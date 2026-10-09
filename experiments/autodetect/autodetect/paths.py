"""Where things live. Code and results are in git; images, weights and predictions are not.

Two roots. In git, under HERE (experiments/autodetect): RESULTS (scored reports, results/*.md)
and MANIFESTS (provenance CSVs: image_id, split, source_url, license, author). Outside git,
under Path.home(): DATA (~/house-scanning-data/autodetect), holding OI (Open Images images per
split, gt_*.json ground truth, selection metadata), CMP (CMP Facade images and gt.json),
WEIGHTS (model weights) and PREDS (the prediction cache, see autodetect/sets.py). ELECTRO is
the ETH3D electro packet (photos, poses, depth) at ~/house-scanning-data/packets/eth3d-electro.

Nothing here creates the directories. Readers raise FileNotFoundError until the step that owns
them has run: openimages.py downloads OI and writes its ground truth and manifests, cmp.py
writes CMP's gt.json, the model modules fill PREDS through save_preds.

>>> HERE.parts[-2:]
('experiments', 'autodetect')
>>> DATA.parts[-2:]
('house-scanning-data', 'autodetect')
>>> PREDS.parts[-2:]
('autodetect', 'preds')
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # experiments/autodetect
RESULTS = HERE / "results"
MANIFESTS = HERE / "manifests"

# Outside git: downloaded images, weights, cached predictions on real images.
DATA = Path.home() / "house-scanning-data" / "autodetect"
OI = DATA / "oi"
CMP = DATA / "cmp"
WEIGHTS = DATA / "weights"
PREDS = DATA / "preds"

ELECTRO = Path.home() / "house-scanning-data" / "packets" / "eth3d-electro"
