"""How a check actually executed, and how it ended.

Every receipt line names its transport and status, so a run can never blur
a fake into a real exchange or a skipped arm into a pass.

REAL_API     The client exchanged HTTP with the endpoint the operator
             declared real (``--backend-mode REAL_API``). This says what the
             client talked to, as declared. It does not establish that the
             backend ran its CPU stages, which models it used, or that the
             endpoint is the mirror at a given ref: the harness sees only
             HTTP answers. Earlier versions called this REAL_MIRROR_CPU,
             which claimed processing the harness cannot observe.
FAKE_HUNTER  The step ran against this package's in-process fake API or
             through its fault proxy. It shows the client reacts to a
             condition; it is never evidence of backend behavior.
GPU_STUB     A GPU/model stage (objects, VLM, dense) that this harness names
             and never runs. Always NOT_RUN here.

Statuses:

PASS     the oracle held.
FAIL     the oracle was violated.
BLOCKED  the check could not be decided: no answer, or a step it depends on
         did not complete. Never counted as a pass.
NOT_RUN  the check was deliberately not executed (no restart command, GPU
         arm). Never counted as a pass.
"""

from __future__ import annotations

from enum import StrEnum


class Transport(StrEnum):
    REAL_API = "REAL_API"
    FAKE_HUNTER = "FAKE_HUNTER"
    GPU_STUB = "GPU_STUB"


class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
