#!/usr/bin/env python3
"""Replay the placement case suite through the real capture front door.

One repeatable command per case, over the real API/hook/coordinator:

    cd backend && uv run --frozen --extra api python scripts/capture_placement_cases.py \
        --output=/tmp/capture-placement-receipt.json

For every case in ``server/placement/tests/data/cases/manifest.json`` (loaded with
``case_suite.load_cases``, so every fixture stays hash-pinned) it walks the device's
own sequence — create, register, PUT bytes to the signed upload URLs, commit,
finalize ``packet.json``, poll events, read ``GET /v1/captures/{id}/result`` —
against ``create_app()`` in-process (static test auth, local volume storage, memory
state, the documented dev handoff ``HSS_INGEST_HOOKS=auto``; no Modal, no GPU, no
network, no credentials).

Where the case enters the pipeline is the point:

- The synthetic source scene (metres) is injected **only** at the upstream ``scene``
  stage boundary, by a scoped replacement of the scene stage stub inside this
  runner's harness. The stamp is the actual capture/run/packet identity written
  into the contract's identity fields (``scene_id`` and ``provenance.inputs``) —
  never a converted placement input, a solver output, or a result.
- The ``objects_3d`` stage boundary gets an explicit, successful object list for
  every case: an empty list where the source scene already carries the hazards,
  and one nonempty observed tap hazard case whose single hazard the coordinator's
  one-time merge must place into the merged scene exactly once — delivered only
  when the case's source does not already carry that hazard id, because the merge
  contract replaces only detector/VLM twins and a same-id re-delivery of a
  tap-sourced hazard would duplicate the id and be refused. The default objects
  stubs never run on this path: a capture with no registered injection fails
  loudly instead of fabricating a hazard.
- Every other unavailable vision stage stays explicitly stubbed (prewarm off,
  external vision calls off). ``placement`` is ``stubs_exempt``: the real
  conversion and solver run behind the door when a case reaches them.

Checks per case (any mismatch fails the suite with a nonzero exit):

- The merged scene in the run's artifacts equals the independently computed
  stamp + merge of that case's source — the merge contract is re-derived here
  (same-id precedence asserted explicitly, never delegated to the production
  ``merge_object_lists`` helper) — so a substituted reference scene, another
  case's source, or a second merge is caught here.
- The placement stage's typed refusal (code, pointer) for source cases the
  bridge refuses; no placement artifact on refusal; a positive record must
  solve (``status == done`` plus a real placement artifact — a declared refusal
  is still a blocker); a negative case that somehow solves is a blocker, never
  a success; the served device result stays conservative (``manual_review``,
  HTTP 200, the journey's own run id) with exactly one terminal event.
- When a case converts: the captured converted bytes are compared field by
  field against the independently authored wire scene (identity stripped,
  floats canonicalized) — covering the tap hazard, units, evidence-derived
  coverage and chain ends — and the decision semantics match the wire case's
  expectations. Both solver responses' ``stats.input_sha256`` must match their
  own exact request bytes (the stage's against the captured converted bytes,
  the direct response's against the bytes posted), so forging the same wrong
  hash in both replies is caught even though they then agree with each other.

Aggregate checks: ``crossCapture`` compares the run's two positive cases and
requires distinct capture ids with distinct *normalized* geometry — identity
fields stripped and the rest canonicalized, so different identity stamps or
stamped scene hashes alone never pass. ``finalizeReplay`` must be an idempotent
success on the same run id. A full run (no ``--cases`` selection) requires both
positive cases present and solved; a subset diagnostic run marks the aggregate
checks it cannot make ``not_applicable``; an empty or unknown selection cannot
pass. Every failed check contributes a blocker, and the receipt is ``ok`` only
when there are none.

Findings this run pins (the reason the suite exits 1 on the committed fixtures):

- Both positive sources refuse conversion — the meter anchor is a stand-off
  wall_rect (``/objects/0/geometry/standoff_m``) and the gas hazard is a
  footprint (``/objects/1/geometry/kind``), neither declared in the manifest.
- ``generated-hazard-unsupported`` refuses at ``/generated_regions``, not the
  declared ``/objects/1/evidence`` pointer.

The receipt is versioned (``hss-capture-placement-receipt/v0``) and carries code
SHA, the stubbed stages, per-case case/packet/source/converted/result hashes,
capture/run ids, stage status/error, solver policy and rules hash, terminal
event count, the direct comparison and the device outcome in separate fields.
Tokens and signed upload URLs never enter the receipt (checked before writing).
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from server.api.app import create_app  # noqa: E402
from server.api.deps import drain_finalize  # noqa: E402
from server.common.config import Settings  # noqa: E402
from server.contracts.jobs import StageContext, StageResult, run_id_for_capture  # noqa: E402
from server.contracts.result import ResultResponse  # noqa: E402
from server.eval.sceneio import validate  # noqa: E402
from server.orchestrator.graph import STAGES  # noqa: E402
from server.orchestrator.stateio import get_json  # noqa: E402
from server.orchestrator.stubs._common import json_bytes, write_files  # noqa: E402
from server.placement import adapter, case_suite  # noqa: E402

RECEIPT_SCHEMA_VERSION = "hss-capture-placement-receipt/v0"
TERMINAL_EVENTS = {"verdict_ready", "retake_request", "failed"}
TOKEN = "d" * 40
PACKET_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "packets" / "mini-arkit"

# The documented dev settings (SD-73), as the integration gate and the front-door
# test set them: the real hook chain over stubbed vision stages, prewarm off.
TEMPLATE_ENV = {
    "HSS_ORCH_STAGE_STUBS": "1",
    "HSS_ORCH_PREWARM": "0",
    "VISION_EXTERNAL_CALLS": "off",
    "STATE_BACKEND": "memory",
    "STORAGE_BACKEND": "volume",
    "SMP_ENVIRONMENT": "dev",
}

# The one case whose objects_3d boundary carries a nonempty observed tap hazard.
TAP_CASE_ID = "hazard-right-ground-gap"

# Expectations pinned on the wire scene, evaluated against the journey's real
# converted-path result: the wire's six-decimal rounding (declared by the packet as
# 5e-7 ft) propagates through the real conversion and solver into derived feet
# values, so those compare within the packet's declared 1e-4 ft derived-output
# tolerance. Scene geometry itself is still compared exactly, field by field (see
# _check_converted_geometry), and the case-suite evaluator stays exact by default
# (tol_ft defaults to 0.0).
_WIRE_DERIVED_TOLERANCE_FT = 1e-4

# The supported case packet is the default replay target; the legacy W7 cases
# manifest stays available explicitly via --manifest and keeps its own pinned
# refusal state, reported as blockers rather than mistaken for acceptance.
DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "server/placement/tests/data/capture_cases_v1/manifest.json"

# An observed tap hazard, hand-authored for this runner: a wall_rect vent on the
# case's wall, away from the case's pinned decision geometry. It exists to prove
# the coordinator's one-time merge, never to fabricate a conflicting hazard.
TAP_HAZARD: dict[str, Any] = {
    "id": "obj-tap-vent",
    "class": "vent",
    "wall_id": "side",
    "geometry": {"kind": "wall_rect", "wall_id": "side", "s_m": [1.6, 1.8], "bottom_m": 0.5, "top_m": 2.0},
    "confidence": 0.9,
    "plus_minus_m": 0.05,
    "source": {"kind": "tap"},
    "evidence": "observed",
}

# Scoped injection registry: capture id -> case injection. The patched scene and
# objects_3d stage functions answer only from here; anything else fails loudly.
_REGISTRY: dict[str, dict[str, Any]] = {}
_PATCHED: list[tuple[Any, str, Any]] = []


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _apply_dev_env(volume_root: Path) -> Callable[[], None]:
    """The finalize handoff builds its service template from process env, so the
    dev flags must be environment, not only Settings fields — including the
    volume settings `services.current()` validates before the app's own state and
    storage are bound in.

    Returns a callable restoring the prior environment: run_cases runs in-process
    inside the test suite, and leaked dev flags would make any later test that
    asserts env-derived defaults order-dependent.
    """
    applied = {
        **TEMPLATE_ENV,
        "VOLUME_ROOT": str(volume_root),
        "CAPTURE_API_PUBLIC_URL": "http://testserver",
        "UPLOAD_URL_HMAC_SECRET": "h" * 32,
    }
    saved: dict[str, str | None] = {key: os.environ.get(key) for key in applied}
    for key, value in applied.items():
        os.environ[key] = value

    def _restore() -> None:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    return _restore


def _stamped_scene(
    record: case_suite.CaseRecord, *, packet_id: str, packet_sha: str, capture_id: str, run_id: str
) -> dict[str, Any]:
    """The case's source scene stamped with this run's actual identity.

    Identity lives in the contract's own identity fields: ``scene_id`` names the
    case, packet, capture and run; ``provenance.inputs`` carries the packet id and
    hash. Nothing else in the authored geometry changes.
    """
    document = copy.deepcopy(json.loads(record.source_path.read_text()))
    document["scene_id"] = f"{record.id}+{packet_id}+{capture_id}+{run_id}"
    provenance = dict(document.get("provenance") or {})
    provenance["inputs"] = {"packet_id": packet_id, "packet_sha256": packet_sha}
    document["provenance"] = provenance
    return document


def _scene_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True).encode()


def _objects_3d_for(record: case_suite.CaseRecord) -> list[dict[str, Any]]:
    """The objects_3d list this case's boundary delivers.

    The tap hazard is delivered only where the upstream source does not already
    carry it: the merge contract replaces only detector/VLM twins, so a same-id
    re-delivery of a source-carried (tap-sourced) hazard is never replaced — it
    duplicates the id and the merged scene is refused as inconsistent. A
    source-carried hazard still reaches the one-time tap checks through the real
    merge and real conversion.
    """
    if record.id != TAP_CASE_ID:
        return []
    source_object_ids = {obj.get("id") for obj in json.loads(record.source_path.read_text()).get("objects") or []}
    return [] if TAP_HAZARD["id"] in source_object_ids else [copy.deepcopy(TAP_HAZARD)]


def _injected_scene_run(ctx: StageContext) -> StageResult:
    """The scene stage boundary: this capture's stamped source scene, or a loud
    refusal. The default stub scene never runs here."""
    entry = _REGISTRY.get(ctx.capture_id)
    if entry is None:
        raise RuntimeError(
            f"capture-placement runner: capture {ctx.capture_id} has no injected source "
            "scene; the default stub scene must not fabricate this case's upstream data"
        )
    scene = _stamped_scene(
        entry["record"],
        packet_id=entry["packet_id"],
        packet_sha=entry["packet_sha256"],
        capture_id=ctx.capture_id,
        run_id=ctx.run_id,
    )
    validate(scene)
    entry["stamped_scene_id"] = scene["scene_id"]
    return write_files(
        ctx,
        {
            "scene/scene.json": _scene_bytes(scene),
            "scene/build_report.json": json.dumps(
                {
                    "status": "capture-placement-runner",
                    "caseId": entry["record"].id,
                    "packetId": entry["packet_id"],
                    "captureId": ctx.capture_id,
                    "runId": ctx.run_id,
                    "sceneId": scene["scene_id"],
                }
            ).encode(),
        },
    )


def _injected_objects_3d_run(ctx: StageContext) -> StageResult:
    """The objects_3d stage boundary: the case's explicit object list, or a loud
    refusal. An empty list is a successful, deliberate shape."""
    entry = _REGISTRY.get(ctx.capture_id)
    if entry is None:
        raise RuntimeError(
            f"capture-placement runner: capture {ctx.capture_id} has no injected "
            "objects_3d output; the default stub must not fabricate hazards"
        )
    body = {"format": "hss-objects-3d/v0", "objects": copy.deepcopy(entry["objects_3d"])}
    return write_files(ctx, {"objects/objects_3d.json": json_bytes(body)})


def _install_boundaries() -> None:
    """Swap the scene and objects_3d stub entry points for the scoped injections."""
    import server.orchestrator.stubs.objects_3d as objects_3d_stub
    import server.orchestrator.stubs.scene as scene_stub

    _PATCHED.extend(
        [
            (scene_stub, "run", scene_stub.run),
            (objects_3d_stub, "run", objects_3d_stub.run),
        ]
    )
    scene_stub.run = _injected_scene_run
    objects_3d_stub.run = _injected_objects_3d_run


def _remove_boundaries() -> None:
    for module, name, original in reversed(_PATCHED):
        setattr(module, name, original)
    _PATCHED.clear()
    _REGISTRY.clear()


def _content_type(role: str) -> str:
    if role == "stream":
        return "application/gzip"
    if role in {"features", "depth", "confidence", "mesh"}:
        return "application/octet-stream"
    return "image/jpeg"


class _Harness:
    """One app, many captures: the cross-capture check lives in the sharing."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.app = create_app(
            settings=Settings(
                SMP_ENVIRONMENT="dev",
                HSS_AUTH_MODE="static",
                CAPTURE_API_TOKENS=f"device:{TOKEN}",
                UPLOAD_URL_HMAC_SECRET="h" * 32,
                STORAGE_BACKEND="volume",
                STATE_BACKEND="memory",
                VOLUME_ROOT=str(workdir / "vol"),
                CAPTURE_API_PUBLIC_URL="http://testserver",
                HSS_INGEST_HOOKS="auto",
                VISION_EXTERNAL_CALLS="off",
                GIT_SHA="0123456789abcdef",
                SMP_SERVICE_NAME="capture-api",
            )
        )
        self.client = TestClient(self.app)
        self.client.headers["Authorization"] = f"Bearer {TOKEN}"

    def drain(self) -> None:
        drain_finalize(self.app.state.deps)


