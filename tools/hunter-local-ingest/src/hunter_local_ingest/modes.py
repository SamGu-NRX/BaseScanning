"""How a stage or check actually executed.

Every receipt line names its transport and status explicitly, so a run can
never blur real backend work into a stub or a fault control.

REAL_MIRROR_CPU  the step ran against the real local mirror backend (the
                 authorized private house-scanning-server checkout at the
                 pinned ref), on CPU, and the recorded output was actually
                 produced by it.
FAKE_HUNTER      the step ran against a labeled local fault control. Useful
                 only for faults the real local service cannot
                 deterministically produce on demand; never a substitute for
                 a passing real check, and never the source of a
                 backend-behavior claim.
GPU_STUB         a named stub stood in for a GPU/model stage (objects, VLM,
                 dense inference). A stub never proves clearance, density or
                 model behavior; dependent claims stay uncertain.
REAL_API         the step executed real HTTP against a live API endpoint
                 (network I/O actually occurred) using synthetic fixture
                 inputs. This records transport reality only; it is never a
                 quality, clearance or safety claim about any backend.
PREPARED         the step consumed prepared synthetic inputs (seeded
                 fixtures) inside this harness with no live network
                 execution. Proves only that the prepared path works.
"""

from __future__ import annotations

from enum import StrEnum


class Transport(StrEnum):
    REAL_MIRROR_CPU = "REAL_MIRROR_CPU"
    FAKE_HUNTER = "FAKE_HUNTER"
    GPU_STUB = "GPU_STUB"
    REAL_API = "REAL_API"
    PREPARED = "PREPARED"


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
