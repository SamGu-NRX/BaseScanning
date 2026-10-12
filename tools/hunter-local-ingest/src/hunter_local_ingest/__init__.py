"""hunter-local-ingest: published-client checks against a local capture API.

Drives the published HouseScanKit upload lifecycle against one endpoint,
labels how every check ran, and writes receipts that are validated,
screened, chained and sealed in a fresh directory per run.

Boundaries:
- The package talks to the capture API over HTTP only. No backend
  implementation, schema, spec, internal test, fixture or raw log from any
  private repository is vendored here.
- Every check names how it ran (Transport.REAL_API, FAKE_HUNTER or GPU_STUB)
  and how it ended (PASS, FAIL, BLOCKED, NOT_RUN). REAL_API records what the
  operator declared the endpoint to be; the harness cannot verify it.
"""

from .adapter import INTERFACE_VERSION, HunterIngestAdapter, StepResult
from .modes import Status, Transport
from .observations import Observation

__version__ = "0.2.0"

__all__ = [
    "INTERFACE_VERSION",
    "HunterIngestAdapter",
    "Observation",
    "Status",
    "StepResult",
    "Transport",
    "__version__",
]