def _poll_events(client: TestClient, capture_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    after = 0
    for _ in range(50):
        page = client.get(f"/v1/captures/{capture_id}/events", params={"after": after}).json()
        events.extend(page["events"])
        if page["next"] == after:
            return events
        after = page["next"]
    raise RuntimeError("event feed never stopped moving")


def _walk_front_door(
    harness: _Harness,
    record: case_suite.CaseRecord,
    *,
    packet_id: str,
) -> dict[str, Any]:
    """One capture for one W8 case, through the device's own sequence."""

    def register(capture_id: str, resolved_packet_id: str, packet_sha: str) -> None:
        _REGISTRY[capture_id] = {
            "record": record,
            "packet_id": resolved_packet_id,
            "packet_sha256": packet_sha,
            "objects_3d": _objects_3d_for(record),
        }

    return _walk_capture(harness, case_id=record.id, packet_id=packet_id, register=register)


def _walk_capture(
    harness: _Harness,
    *,
    case_id: str,
    packet_id: str,
    register: Callable[[str, str, str], None],
) -> dict[str, Any]:
    """One capture through the device's own sequence, shared by both modes: the
    W8 case suite registers its manifest record here, the real-downstream mode
    registers its fixture entry. Everything else is the journey itself."""
    journey: dict[str, Any] = {"caseId": case_id, "packetId": packet_id}
    client = harness.client
    root = harness.workdir / f"packet-{packet_id}"
    shutil.copytree(PACKET_FIXTURE, root)
    packet = json.loads((root / "packet.json").read_text())
    document = dict(packet)
    document["packetId"] = packet_id
    raw = (json.dumps(document, indent=2) + "\n").encode()
    (root / "packet.json").write_bytes(raw)
    journey["packetSha256"] = _sha256(raw)

    session = packet["session"]
    created = client.post(
        "/v1/captures",
        json={
            "packetId": packet_id,
            "formatVersion": packet["formatVersion"],
            "tier": session["tier"],
            "flow": "guided",
            "device": {
                "model": session["deviceModel"],
                "systemVersion": session["systemVersion"],
                "appVersion": session["appVersion"],
            },
        },
        headers={"Idempotency-Key": packet_id},
    )
    if created.status_code != 201:
        journey["errors"] = [f"create_http_{created.status_code}: {created.text[:200]}"]
        return journey
    capture_id = created.json()["captureId"]
    journey["captureId"] = capture_id

    # Register the case at the boundaries before finalize can start the pipeline.
    register(capture_id, packet_id, journey["packetSha256"])

    files = []
    for entry in packet["files"]:
        data = (root / entry["path"]).read_bytes()
        role = entry.get("role", "still")
        files.append(
            {
                "path": entry["path"],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "md5": base64.b64encode(hashlib.md5(data).digest()).decode(),
                "role": role,
                "contentType": _content_type(role),
            }
        )
    registered = client.post(f"/v1/captures/{capture_id}/files", json={"files": files})
    if registered.status_code != 200:
        journey["errors"] = [f"register_http_{registered.status_code}: {registered.text[:200]}"]
        return journey
    for item, target in zip(files, registered.json()["files"], strict=True):
        put = TestClient(harness.app).put(
            target["upload"]["url"],
            content=(root / item["path"]).read_bytes(),
            headers=target["upload"].get("headers") or {},
        )
        if put.status_code != 200:
            journey["errors"] = [f"upload_http_{put.status_code}: {target['path']}"]
            return journey
    commit = client.post(
        f"/v1/captures/{capture_id}/files:commit",
        json={"files": [{"path": item["path"], "sha256": item["sha256"]} for item in files]},
    )
    if commit.status_code != 200 or commit.json().get("mismatch"):
        journey["errors"] = [f"commit_http_{commit.status_code}: {commit.text[:200]}"]
        return journey
    harness.drain()

    finalize = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": packet_id,
            "Content-Type": "application/json",
            "X-Packet-Sha256": journey["packetSha256"],
        },
    )
    if finalize.status_code != 202:
        journey["errors"] = [f"finalize_http_{finalize.status_code}: {finalize.text[:200]}"]
        return journey
    accepted = finalize.json()
    run_id = accepted["runId"]
    journey["runId"] = run_id
    journey["runIdDeterministic"] = run_id == run_id_for_capture(capture_id, raw)
    harness.drain()
    journey["events"] = _poll_events(client, capture_id)
    return journey


def _replay_finalize(harness: _Harness, journey: dict[str, Any]) -> dict[str, Any] | None:
    """Finalize replay: one run id, one terminal event, still."""
    capture_id = journey.get("captureId")
    if capture_id is None:
        return None
    client = harness.client
    raw = (harness.workdir / f"packet-{journey['packetId']}" / "packet.json").read_bytes()
    replayed = client.post(
        f"/v1/captures/{capture_id}/finalize",
        content=raw,
        headers={
            "Idempotency-Key": journey["packetId"],
            "Content-Type": "application/json",
            "X-Packet-Sha256": _sha256(raw),
        },
    )
    harness.drain()
    events_after = _poll_events(client, capture_id)
    terminals_after = [event for event in events_after if event["type"] in TERMINAL_EVENTS]
    return {
        "caseId": journey["caseId"],
        "replayHttpStatus": replayed.status_code,
        "sameRunId": replayed.json().get("runId") == journey["runId"],
        "terminalEventCountAfter": len(terminals_after),
        "ok": replayed.status_code == 202
        and replayed.json().get("runId") == journey["runId"]
        and len(terminals_after) == 1,
    }


def _run_artifacts(harness: _Harness, run_id: str) -> dict[str, str]:
    """The run's artifact map overlaid with the placement stage's own keys — the
    same recovery `routes/result.py` uses."""
    run = harness.app.state.deps.state.get_run(run_id)
    artifacts = dict(run.artifacts or {})
    extra = get_json(harness.app.state.deps.state, f"stage_artifacts:{run_id}:placement") or {}
    if isinstance(extra, dict):
        artifacts.update({str(key): str(value) for key, value in extra.items() if value is not None})
    return artifacts


# The merge contract, re-derived here without the production
# ``server.scene.merge.merge_object_lists`` helper: incoming objects append, and a
# detector or VLM scene object leaves only when an incoming object takes its id or
# its place (same class, wall_rect on the same wall, or the same centre within the
# merge tolerance). Re-deriving keeps the oracle independent: a coordinator bug and
# an oracle bug can no longer be the same bug.
_MERGE_SAME_PLACE_TOL_M = 0.3


def _object_center_m(obj: dict[str, Any]) -> tuple[float, float, float] | None:
    geometry = obj.get("geometry") or {}
    if geometry.get("kind") == "point":
        x, y, z = (float(v) for v in geometry["pos_m"])
        return x, y, z
    if geometry.get("kind") == "wall_rect":
        span = geometry.get("s_m") or [0.0, 0.0]
        return (
            (float(span[0]) + float(span[1])) / 2,
            (float(geometry.get("bottom_m", 0.0)) + float(geometry.get("top_m", 0.0))) / 2,
            0.0,
        )
    return None


