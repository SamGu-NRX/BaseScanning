import os
from pathlib import Path

EXPERIMENT_DIR = Path(__file__).resolve().parents[2]
MANIFEST = EXPERIMENT_DIR / "manifest.csv"
RESULTS_DIR = EXPERIMENT_DIR / "results"
# Images and raw recognizer output stay outside the repository.
DATA_DIR = Path(os.environ.get("METER_DATA", Path.home() / "house-scanning-data" / "meter"))
# The label review page shows plaintext meter numbers, so it lives outside the repository too.
REVIEW_DIR = DATA_DIR.parent / "meter-closeup"
# The key for the meter-number digests in manifest.csv; never committed.
KEY_PATH = REVIEW_DIR / "hmac.key"
