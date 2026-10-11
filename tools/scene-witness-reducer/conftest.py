"""Make the tool package importable from the tests without installing anything:
the reducer is a flat directory of modules, not a distribution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
