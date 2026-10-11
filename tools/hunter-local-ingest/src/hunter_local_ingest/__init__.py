"""hunter-local-ingest: real-client evaluation harness for the local capture API.

Public foundation per the canonical plan (docs/plan/apps-rounds.md, BaseScanning
commit b2f5cb1b8260053bc12b416191e6d8edaab358cd): drives the published capture
lifecycle against a configurable endpoint, labels every check's transport
honestly, and emits sanitized public receipts (run-manifest.json,
checks.jsonl, summary.json) in fresh, never-overwritten run directories.

Boundaries this package enforces:
- Public code talks to the capture API over HTTP only. No backend
  implementation, schema, spec, internal test, fixture or raw log from any
  private repository is vendored here.
- Every check names how it ran: Transport.REAL_MIRROR_CPU,
  Transport.FAKE_HUNTER or Transport.GPU_STUB. A fake can never substitute
  for a passing real check; a stub never proves what the real stage proves.
- Receipts are sanitized by construction (observations.Observation).
"""

from .adapter import INTERFACE_VERSION, HunterIngestAdapter, StepResult
from .modes import Status, Transport
from .observations import Observation

__all__ = [
    "INTERFACE_VERSION",
    "HunterIngestAdapter",
    "Observation",
    "Status",
    "StepResult",
    "Transport",
]