def _same_place(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("class") != b.get("class"):
        return False
    center_a, center_b = _object_center_m(a), _object_center_m(b)
    if center_a is None or center_b is None:
        return False
    geometry_a, geometry_b = a.get("geometry") or {}, b.get("geometry") or {}
    if geometry_a.get("kind") == "wall_rect" and geometry_b.get("kind") == "wall_rect":
        if geometry_a.get("wall_id") != geometry_b.get("wall_id"):
            return False
        return abs(center_a[0] - center_b[0]) <= _MERGE_SAME_PLACE_TOL_M and abs(center_a[1] - center_b[1]) <= (
            _MERGE_SAME_PLACE_TOL_M
        )
    return math.dist(center_a, center_b) <= _MERGE_SAME_PLACE_TOL_M


def _replaced_independently(prev: dict[str, Any], incoming: list[dict[str, Any]]) -> bool:
    kind = (prev.get("source") or {}).get("kind")
    if kind not in {"detector", "vlm"}:
        return False
    for obj in incoming:
        if prev.get("id") and prev.get("id") == obj.get("id"):
            return True
        if _same_place(prev, obj):
            return True
    return False


def _assert_merge_precedence(merged: dict[str, Any], journey: dict[str, Any], mismatches: list[str]) -> None:
    """Same-id precedence, asserted explicitly: every injected object appears in the
    merged scene exactly once and the merged copy *is* the injected object."""
    entry = _REGISTRY[journey["captureId"]]
    merged_objects = merged.get("objects") or []
    for incoming in entry["objects_3d"]:
        object_id = incoming.get("id")
        matches = [obj for obj in merged_objects if obj.get("id") == object_id]
        if len(matches) != 1:
            mismatches.append(
                f"injected object {object_id!r} appears {len(matches)} times after the merge, not exactly once"
            )
        elif matches[0] != incoming:
            mismatches.append(
                f"injected object {object_id!r} lost same-id precedence: the merged copy is not the injected object"
            )


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    return value


def _normalized_geometry(document: dict[str, Any]) -> dict[str, Any]:
    """Identity-stripped, canonicalized geometry. Hashes of normalized geometry are
    equal only when the geometry itself is equal: different identity stamps or
    different stamped scene hashes prove nothing here."""
    stripped = {
        key: value for key, value in document.items() if key not in {"scene_id", "provenance", "schema_version"}
    }
    return _canonical(stripped)


def _geometry_diffs(converted: Any, expected: Any, path: str = "", limit: int = 5) -> list[str]:
    """The first structural differences between the converted scene and the
    independently authored wire scene, as readable paths."""
    if isinstance(converted, dict) and isinstance(expected, dict):
        diffs: list[str] = []
        for key in sorted(set(converted) | set(expected)):
            if len(diffs) >= limit:
                return diffs
            if key not in converted:
                diffs.append(f"{path}/{key}: missing from the converted scene")
            elif key not in expected:
                diffs.append(f"{path}/{key}: not in the wire scene")
            else:
                diffs.extend(_geometry_diffs(converted[key], expected[key], f"{path}/{key}", limit - len(diffs)))
        return diffs
    if isinstance(converted, list) and isinstance(expected, list):
        diffs = []
        if len(converted) != len(expected):
            diffs.append(f"{path}: {len(converted)} entries, wire scene has {len(expected)}")
        else:
            for index, (a, b) in enumerate(zip(converted, expected, strict=True)):
                if len(diffs) >= limit:
                    return diffs
                diffs.extend(_geometry_diffs(a, b, f"{path}/{index}", limit - len(diffs)))
        return diffs
    if _canonical(converted) != _canonical(expected):
        return [f"{path}: {converted!r} != wire {expected!r}"]
    return []


# One metre in feet, by definition: every converted length is the source metres
# divided by exactly 0.3048. The unit sentinel re-derives one length end to end.
M_PER_FT = 0.3048


def _expected_merged_scene(journey: dict[str, Any]) -> dict[str, Any]:
    """The merged scene this case must produce, computed independently of the
    pipeline: the stamped source plus the injected objects, merged once under the
    contract re-derived above."""
    entry = _REGISTRY[journey["captureId"]]
    stamped = _stamped_scene(
        entry["record"],
        packet_id=entry["packet_id"],
        packet_sha=entry["packet_sha256"],
        capture_id=journey["captureId"],
        run_id=journey["runId"],
    )
    incoming = copy.deepcopy(entry["objects_3d"])
    kept = [obj for obj in stamped.get("objects") or [] if not _replaced_independently(obj, incoming)]
    expected = dict(stamped)
    expected["objects"] = kept + incoming
    return expected


def _check_converted_geometry(
    case: dict[str, Any],
    record: case_suite.CaseRecord,
    *,
    converted_bytes: bytes,
    merged_bytes: bytes | None,
    mismatches: list[str],
) -> None:
    """The captured converted scene against independently derived facts: the unit
    sentinel, the preserved chain ends, the tap hazard's once-only conversion, and
    a field-by-field comparison with the authored wire scene."""
    converted = json.loads(converted_bytes)
    merged = json.loads(merged_bytes) if merged_bytes else {}

    source_height_m = (merged.get("walls") or [{}])[0].get("height_m")
    converted_height_ft = (converted.get("walls") or [{}])[0].get("height_ft")
    units_ok = (
        source_height_m is not None
        and converted_height_ft is not None
        and abs(float(converted_height_ft) - float(source_height_m) / M_PER_FT) <= 1e-6
    )
    case["unitsSentinel"] = "matched" if units_ok else "mismatched"
    if not units_ok:
        mismatches.append("converted wall height is not the merged source height divided by 0.3048 (units sentinel)")

    chain_ends = (merged.get("chain") or {}).get("ends") or {}
    converted_ends = (converted.get("coverage") or {}).get("ends") or {}
    for side, end in sorted(chain_ends.items()):
        converted_kind = (converted_ends.get(side) or {}).get("kind")
        if converted_kind != end.get("kind"):
            mismatches.append(
                f"converted coverage end {side!r} is {converted_kind!r}, not the source chain's {end.get('kind')!r}"
            )

    if record.id == TAP_CASE_ID:
        expected_span_ft = [value / M_PER_FT for value in TAP_HAZARD["geometry"]["s_m"]]
        taps = [
            obj
            for obj in converted.get("objects") or []
            if obj.get("type") == TAP_HAZARD["class"]
            and obj.get("source") == TAP_HAZARD["source"]["kind"]
            and len(obj.get("span_ft") or []) == 2
            and all(abs(a - b) <= 1e-6 for a, b in zip(obj["span_ft"], expected_span_ft, strict=True))
        ]
        case["tapHazardConvertedCount"] = len(taps)
        if len(taps) != 1:
            mismatches.append(
                f"the observed tap hazard appears {len(taps)} times in the converted scene at its expected feet "
                "span, not exactly once"
            )

    if record.wire_path is None:
        case["geometryComparison"] = "not_applicable"
        return
    wire = json.loads(record.wire_path.read_bytes())
    normalized = _normalized_geometry(converted)
    diffs = _geometry_diffs(normalized, _normalized_geometry(wire))
    case["geometryComparison"] = "matched" if not diffs else "mismatched"
    case["geometryDiffs"] = diffs
    # Canonical normalized-geometry hash, identity stamps stripped: the
    # cross-capture comparison fails same-geometry-different-identity runs.
    case["mergedGeometrySha256"] = _sha256(json.dumps(normalized, sort_keys=True).encode())
    if diffs:
        mismatches.append("converted scene differs from the independently authored wire scene: " + "; ".join(diffs[:3]))


def _decision_bytes(payload: dict[str, Any]) -> str:
    decision = json.loads(json.dumps(payload))
    decision.get("stats", {}).pop("elapsed_ms", None)
    return json.dumps(decision, sort_keys=True)


def _collect_case(harness: _Harness, record: case_suite.CaseRecord, journey: dict[str, Any]) -> dict[str, Any]:
    """The case's evidence and checks, from the journey's own artifacts."""
    case: dict[str, Any] = {
        "id": record.id,
        "kind": record.kind,
        "packetId": journey.get("packetId"),
        "packetSha256": journey.get("packetSha256"),
        "captureId": journey.get("captureId"),
        "runId": journey.get("runId"),
        "runIdDeterministic": journey.get("runIdDeterministic"),
        "sourceSha256": _sha256(record.source_path.read_bytes()),
        "wireSha256": _sha256(record.wire_path.read_bytes()) if record.wire_path else None,
        "mismatches": [],
        "errors": list(journey.get("errors", [])),
    }
    if case["errors"]:
        case["status"] = "error"
        return case
    deps = harness.app.state.deps
    run_id = journey["runId"]

    # The merged scene the boundary handed to placement, checked against the
    # independent stamp + merge (catches reference substitution, cross-capture
    # scene reuse, and a second merge).
    artifacts = _run_artifacts(harness, run_id)
    scene_key = artifacts.get("scene/scene.json")
    merged_bytes = deps.storage.get_bytes(scene_key) if scene_key else None
    case["mergedSceneSha256"] = _sha256(merged_bytes) if merged_bytes else None
    expected_merged = _expected_merged_scene(journey)
    merged_matches = bool(merged_bytes) and json.loads(merged_bytes) == expected_merged
    case["sceneBoundaryMatchesCase"] = merged_matches
    if not merged_matches:
        case["mismatches"].append(
            "merged scene does not equal this case's stamped source merged once with the injected objects_3d output"
        )

    tap_id = TAP_HAZARD["id"]
    tap_objects = (
        [obj for obj in json.loads(merged_bytes).get("objects", []) if obj.get("id") == tap_id] if merged_bytes else []
    )
    case["tapHazardMergeCount"] = len(tap_objects) if record.id == TAP_CASE_ID else None
    if record.id == TAP_CASE_ID and len(tap_objects) != 1:
        case["mismatches"].append(f"observed tap hazard merged {len(tap_objects)} times, not exactly once")

    # The objects_3d boundary's own record.
    o3d_record = deps.state.get_stage(run_id, "objects_3d")
    case["objects3dStage"] = {"status": getattr(o3d_record, "status", None)}
    if getattr(o3d_record, "status", None) != "done":
        case["mismatches"].append(f"objects_3d stage status {getattr(o3d_record, 'status', None)!r}, not done")

    # The placement stage: real conversion and solver when the case reaches them.
    placement_record = deps.state.get_stage(run_id, "placement")
    stage_status = getattr(placement_record, "status", None)
    stage_error = getattr(placement_record, "error_code", None)
    case["placementStage"] = {"status": stage_status, "errorCode": stage_error}
    placement_artifact_key = artifacts.get("placement/placement.json")
    converted_key = artifacts.get("placement/scene.json")
    case["hasPlacementArtifact"] = bool(placement_artifact_key) and deps.storage.exists(placement_artifact_key)

    if stage_status == "done" and case["hasPlacementArtifact"]:
        if record.kind != case_suite.KIND_POSITIVE:
            case["mismatches"].append(
                f"{record.kind!r} case produced a successful placement; only a positive case may solve"
            )
        case["status"] = "solved"
        converted_bytes = deps.storage.get_bytes(converted_key) if converted_key else None
        placement = json.loads(deps.storage.get_bytes(placement_artifact_key))
        case["convertedSha256"] = _sha256(converted_bytes) if converted_bytes else None
        case["resultSha256"] = _sha256(deps.storage.get_bytes(placement_artifact_key))
        case["solverPolicy"] = placement.get("policy")
        # The solver's own decision, kept distinct from the device's served result
        # kind (deviceOutcome) so the receipt shows both sides of the door.
        case["solverDecision"] = placement.get("decision")

        # Both solver responses must bind their own input hash to their own exact
        # request bytes. Forging the same wrong hash in both replies leaves them in
        # agreement with each other and still fails here.
        stage_input_sha = (placement.get("stats") or {}).get("input_sha256")
        case["stageInputShaMatchesCapturedBytes"] = stage_input_sha == case["convertedSha256"]
        if not case["stageInputShaMatchesCapturedBytes"]:
            case["mismatches"].append(
                "the stage response's stats.input_sha256 does not match the sha256 of the captured converted bytes it solved"
            )

        _check_converted_geometry(
            case,
            record,
            converted_bytes=converted_bytes or b"{}",
            merged_bytes=merged_bytes,
            mismatches=case["mismatches"],
        )

        if converted_bytes is None:
            case["errors"].append("the placement stage solved without publishing placement/scene.json")
            case["directComparison"] = "error"
        else:
            direct = TestClient(adapter.load_api()["api"].app).post(
                "/v1/placements",
                content=converted_bytes,
                headers={"Content-Type": "application/json"},
            )
            if direct.status_code != 200:
                case["errors"].append(f"direct_placements_http_{direct.status_code}")
                case["directComparison"] = "error"
            else:
                direct_body = direct.json()
                direct_sha = (direct_body.get("stats") or {}).get("input_sha256")
                case["directInputShaMatchesPostedBytes"] = direct_sha == case["convertedSha256"]
                if not case["directInputShaMatchesPostedBytes"]:
                    case["mismatches"].append(
                        "the direct solver response's stats.input_sha256 does not match the sha256 of the exact "
                        "request bytes"
                    )
                case["directComparison"] = (
                    "matched" if _decision_bytes(direct_body) == _decision_bytes(placement) else "mismatched"
                )
                if case["directComparison"] == "mismatched":
                    case["mismatches"].append(
                        "direct POST /v1/placements on the captured bytes disagrees with the stage response"
                    )
        if record.wire_path is not None:
            wire_mismatches = case_suite._evaluate_expectations(
                record.expectations, placement, tol_ft=_WIRE_DERIVED_TOLERANCE_FT
            )
            case["wireSemanticComparison"] = "matched" if not wire_mismatches else "mismatched"
            case["wireSemanticMismatches"] = wire_mismatches
            if wire_mismatches:
                case["mismatches"].append("decision semantics differ from the independently authored wire case")
    elif stage_error is not None:
        # Typed refusal: visible as refused, never as a passed decision. No
        # fallback placement artifact may exist.
        if case["hasPlacementArtifact"]:
            case["mismatches"].append("refused placement still published a placement artifact")
        case["status"] = "refused"
        case["refusal"] = {"code": stage_error, "message": str(getattr(placement_record, "error", ""))[:400]}
        declared = record.expected_error
        case["refusal"]["declared"] = bool(declared)
        case["refusal"]["pointerMatchesDeclaration"] = None
        if declared:
            case["refusal"]["declaredCode"] = declared.get("code")
            case["refusal"]["declaredPointer"] = declared.get("pointer")
            case["refusal"]["codeMatchesDeclaration"] = stage_error == declared.get("code")
            case["refusal"]["pointerMatchesDeclaration"] = declared.get("pointer") in case["refusal"]["message"]
            if not case["refusal"]["codeMatchesDeclaration"]:
                case["mismatches"].append(f"refusal code {stage_error!r} != declared {declared.get('code')!r}")
            if not case["refusal"]["pointerMatchesDeclaration"]:
                case["mismatches"].append(
                    f"refusal pointer {declared.get('pointer')!r} not found in the refusal message"
                )
        elif record.kind == case_suite.KIND_POSITIVE:
            if declared:
                case["mismatches"].append(
                    "positive case refused conversion; even with a declared error a positive record must solve "
                    "and publish a placement artifact"
                )
            else:
                case["mismatches"].append(
                    "positive case refused conversion, but the manifest declares no expected error for it "
                    "(the source does not pass the scene bridge as authored)"
                )
        case["convertedSha256"] = None
        case["resultSha256"] = None
        case["solverPolicy"] = None
        case["solverDecision"] = None
        case["directComparison"] = "not_applicable"
        case["wireSemanticComparison"] = "not_applicable"
    else:
        case["status"] = "error"
        case["errors"].append(
            f"placement stage {stage_status!r} with error {stage_error!r}: neither solved nor refused"
        )

    # The device boundary: conservative outcome, the journey's own identity, one
    # terminal event. Criteria and the result stay separately stubbed, so this pins
    # the served boundary, never a claim that the decision caused the device state.
    client = harness.client
    capture = client.get(f"/v1/captures/{journey['captureId']}").json()
    served = client.get(f"/v1/captures/{journey['captureId']}/result")
    served_body = served.json() if served.status_code == 200 else {}
    case["deviceOutcome"] = {
        "captureStatus": capture.get("status"),
        "servedResultHttpStatus": served.status_code,
        "servedResultKind": (served_body.get("outcome") or {}).get("kind"),
        "servedResultRunId": served_body.get("runId"),
    }
    terminals = [event for event in journey["events"] if event["type"] in TERMINAL_EVENTS]
    case["terminalEventCount"] = len(terminals)
    if len(terminals) != 1:
        case["mismatches"].append(f"{len(terminals)} terminal events, not exactly one")
    if case["deviceOutcome"]["captureStatus"] != "manual_review":
        case["mismatches"].append(
            f"capture status {case['deviceOutcome']['captureStatus']!r} is not the conservative manual_review"
        )
    if served.status_code != 200:
        case["mismatches"].append(f"GET /result returned HTTP {served.status_code}, not 200")
    elif case["deviceOutcome"]["servedResultKind"] != "manual_review":
        case["mismatches"].append(
            f"served result outcome {case['deviceOutcome']['servedResultKind']!r} is not the conservative manual_review"
        )
    if case["deviceOutcome"]["servedResultRunId"] != journey.get("runId"):
        case["mismatches"].append(
            f"served result run id {case['deviceOutcome']['servedResultRunId']!r} does not match this journey's "
            f"run id {journey.get('runId')!r}"
        )
    return case


def run_cases(
    manifest_path: Path,
    workdir: Path,
    *,
    case_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run the manifest's cases through the front door and return the receipt."""
    restore_env = _apply_dev_env(workdir / "vol")
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        if case_ids is not None and not case_ids:
            raise SystemExit("empty case selection: name at least one case id, or run every case")
        records = case_suite.load_cases(manifest_path)
        full_run = case_ids is None
        if case_ids:
            wanted = set(case_ids)
            records = [record for record in records if record.id in wanted]
            if len(records) != len(wanted):
                raise SystemExit(f"unknown case ids: {sorted(wanted - {record.id for record in records})}")
        try:
            code_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            code_sha = "unknown"

        harness = _Harness(workdir / "app")
        _install_boundaries()
        try:
            journeys = []
            for index, record in enumerate(records):
                journeys.append(_walk_front_door(harness, record, packet_id=f"placement-case-{index + 1:02d}"))
            harness.drain()
            cases = [_collect_case(harness, record, journey) for record, journey in zip(records, journeys, strict=True)]
            if journeys and journeys[0].get("captureId") is not None:
                replay = _replay_finalize(harness, journeys[0])
                replay["applicable"] = True
            elif journeys:
                replay = {
                    "applicable": False,
                    "status": "not_applicable",
                    "reason": "the first journey never created a capture",
                    "ok": None,
                }
            else:
                replay = {"applicable": False, "status": "not_applicable", "reason": "no case ran", "ok": None}
        finally:
            _remove_boundaries()

        # Cross-capture: distinct captures in one app must carry distinct scenes — and
        # distinct *normalized* geometry, not merely distinct identity stamps or
        # stamped scene hashes. Only the two positive cases are compared.
        positive_cases = [case for case in cases if case["kind"] == case_suite.KIND_POSITIVE]
        pair = positive_cases[:2]
        raw_hashes = [case.get("mergedSceneSha256") for case in pair]
        geometry_hashes = [case.get("mergedGeometrySha256") for case in pair]
        capture_ids = [case.get("captureId") for case in pair]
        applicable = len(pair) == 2 and all(raw_hashes) and all(geometry_hashes) and all(capture_ids)
        cross_capture = {
            "applicable": applicable,
            "status": "evaluated" if applicable else "not_applicable",
            "reason": None
            if applicable
            else "not_applicable: this run does not include two positive cases that each carried a merged scene",
            "captureIds": capture_ids,
            "distinctScenes": (raw_hashes[0] != raw_hashes[1]) if applicable else None,
            "distinctNormalizedGeometry": (geometry_hashes[0] != geometry_hashes[1]) if applicable else None,
            "mergedGeometrySha256": dict(zip([case["id"] for case in pair], geometry_hashes, strict=False))
            if pair
            else {},
        }

        blockers = []
        for case in cases:
            if case["status"] == "refused" and not case.get("refusal", {}).get("declared"):
                blockers.append(
                    f"{case['id']}: refused at {case['refusal']['code']} — the manifest declares no expected error "
                    f"({case['refusal']['message'][:160]})"
                )
            for key in ("codeMatchesDeclaration", "pointerMatchesDeclaration"):
                if case.get("refusal", {}).get(key) is False:
                    blockers.append(
                        f"{case['id']}: refusal {key.replace('MatchesDeclaration', '')} does not match the declaration"
                    )
            if case["mismatches"]:
                blockers.append(f"{case['id']}: " + "; ".join(case["mismatches"]))
            if case["errors"]:
                blockers.append(f"{case['id']}: " + "; ".join(case["errors"]))
        if cross_capture["applicable"]:
            if not (
                cross_capture["distinctScenes"]
                and cross_capture["distinctNormalizedGeometry"]
                and len(set(cross_capture["captureIds"])) == 2
            ):
                blockers.append(
                    "crossCapture: the two positive cases do not carry distinct capture ids with distinct normalized "
                    f"geometry (distinctScenes={cross_capture['distinctScenes']}, "
                    f"distinctNormalizedGeometry={cross_capture['distinctNormalizedGeometry']})"
                )
        if replay is not None and replay.get("applicable", True) and replay.get("ok") is not True:
            blockers.append(
                "finalizeReplay: the finalize replay is not an idempotent success (HTTP 202, same run id, one terminal "
                f"event); got {json.dumps(replay)}"
            )
        if full_run:
            if len(positive_cases) < 2:
                blockers.append(f"full run requires both positive cases; this manifest ran {len(positive_cases)}")
            unsolved = [case["id"] for case in positive_cases if case["status"] != "solved"]
            if unsolved:
                blockers.append(
                    "full run requires both positive cases to solve and publish placement artifacts; not solved: "
                    + ", ".join(unsolved)
                )

        statuses = [case["status"] for case in cases]
        receipt: dict[str, Any] = {
            "schemaVersion": RECEIPT_SCHEMA_VERSION,
            "generatedAt": datetime.now(UTC).isoformat(),
            "codeSha": code_sha,
            "producers": {
                # The integrated producer SHAs on this branch (base + imports), so the
                # receipt names exactly which producer code ran, not just branches.
                "base": "obv/burst7-bs-capture-placement-runner@bdfbb3fc649df8187675b880ba8df83a803fd119",
                "caseIntegrityFix": "115b6ce313e51cede71496f267b65b8cd78aa118 (obv/burst7-bs-placement-cases, PR #271)",
                "replayAcceptance": "5f9e29576556f635757eea0605d5a23d29ce1cc5 (obv/burst8-bs-replay-acceptance, PR #275)",
                "supportedCasePairs": "e7ad2abc15d94691305bdb558a752ee535cf374e (obv/burst8-bs-supported-case-pairs, PR #276)",
            },
            "devSettings": dict(TEMPLATE_ENV),
            "stubbedStages": {
                name: f"server.orchestrator.stubs.{STAGES[name].module}"
                for name in STAGES
                if not STAGES[name].stubs_exempt
            },
            "realStages": [name for name in STAGES if STAGES[name].stubs_exempt],
            "tapHazardCase": TAP_CASE_ID,
            "cases": cases,
            "crossCapture": cross_capture,
            "finalizeReplay": replay,
            "blockers": blockers,
        }
        receipt["summary"] = {
            "cases": len(cases),
            "solved": statuses.count("solved"),
            "refused": statuses.count("refused"),
            "refusedDeclared": sum(
                1 for case in cases if case["status"] == "refused" and case["refusal"].get("declared")
            ),
            "mismatched": sum(1 for case in cases if case["mismatches"]),
            "errored": statuses.count("error"),
        }
        receipt["ok"] = not blockers
        return receipt

    finally:
        restore_env()


# ---------------------------------------------------------------------------
# Real downstream mode (`--real-downstream`)
#
# The W8 journey above proves the real placement solver over stubbed criteria and
# result stages. This mode keeps that same front-door journey — upload, finalize,
# the coordinator's dispatch/hydration/publication, events, finalize replay — and
# selects the real criteria (server.criteria.run) and result (server.result.run)
# implementations for the downstream stages, through the normal orchestrator
# path. The upstream boundaries a local box cannot produce — the source scene and
# its occluder asset, the objects_3d output, the meter reads and the scale stamp
# — are injected per capture from the criteria producer's own tiny synthetic
# fixture (server/criteria/tests/data/synthetic_flatwall), stamped with the
# actual capture/run/packet identity. The unavailable GPU/vision stages (validate,
# poses, objects, dense) and the coverage stage stay stubbed: they are the
# upstream inputs this mode is honest about, not its subject. No placement
# decision, verdict or device result is ever injected: the placement solver, the
# criteria engine and the result composer run their real code on the injected
# scene, and the receipt records their separate decisions plus the current
# criteria-to-result policy — never an agreement forged between them, and never
# placement/placement.json dressed up as the device answer.
# ---------------------------------------------------------------------------

REAL_DOWNSTREAM_RECEIPT_SCHEMA = "hss-capture-real-downstream-receipt/v0"

# The stages this mode selects the real implementation for. The selection
# override is installed per run, restored through _PATCHED, and every selected
# callable is recorded with the capture and run it actually served.
REAL_DOWNSTREAM_STAGES = ("criteria", "result")

# Fault-test hook: stages forced back onto the dev stub even in this mode. The
# recorded-callable check then fails the mode — substituting the criteria or
# result stub must never pass silently.
_FORCE_STUB_STAGES: frozenset[str] = frozenset()

# Fault-test hook: drop the real result stage's published result/result.json so
# the required-published-asset check must catch the gap.
_DROP_RESULT_ARTIFACT = False

# The criteria producer's fixture, generated at runtime from its own generator —
# never a committed blob, never re-authored by this runner.
FLATWALL_FIXTURE_PATH = "server/criteria/tests/data/synthetic_flatwall/make.py"

# The reads boundary's synthetic meter number. The reads stub's placeholder is
# deliberately unclean and would review every case; this one is clean so the real
# criteria's meter-number policy is exercised on real verdicts. It is still a
# synthetic placeholder, never a real plate.
REAL_DOWNSTREAM_METER_NUMBER = "23456789"

# The injected same-id object. The scene carries it with a vlm source and the
# objects_3d boundary re-delivers the identical twin, so the coordinator's
# one-time merge must replace it in place and the real criteria's input
# preparation must keep the id unique. (The W8 TAP_HAZARD is tap-sourced; the
# merge contract never replaces a tap-sourced object, so this mode authors a
# replaceable vlm twin instead. The source kind also has to be one the placement
# bridge maps: only vlm, tap and tape convert.)
REAL_DOWNSTREAM_VENT: dict[str, Any] = {
    "id": "obj-tap-vent",
    "class": "vent",
    "wall_id": "w1",
    "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [1.6, 1.8], "bottom_m": 0.5, "top_m": 2.0},
    "confidence": 0.9,
    "plus_minus_m": 0.05,
    "source": {"kind": "vlm"},
    "evidence": "observed",
}

# The blocked case's hazard: a gas meter between the wall's left end and the
# meter anchor, so the clear case's chosen spot is inside the blocked span. The
# placement solver sees it as a real wire hazard (class gas_meter maps); the real
# criteria sees it as the gas_clearance subject. The two supported cases must
# differ in these recorded consequences; the receipt never forces their verdicts
# to agree with each other or with the solver.
REAL_DOWNSTREAM_GAS: dict[str, Any] = {
    "id": "obj-gas",
    "class": "gas_meter",
    "wall_id": "w1",
    "geometry": {"kind": "wall_rect", "wall_id": "w1", "s_m": [-5.0, -1.0], "bottom_m": 0.3, "top_m": 1.4},
    "confidence": 0.99,
    "plus_minus_m": 0.05,
    "source": {"kind": "vlm"},
    "evidence": "observed",
}

REAL_DOWNSTREAM_CASES: tuple[dict[str, Any], ...] = (
    {"id": "flatwall-clear"},
    {"id": "flatwall-gas-blocked", "objects": (REAL_DOWNSTREAM_GAS,)},
    {"id": "flatwall-missing-occluder", "missing_occluder": True},
)


def _real_case_document(case: dict[str, Any]) -> dict[str, Any]:
    """The criteria producer's flatwall fixture, reduced to the scene both real
    engines accept, with nothing authored by this runner beyond the two injected
    objects above.

    The fixture ships keyframes and an in-scene coverage grid; the placement
    bridge refuses both on the local wire format (`/keyframes`,
    `/coverage/grid: grid coverage does not convert; only explicit coverage bands
    do`). Without keyframes the real criteria runs its visibility query as an
    explicit ``no_keyframes`` degradation with unsure runs — the conservative
    branch, recorded per case in the receipt, never a hidden threshold change.
    The meter stays the fixture's wall-anchored point object: the real criteria
    engine requires it, and the bridge accepts it.
    """
    from server.criteria.tests.data.synthetic_flatwall.make import write as write_flatwall

    fixture_dir = Path(tempfile.mkdtemp(prefix="real-downstream-fixture-"))
    try:
        write_flatwall(fixture_dir)
        document = json.loads((fixture_dir / "scene.json").read_text())
    finally:
        shutil.rmtree(fixture_dir, ignore_errors=True)
    document.pop("keyframes", None)
    coverage = dict(document.get("coverage") or {})
    coverage.pop("grid", None)
    document["coverage"] = coverage
    document["assets"] = [asset for asset in document.get("assets") or [] if asset.get("kind") != "coverage_grid"]
    objects = list(document.get("objects") or [])
    objects.append(copy.deepcopy(REAL_DOWNSTREAM_VENT))
    for extra in case.get("objects") or ():
        objects.append(copy.deepcopy(extra))
    document["objects"] = objects
    return document


def _real_laser_bytes() -> bytes:
    """The fixture's laser point cloud: the declared occluder asset the real
    criteria's visibility precondition requires on disk."""
    from server.criteria.tests.data.synthetic_flatwall.make import write as write_flatwall

    fixture_dir = Path(tempfile.mkdtemp(prefix="real-downstream-fixture-"))
    try:
        write_flatwall(fixture_dir)
        return (fixture_dir / "laser.ply").read_bytes()
    finally:
        shutil.rmtree(fixture_dir, ignore_errors=True)


def _stamp_real_scene(
    document: dict[str, Any],
    *,
    case_id: str,
    packet_id: str,
    packet_sha: str,
    capture_id: str,
    run_id: str,
) -> dict[str, Any]:
    stamped = copy.deepcopy(document)
    stamped["scene_id"] = f"real-downstream-{case_id}+{packet_id}+{capture_id}+{run_id}"
    provenance = dict(stamped.get("provenance") or {})
    provenance["inputs"] = {"packet_id": packet_id, "packet_sha256": packet_sha}
    provenance["producer"] = {
        **{key: value for key, value in dict(provenance.get("producer") or {}).items() if key != "path"},
        "path": FLATWALL_FIXTURE_PATH,
    }
    stamped["provenance"] = provenance
    return stamped


def _real_downstream_reads(capture_id: str) -> dict[str, Any]:
    """Reads in the shape both real consumers take: server.result.meter reads the
    meter block, server.criteria.c3.facts.apply_reads copies the fact rows — and
    the verdict contract validates every copied fact row as a criterion entry, so
    the fact carries the full measured/fact/coverage/reason shape (the committed
    criteria reads sample is the reference). Stamped with the actual capture id."""
    value = REAL_DOWNSTREAM_METER_NUMBER
    coverage = {
        "region": {"element_ids": ["meter"], "images": [], "description": "synthetic meter close-up"},
        "status": "observed",
        "probes": 1,
        "observed_share": 1.0,
        "seen_share": 1.0,
        "views_min": 1,
        "views_median": 1.0,
        "best_incidence_deg": None,
        "worst_best_incidence_deg": None,
        "rule": {"min_views": 1, "surface": "free", "max_incidence_deg": None, "observed_share": 1.0},
        "method": "reads/v0",
    }
    return {
        "format": "base-reads/v0",
        "capture_id": capture_id,
        "status": "complete",
        "meter": {"status": "auto_fill", "value": value, "core": value, "confidence": 0.5},
        "confirm": {"candidates": [value]},
        "facts": [
            {
                "id": "meter_number",
                "scope": "site",
                "outcome": "pass",
                "reason": "synthetic clean meter read",
                "measured": None,
                "threshold": None,
                "fact": {"value": value, "clean": True, "raw": value, "coverage": [coverage]},
                "evidence": {"element_ids": ["meter"], "views": [], "coverage": "observed"},
                "views_needed": [],
            }
        ],
    }


def _real_downstream_registry_entry(
    case: dict[str, Any], packet_id: str, packet_sha: str, laser: bytes
) -> dict[str, Any]:
    return {
        "real_downstream": True,
        "case": case,
        "packet_id": packet_id,
        "packet_sha256": packet_sha,
        "scene_document": _real_case_document(case),
        "laser_ply": None if case.get("missing_occluder") else laser,
        "objects_3d": [copy.deepcopy(REAL_DOWNSTREAM_VENT)],
    }


def _real_downstream_scene_stage(ctx: StageContext, entry: dict[str, Any]) -> StageResult:
    """The scene boundary for a real-downstream capture: the case's stamped
    fixture scene plus its declared occluder asset — unless the case declares the
    occluder missing, which is exactly what the real criteria must refuse."""
    case = entry["case"]
    stamped = _stamp_real_scene(
        entry["scene_document"],
        case_id=case["id"],
        packet_id=entry["packet_id"],
        packet_sha=entry["packet_sha256"],
        capture_id=ctx.capture_id,
        run_id=ctx.run_id,
    )
    validate(stamped)
    entry["stamped_scene_id"] = stamped["scene_id"]
    files = {
        "scene/scene.json": _scene_bytes(stamped),
        "scene/build_report.json": json_bytes(
            {
                "status": "capture-placement-runner-real-downstream",
                "caseId": case["id"],
                "packetId": entry["packet_id"],
                "captureId": ctx.capture_id,
                "runId": ctx.run_id,
                "sceneId": stamped["scene_id"],
                "fixturePath": FLATWALL_FIXTURE_PATH,
            }
        ),
    }
    if entry.get("laser_ply"):
        files["scene/laser.ply"] = entry["laser_ply"]
    return write_files(ctx, files)


def _injected_real_reads_run(ctx: StageContext) -> StageResult:
    """The reads boundary: the fixture-shaped reads stamped with this capture, or
    a loud refusal. The default stub reads must not fabricate this case's meter."""
    entry = _REGISTRY.get(ctx.capture_id)
    if entry is None or not entry.get("real_downstream"):
        raise RuntimeError(
            f"capture-placement runner: capture {ctx.capture_id} has no injected reads; "
            "the default stub reads must not fabricate this case's meter"
        )
    return write_files(ctx, {"reads/reads.json": json_bytes(_real_downstream_reads(ctx.capture_id))})


def _injected_real_scale_run(ctx: StageContext) -> StageResult:
    """The scale boundary: the fixture's identity scale stamped with this
    capture's identity, or a loud refusal."""
    entry = _REGISTRY.get(ctx.capture_id)
    if entry is None or not entry.get("real_downstream"):
        raise RuntimeError(f"capture-placement runner: capture {ctx.capture_id} has no injected scale")
    body = {
        "status": "verified",
        "scale_factor": 1.0,
        "origin_m": [0, 0, 0],
        "capture_id": ctx.capture_id,
        "run_id": ctx.run_id,
        "packet_id": entry["packet_id"],
    }
    return write_files(ctx, {"scale/scale.json": json_bytes(body)})


def _injected_real_objects_3d_run(ctx: StageContext) -> StageResult:
    """The objects_3d boundary: this case's injected objects — the same-id vent —
    or a loud refusal. The default stub must not fabricate this case's hazards."""
    entry = _REGISTRY.get(ctx.capture_id)
    if entry is None or not entry.get("real_downstream"):
        raise RuntimeError(
            f"capture-placement runner: capture {ctx.capture_id} has no injected objects_3d; "
            "the default stub must not fabricate this case's hazards"
        )
    body = {"format": "hss-objects-3d/v0", "objects": copy.deepcopy(entry["objects_3d"])}
    return write_files(ctx, {"objects/objects_3d.json": json_bytes(body)})


def _install_real_boundaries() -> None:
    """Swap the scene, objects_3d, reads and scale stub entry points for the
    real-downstream injections. Mode-scoped: restored with the rest of _PATCHED."""
    import server.orchestrator.stubs.objects_3d as objects_3d_stub
    import server.orchestrator.stubs.reads as reads_stub
    import server.orchestrator.stubs.scale as scale_stub
    import server.orchestrator.stubs.scene as scene_stub

    _PATCHED.extend(
        [
            (scene_stub, "run", scene_stub.run),
            (objects_3d_stub, "run", objects_3d_stub.run),
            (reads_stub, "run", reads_stub.run),
            (scale_stub, "run", scale_stub.run),
        ]
    )

    def _scene(ctx: StageContext) -> StageResult:
        entry = _REGISTRY.get(ctx.capture_id)
        if entry is None or not entry.get("real_downstream"):
            raise RuntimeError(
                f"capture-placement runner: capture {ctx.capture_id} has no injected fixture scene; "
                "the default stub scene must not fabricate this case's upstream data"
            )
        return _real_downstream_scene_stage(ctx, entry)

    scene_stub.run = _scene
    objects_3d_stub.run = _injected_real_objects_3d_run
    reads_stub.run = _injected_real_reads_run
    scale_stub.run = _injected_real_scale_run


def _install_real_selection(executed: list[dict[str, Any]]) -> None:
    """Select the real criteria and result implementations for this run.

    The override replaces server.orchestrator.runner.stage_callable for
    REAL_DOWNSTREAM_STAGES only; every other stage keeps the dev selection —
    stub modules for the non-exempt stages, the real placement implementation
    through its stubs_exempt flag. Restored via _PATCHED. The wrapper records the
    callable that actually executed with the capture and run it served, so the
    receipt names real code, not intent.
    """
    import server.orchestrator.runner as runner_module

    original = runner_module.stage_callable
    _PATCHED.append((runner_module, "stage_callable", original))

    def _select(stage: str, *, stubs: bool) -> Callable[[StageContext], StageResult]:
        spec = STAGES[stage]
        if stage in REAL_DOWNSTREAM_STAGES and stage not in _FORCE_STUB_STAGES:
            module = importlib.import_module(f"server.{spec.module}")
            fn = getattr(module, spec.entry)
            return _recorded_real(stage, fn, executed)
        return original(stage, stubs=stubs)

    runner_module.stage_callable = _select


def _recorded_real(
    stage: str, fn: Callable[[StageContext], StageResult], executed: list[dict[str, Any]]
) -> Callable[[StageContext], StageResult]:
    def _run(ctx: StageContext) -> StageResult:
        record: dict[str, Any] = {
            "stage": stage,
            "callable": f"{fn.__module__}:{fn.__qualname__}",
            "captureId": ctx.capture_id,
            "runId": ctx.run_id,
        }
        result = fn(ctx)
        if stage == "criteria":
            prepared = Path(ctx.work_dir) / "criteria-input" / "scene.json"
            if prepared.is_file():
                document = json.loads(prepared.read_text())
                ids = [obj.get("id") for obj in document.get("objects") or []]
                record["criteriaInputObjectCount"] = len(ids)
                record["criteriaInputIdsUnique"] = len(set(ids)) == len(ids)
                record["criteriaInputVentCount"] = ids.count(REAL_DOWNSTREAM_VENT["id"])
        if stage == "result" and _DROP_RESULT_ARTIFACT:
            result = copy.deepcopy(result)
            result.artifacts.pop("result/result.json", None)
        record["publishedKeys"] = sorted(result.artifacts or {})
        executed.append(record)
        return result

    return _run


def _expected_device_kind(verdict_c: dict[str, Any], *, degraded: list[str], failure_code: str | None) -> str:
    """The current criteria-to-result policy, recomputed from
    server.result.compose's lattice: pass serves eligible unless the run is
    degraded or failure-coded; manual_review with requested views serves
    needs_more_photos; manual_review without views serves manual_review; a true
    reject stays hidden behind manual_review (show_reject off). Recomputed here
    so the receipt asserts the policy, never an agreement forced between the two
    engines."""
    decision = verdict_c["site"]["decision"]
    views = verdict_c.get("views_needed") or []
    if decision == "pass":
        kind = "eligible"
    elif decision == "manual_review" and views:
        kind = "needs_more_photos"
    elif decision == "manual_review":
        kind = "manual_review"
    else:
        kind = "not_eligible"
    if (degraded or failure_code) and kind == "eligible":
        kind = "manual_review"
    if kind == "not_eligible":
        kind = "manual_review"
    return kind


def _collect_real_case(
    harness: _Harness,
    case: dict[str, Any],
    journey: dict[str, Any],
    executed: list[dict[str, Any]],
) -> dict[str, Any]:
    deps = harness.app.state.deps
    collected: dict[str, Any] = {
        "id": case["id"],
        "packetId": journey.get("packetId"),
        "packetSha256": journey.get("packetSha256"),
        "captureId": journey.get("captureId"),
        "runId": journey.get("runId"),
        "runIdDeterministic": journey.get("runIdDeterministic"),
        "mismatches": [],
        "errors": list(journey.get("errors") or []),
    }
    if collected["errors"]:
        collected["status"] = "error"
        return collected
    run_id = journey["runId"]
    run = deps.state.get_run(run_id)
    artifacts = _run_artifacts(harness, run_id)

    # The merged scene: this case's stamped fixture scene merged once with the
    # injected objects_3d twin.
    scene_key = artifacts.get("scene/scene.json")
    merged_bytes = deps.storage.get_bytes(scene_key) if scene_key and deps.storage.exists(scene_key) else None
    collected["mergedSceneSha256"] = _sha256(merged_bytes) if merged_bytes else None
    merged = json.loads(merged_bytes) if merged_bytes else {}
    vent_id = REAL_DOWNSTREAM_VENT["id"]
    vents = [obj for obj in merged.get("objects") or [] if obj.get("id") == vent_id]
    collected["mergedVentCount"] = len(vents)
    if len(vents) != 1:
        collected["mismatches"].append(
            f"the injected same-id vent appears {len(vents)} times after the coordinator merge, not exactly once"
        )
    elif vents and vents[0] != REAL_DOWNSTREAM_VENT:
        collected["mismatches"].append("the merged vent is not the injected same-id twin")
    scene_id = merged.get("scene_id") or ""
    if not scene_id.startswith(f"real-downstream-{case['id']}+"):
        collected["mismatches"].append(f"merged scene_id {scene_id!r} does not carry this case's identity stamp")
    gas_id = REAL_DOWNSTREAM_GAS["id"]
    has_gas = any(obj.get("id") == gas_id for obj in merged.get("objects") or [])
    if case["id"] == "flatwall-gas-blocked" and not has_gas:
        collected["mismatches"].append("the blocked case's merged scene is missing its gas_meter hazard")
    if case["id"] == "flatwall-clear" and has_gas:
        collected["mismatches"].append("the clear case's merged scene carries another case's gas_meter hazard")
    normalized = _normalized_geometry(merged)
    collected["mergedGeometrySha256"] = _sha256(json.dumps(normalized, sort_keys=True).encode())

    # The injected boundaries all ran; the unavailable vision stages stayed stubbed.
    for stage in ("scene", "objects_3d", "reads", "scale"):
        record = deps.state.get_stage(run_id, stage)
        status = getattr(record, "status", None)
        if status != "done":
            collected["mismatches"].append(f"injected boundary {stage} status {status!r}, not done")

    # The placement stage: the real bridge and solver on the merged scene.
    placement_record = deps.state.get_stage(run_id, "placement")
    placement_status = getattr(placement_record, "status", None)
    placement_error = getattr(placement_record, "error_code", None)
    collected["placementStage"] = {"status": placement_status, "errorCode": placement_error}
    placement_key = artifacts.get("placement/placement.json")
    placement_body = (
        json.loads(deps.storage.get_bytes(placement_key))
        if placement_key and deps.storage.exists(placement_key)
        else None
    )
    converted_key = artifacts.get("placement/scene.json")
    converted_bytes = (
        deps.storage.get_bytes(converted_key) if converted_key and deps.storage.exists(converted_key) else None
    )
    # The solver's own decision, kept separate from the served device outcome.
    collected["solverDecision"] = (placement_body or {}).get("decision")
    collected["solverChosenCandidate"] = (placement_body or {}).get("chosen_candidate")
    collected["solverStartSM"] = (placement_body or {}).get("start_s_m")
    collected["convertedSceneSha256"] = _sha256(converted_bytes) if converted_bytes else None

    # The criteria stage: the real engine, with its prepared input.
    criteria_record = deps.state.get_stage(run_id, "criteria")
    criteria_status = getattr(criteria_record, "status", None)
    criteria_error = getattr(criteria_record, "error_code", None)
    collected["criteriaStage"] = {"status": criteria_status, "errorCode": criteria_error}
    criteria_calls = [
        item for item in executed if item["stage"] == "criteria" and item["captureId"] == journey.get("captureId")
    ]
    collected["criteriaCallable"] = criteria_calls[0]["callable"] if criteria_calls else None
    collected["criteriaInputVentCount"] = criteria_calls[0].get("criteriaInputVentCount") if criteria_calls else None
    collected["criteriaInputIdsUnique"] = criteria_calls[0].get("criteriaInputIdsUnique") if criteria_calls else None
    verdict_keys = {key: value for key, value in artifacts.items() if key.startswith("verdicts/")}
    verdict_c = None
    if verdict_keys.get("verdicts/verdict.C.json") and deps.storage.exists(verdict_keys["verdicts/verdict.C.json"]):
        verdict_c = json.loads(deps.storage.get_bytes(verdict_keys["verdicts/verdict.C.json"]))
    criteria_report = None
    if verdict_keys.get("verdicts/criteria_report.json") and deps.storage.exists(
        verdict_keys["verdicts/criteria_report.json"]
    ):
        criteria_report = json.loads(deps.storage.get_bytes(verdict_keys["verdicts/criteria_report.json"]))
    collected["criteriaReport"] = {
        "degraded": (criteria_report or {}).get("degraded"),
        "provisional": (criteria_report or {}).get("provisional"),
    }

    # The result stage: the real composer.
    result_record = deps.state.get_stage(run_id, "result")
    result_status = getattr(result_record, "status", None)
    result_error = getattr(result_record, "error_code", None)
    collected["resultStage"] = {"status": result_status, "errorCode": result_error}
    result_key = artifacts.get("result/result.json")
    result_bytes = deps.storage.get_bytes(result_key) if result_key and deps.storage.exists(result_key) else None
    collected["resultSha256"] = _sha256(result_bytes) if result_bytes else None
    result_calls = [
        item for item in executed if item["stage"] == "result" and item["captureId"] == journey.get("captureId")
    ]
    collected["resultCallable"] = result_calls[0]["callable"] if result_calls else None

    capture = harness.client.get(f"/v1/captures/{journey['captureId']}")
    collected["captureStatus"] = capture.json().get("status") if capture.status_code == 200 else None
    terminals = [event for event in journey.get("events") or [] if event["type"] in TERMINAL_EVENTS]
    collected["terminalEventCount"] = len(terminals)
    collected["terminalEvents"] = [event["type"] for event in terminals]

    if case.get("missing_occluder"):
        # The declared-missing occluder is a typed criteria refusal, never a
        # degradation into an evaluated outcome: the real engine raises its own
        # code and no verdicts exist, and the coordinator falls back to its
        # conservative manual-review result — the device boundary then serves
        # that fallback and never claims eligibility.
        if criteria_status != "failed" or criteria_error != "occluders_missing":
            collected["mismatches"].append(
                f"missing-occluder case closed criteria as {criteria_status!r}/{criteria_error!r}, "
                "not the typed occluders_missing refusal"
            )
        if verdict_c is not None:
            collected["mismatches"].append(
                "the missing-occluder case produced verdicts; a typed refusal must not evaluate"
            )
        served = harness.client.get(f"/v1/captures/{journey['captureId']}/result")
        collected["servedResultHttpStatus"] = served.status_code
        collected["servedResultBody"] = served.json() if served.status_code == 200 else None
        if served.status_code != 200:
            collected["mismatches"].append(
                f"the missing-occluder case served HTTP {served.status_code}; the conservative "
                "fallback must stay readable to the device"
            )
        else:
            kind = (served.json().get("outcome") or {}).get("kind")
            collected["servedResultKind"] = kind
            if kind != "manual_review":
                collected["mismatches"].append(
                    f"the missing-occluder case served outcome kind {kind!r}, not manual_review"
                )
        if collected["captureStatus"] not in ("manual_review", "needs_views"):
            collected["mismatches"].append(
                f"the missing-occluder capture closed as {collected['captureStatus']!r}, not a review state"
            )
        collected["failureCode"] = getattr(run, "failure_code", None)
        collected["status"] = "refused"
        return collected

    # Supported cases: every real stage ran the real code, the required assets
    # are published and hashed, and the served boundary follows the policy.
    if placement_status != "done":
        collected["mismatches"].append(
            f"placement stage {placement_status!r}/{placement_error!r}, not done on a supported case"
        )
    if criteria_status != "done":
        collected["mismatches"].append(f"criteria stage {criteria_status!r}/{criteria_error!r}, not done")
    if collected["criteriaCallable"] is None or not str(collected["criteriaCallable"]).startswith("server.criteria:"):
        collected["mismatches"].append(
            f"criteria ran {collected['criteriaCallable']!r}, not the real server.criteria implementation"
        )
    if collected["criteriaInputVentCount"] != 1 or collected["criteriaInputIdsUnique"] is not True:
        collected["mismatches"].append(
            f"the real criteria's prepared input merged the same-id vent {collected['criteriaInputVentCount']} times "
            f"(ids unique: {collected['criteriaInputIdsUnique']})"
        )
    if result_status != "done":
        collected["mismatches"].append(f"result stage {result_status!r}/{result_error!r}, not done")
    if collected["resultCallable"] and not str(collected["resultCallable"]).startswith("server.result:"):
        collected["mismatches"].append(
            f"result ran {collected['resultCallable']!r}, not the real server.result implementation"
        )

    required = {
        "verdicts/verdict.C.json": verdict_keys.get("verdicts/verdict.C.json"),
        "verdicts/criteria_report.json": verdict_keys.get("verdicts/criteria_report.json"),
        "placement/scene.json": converted_key,
        "placement/placement.json": placement_key,
        "result/result.json": result_key,
    }
    hashes: dict[str, str | None] = {}
    for key, storage_key in required.items():
        exists = bool(storage_key) and deps.storage.exists(storage_key)
        hashes[key] = _sha256(deps.storage.get_bytes(storage_key)) if exists else None
        if not exists:
            collected["mismatches"].append(f"required published asset {key} is missing")
    collected["artifactSha256"] = hashes

    if verdict_c is not None and not (isinstance(verdict_c.get("site"), dict) and "decision" in verdict_c["site"]):
        # A non-verdict document (e.g. whatever a substituted stub publishes) must
        # never reach the policy recomputation; the callable check above names it.
        collected["mismatches"].append("verdict.C is not a criteria verdict document (no site decision)")
        verdict_c = None

    if verdict_c is not None:
        degraded = list((criteria_report or {}).get("degraded") or [])
        expected_kind = _expected_device_kind(
            verdict_c, degraded=degraded, failure_code=getattr(run, "failure_code", None)
        )
        collected["criteriaDecision"] = verdict_c["site"]["decision"]
        collected["criteriaReasons"] = verdict_c["site"].get("reasons")
        collected["expectedDeviceKind"] = expected_kind
        gas_checks = [c for c in verdict_c.get("criteria") or [] if c.get("id") == "gas_clearance"]
        gas_row = gas_checks[0] if gas_checks else None
        collected["gasClearanceOutcome"] = gas_row.get("outcome") if gas_row else None
        collected["gasClearanceSubject"] = ((gas_row or {}).get("measured") or {}).get("subject")
        collected["gasClearanceMeasuredFt"] = ((gas_row or {}).get("measured") or {}).get("value_ft")
        collected["viewsNeededCount"] = len(verdict_c.get("views_needed") or [])

        served = harness.client.get(f"/v1/captures/{journey['captureId']}/result")
        served_body = served.json() if served.status_code == 200 else {}
        collected["servedResultHttpStatus"] = served.status_code
        collected["servedResultKind"] = (served_body.get("outcome") or {}).get("kind")
        collected["servedResultRunId"] = served_body.get("runId")
        collected["servedResultStatus"] = served_body.get("status")
        if served.status_code != 200:
            collected["mismatches"].append(f"GET /result returned HTTP {served.status_code}, not 200")
        else:
            try:
                ResultResponse.model_validate(served_body)
            except ValidationError as exc:
                collected["mismatches"].append(f"the served result is not a schema-valid ResultResponse: {exc}")
            if served_body.get("runId") != run_id:
                collected["mismatches"].append(
                    f"served runId {served_body.get('runId')!r} is not this journey's {run_id!r}"
                )
            if collected["servedResultKind"] != expected_kind:
                collected["mismatches"].append(
                    f"served outcome kind {collected['servedResultKind']!r} is not the policy outcome {expected_kind!r}"
                )
            if collected["captureStatus"] != served_body.get("status"):
                collected["mismatches"].append(
                    f"capture status {collected['captureStatus']!r} does not match the served result status "
                    f"{served_body.get('status')!r}"
                )
    if len(terminals) != 1:
        collected["mismatches"].append(f"{len(terminals)} terminal events, not exactly one")
    collected["status"] = "solved" if not collected["mismatches"] else "mismatched"
    return collected


def run_real_downstream(workdir: Path, *, case_ids: list[str] | None = None) -> dict[str, Any]:
    """Run the real-downstream journey: the capture front door over the criteria
    producer's tiny synthetic fixture, with the real placement conversion/solver,
    the real criteria engine and the real result composer, and a compact receipt
    naming what actually ran."""
    restore_env = _apply_dev_env(workdir / "vol")
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        cases = list(REAL_DOWNSTREAM_CASES)
        if case_ids is not None:
            if not case_ids:
                raise SystemExit("empty case selection: name at least one case id, or run every case")
            wanted = set(case_ids)
            cases = [case for case in cases if case["id"] in wanted]
            if len(cases) != len(wanted):
                known = [case["id"] for case in REAL_DOWNSTREAM_CASES]
                raise SystemExit(f"unknown case ids: {sorted(wanted - set(known))}; known: {known}")
        try:
            code_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
            ).stdout.strip()
        except Exception:
            code_sha = "unknown"
        harness = _Harness(workdir / "app")
        executed: list[dict[str, Any]] = []
        laser = _real_laser_bytes()
        _install_real_boundaries()
        _install_real_selection(executed)
        try:
            journeys = []
            for index, case in enumerate(cases):
                journeys.append(
                    _walk_capture(
                        harness,
                        case_id=case["id"],
                        packet_id=f"real-downstream-{index + 1:02d}",
                        register=lambda capture_id, packet_id_, packet_sha, case=case: _REGISTRY.update(
                            {capture_id: _real_downstream_registry_entry(case, packet_id_, packet_sha, laser)}
                        ),
                    )
                )
            harness.drain()
            collected = [
                _collect_real_case(harness, case, journey, executed)
                for case, journey in zip(cases, journeys, strict=True)
            ]
            replay = _replay_finalize(harness, journeys[0]) if journeys else {"ok": False, "reason": "no journeys"}
        finally:
            _remove_boundaries()

        blockers: list[str] = []
        for case in collected:
            for message in case["errors"]:
                blockers.append(f"{case['id']}: {message}")
            for message in case["mismatches"]:
                blockers.append(f"{case['id']}: {message}")
        if not blockers and replay and not replay.get("ok"):
            blockers.append(f"finalize replay is not idempotent: {replay}")

        clear = next((case for case in collected if case["id"] == "flatwall-clear"), None)
        blocked = next((case for case in collected if case["id"] == "flatwall-gas-blocked"), None)
        if clear and blocked and not blockers:
            # The clearance outcome may honestly tie: without keyframes or a
            # coverage grid the engine reads every clearance as unsure. What must
            # differ is whether the criterion is attached to the injected gas
            # meter at all — the blocked case binds gas_clearance to obj-gas and
            # records its measured margin; the clear case has no gas subject.
            if clear.get("gasClearanceSubject") is not None:
                blockers.append(
                    "the clear case's gas_clearance criterion carries a gas-meter subject "
                    f"({clear.get('gasClearanceSubject')!r}); the clear scene has no gas meter"
                )
            if blocked.get("gasClearanceSubject") != "obj-gas":
                blockers.append(
                    "the gas-blocked case's gas_clearance criterion is not attached to the "
                    f"injected gas meter (subject {blocked.get('gasClearanceSubject')!r})"
                )
            if blocked.get("gasClearanceMeasuredFt") is None:
                blockers.append("the gas-blocked case's gas_clearance criterion recorded no measured value")
            if clear.get("mergedGeometrySha256") == blocked.get("mergedGeometrySha256"):
                blockers.append("the two supported cases' merged geometry is identical")
            if clear.get("resultSha256") == blocked.get("resultSha256"):
                blockers.append("the two supported cases' served results are identical")

        statuses = [case["status"] for case in collected]
        receipt = {
            "schemaVersion": REAL_DOWNSTREAM_RECEIPT_SCHEMA,
            "generatedAt": datetime.now(UTC).isoformat(),
            "codeSha": code_sha,
            "devSettings": dict(TEMPLATE_ENV),
            "journey": {
                "frontDoor": "upload → finalize → coordinator dispatch/hydration/publication → events → finalize replay",
                "packetFixture": "data/fixtures/packets/mini-arkit",
            },
            "realStages": {
                "placement": "server.placement.adapter.run (stubs_exempt: the unchanged top-level solver behind POST /v1/placements)",
                "criteria": "server.criteria.run (selected real for this mode)",
                "result": "server.result.run (selected real for this mode)",
            },
            "injectedBoundaries": {
                "scene": "the criteria producer's flatwall fixture, stamped with the actual capture/run/packet identity, plus its laser.ply occluder asset (absent on the missing-occluder case by design)",
                "objects_3d": "the same-id vlm twin obj-tap-vent the coordinator merge must replace in place",
                "reads": "a synthetic clean meter number, capture-stamped",
                "scale": "the identity scale, capture/run/packet-stamped",
            },
            "stubbedStages": ["validate", "poses", "objects", "dense", "coverage"],
            "inputProvenance": {
                "fixture": FLATWALL_FIXTURE_PATH,
                "fixtureSceneId": "synthetic-flatwall",
                "packetSha256s": {case["id"]: case.get("packetSha256") for case in collected},
                "meterNumber": f"synthetic placeholder {REAL_DOWNSTREAM_METER_NUMBER}",
                "reductions": [
                    "keyframes removed: the placement bridge refuses them on the local wire format (/keyframes); the real criteria then runs its visibility query as an explicit no_keyframes degradation with unsure runs",
                    "in-scene coverage grid removed: the bridge refuses it (/coverage/grid: grid coverage does not convert; only explicit coverage bands do); the fixture's explicit bands remain",
                    "the meter stays the fixture's wall-anchored point object: the real criteria engine requires it, and the bridge accepts it",
                ],
            },
            "criteriaToResultPolicy": (
                "pass serves eligible unless the run is degraded or failure-coded; manual_review with requested "
                "views serves needs_more_photos; manual_review without views serves manual_review; a true reject "
                "stays hidden behind manual_review (show_reject off) — recomputed from server.result.compose and "
                "asserted per case, never forced into agreement with the solver's decision"
            ),
            "executedCallables": executed,
            "cases": collected,
            "finalizeReplay": replay,
            "blockers": blockers,
        }
        receipt["summary"] = {
            "cases": len(collected),
            "solved": statuses.count("solved"),
            "refused": statuses.count("refused"),
            "refusedDeclared": sum(
                1
                for case in collected
                if case["status"] == "refused" and case.get("criteriaStage", {}).get("errorCode")
            ),
            "mismatched": statuses.count("mismatched"),
            "errored": statuses.count("error"),
        }
        receipt["ok"] = not blockers
        return receipt

    finally:
        restore_env()


def _redaction_check(receipt: dict[str, Any]) -> str | None:
    text = json.dumps(receipt)
    for needle in (TOKEN, "Bearer", "signature", "/uploads/", "Idempotency"):
        if needle in text:
            return f"receipt redaction check failed: found {needle!r}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay the placement case suite through the real capture front door.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Path to the case manifest.")
    parser.add_argument("--output", type=Path, required=True, help="Where to write the JSON receipt.")
    parser.add_argument("--workdir", type=Path, help="run directory (default: a fresh temp dir)")
    parser.add_argument("--cases", help="comma-separated case ids to run (default: every case)")
    parser.add_argument(
        "--real-downstream",
        action="store_true",
        help=(
            "Run the real-downstream journey instead of the W8 case suite: the real placement solver, "
            "real criteria and real result over the criteria producer's synthetic fixture. --manifest "
            "does not apply; --cases selects real-downstream case ids."
        ),
    )
    args = parser.parse_args(argv)
    owned = args.workdir is None
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="capture-placement-cases-"))
    if args.real_downstream:
        if args.manifest != DEFAULT_MANIFEST:
            parser.error("--manifest does not apply to --real-downstream")
        receipt = run_real_downstream(workdir, case_ids=args.cases.split(",") if args.cases else None)
    else:
        receipt = run_cases(args.manifest, workdir, case_ids=args.cases.split(",") if args.cases else None)
    redaction = _redaction_check(receipt)
    if redaction:
        print(redaction, file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    summary = receipt["summary"]
    print(
        f"cases={summary['cases']} solved={summary['solved']} refused={summary['refused']} "
        f"(declared={summary['refusedDeclared']}) mismatched={summary['mismatched']} errored={summary['errored']}"
    )
    for blocker in receipt["blockers"]:
        print(f"blocker: {blocker}", file=sys.stderr)
    if owned:
        print(f"workdir: {workdir}")
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
