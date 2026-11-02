"""Where the experiment's files live."""

from pathlib import Path

EXPERIMENT_DIR = Path(__file__).resolve().parents[2]
INPUTS_DIR = EXPERIMENT_DIR / "inputs"
IMAGES_DIR = INPUTS_DIR / "images"
OBSERVATIONS_DIR = INPUTS_DIR / "observations"
# Gold stays outside the reader input: the reader sees images and observation sets only.
GOLD_PATH = EXPERIMENT_DIR / "gold" / "labels.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"
