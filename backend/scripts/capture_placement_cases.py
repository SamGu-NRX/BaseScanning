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

from server.api.app import create_app  # noqa: E402
from server.api.deps import drain_finalize  # noqa: E402
from server.common.config import Settings  # noqa: E402
from server.contracts.jobs import StageContext, StageResult, run_id_for_capture  # noqa: E402
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


def _geometry_sha256(document: dict[str, Any] | None) -> str | None:
    """The merged scene's sha256 with its identity stamp removed.

    ``scene_id`` and ``provenance.inputs`` name this case, packet, capture and
    run — they differ across captures by construction, so hashing the stored
    bytes verbatim would make the cross-capture distinctness check compare
    identity stamps rather than geometry, and it could only ever fail when a
    scene artifact is missing entirely. Stripping the identity leaves everything
    the contract says about the world itself.
    """
    if document is None:
        return None
    stripped = copy.deepcopy(document)
    stripped.pop("scene_id", None)
    provenance = dict(stripped.get("provenance") or {})
    provenance.pop("inputs", None)
    if provenance:
        stripped["provenance"] = provenance
    else:
        stripped.pop("provenance", None)
    return _sha256(_scene_bytes(stripped))


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
    """One capture for one case, through the device's own sequence."""
    journey: dict[str, Any] = {"caseId": record.id, "packetId": packet_id}
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

    # Register the case at both boundaries before finalize can start the pipeline.
    # The tap hazard is delivered only where the upstream source does not already
    # carry it: the merge contract replaces only detector/VLM twins, so a same-id
    # re-delivery of a source-carried (tap-sourced) hazard is never replaced — it
    # duplicates the id and the merged scene is refused as inconsistent. A
    # source-carried hazard still reaches the one-time tap checks through the real
    # merge and real conversion below.
    source_object_ids = {obj.get("id") for obj in json.loads(record.source_path.read_text()).get("objects") or []}
    inject_tap = record.id == TAP_CASE_ID and TAP_HAZARD["id"] not in source_object_ids
    _REGISTRY[capture_id] = {
        "record": record,
        "packet_id": packet_id,
        "packet_sha256": journey["packetSha256"],
        "objects_3d": [copy.deepcopy(TAP_HAZARD)] if inject_tap else [],
    }

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
    case["mergedGeometrySha256"] = _geometry_sha256(json.loads(merged_bytes) if merged_bytes else None)
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
    args = parser.parse_args(argv)
    owned = args.workdir is None
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="capture-placement-cases-"))
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
