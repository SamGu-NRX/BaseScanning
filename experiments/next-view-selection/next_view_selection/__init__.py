"""Next-view selection as a finite decision problem (see ../README.md)."""

from .model import StudySpec, spec_from_manifest
from .study import run_study

__all__ = ["StudySpec", "run_study", "spec_from_manifest"]
