"""Spec, prompt, AR, and size gates for the iOS kit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from ios_kit.ar import frame_to_arkit, transform_points
from ios_kit.prompts import (
    CAPTURE_CHECK_CODES,
    HINT_CODES,
    OUTCOME_HEADLINES,
    load_catalogue,
    matching_entries,
    render,
    select_prompt,
)
from ios_kit.schemas import snake_case_keys

KIT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = KIT_ROOT.parent
MAX_FILE_BYTES = 5_000_000
MAX_ZIP_BYTES = 30_000_000
FORBIDDEN = (
    "pass",
    "fail",
    "reject",
    "eligib",
    "criteri",
    "threshold",
    "grazing",
    "unobserved",
    "candidate",
    "coverage",
    "s_m",
    "tape",
    "measure",
    "ladder",
    "wiring",
)
LONGEST = {
    "side": "to the right of your meter",
    "s_ft": "99",
    "out_ft": "99",
    "what": "air-conditioning unit",
    "threshold_ft": "99",
}
KINDS = ("band", "past_end", "close_up", "confirm_attribute", "region")
BANDS = ("wall", "ground", "overhead", "facing")
CAUSES = (
    "generated_only",
    "unobserved",
    "partial",
    "grazing",
    "unknown_attribute",
    "margin",
    "rule_requires_review",
    "not_evaluated",
)
CRITERIA = (
    "wall_backing",
    "ground_surface",
    "meter_working_space",
    "gas_clearance",
    "ac_clearance",
    "drive_clearance",
    "pool_clearance",
    "opening_clearance",
    "wall_equipment_above",
    "facing_gap",
    "headroom",
    "route_path",
    "route_length",
    "disconnect_space",
    "walkway",
    "street_facing",
    "requires_member_action",
    "placement",
    "meter_number",
    "cl320",
    "oversized_can",
    "meter_type",
    "meter_mount",
    "multiple_mains",
    "main_panel_type",
    "meter_panel_relationship",
    "meter_not_accessible",
    "panel_not_accessible",
    "panel_in_house",
    "not_same_wall",
    "panel_recall",
)
MEMBER_ACTIONS = ("retake_meter_closeups", "truedepth_meter_scan")
CUSTOMER_PREFIXES = ("vn.", "hint.", "capture_check.", "ma.")


def check_spec(spec_path: Path) -> int:
    problems: list[str] = []
    server_packet = REPO_ROOT / "server/contracts/schemas/capture-packet.v0.4.schema.json"
    kit_packet = KIT_ROOT / "schemas/capture-packet.v0.4.schema.json"
    if server_packet.read_bytes() != kit_packet.read_bytes():
        problems.append("ios-kit packet schema is not byte-identical to server/contracts/schemas")
    for name in ("result.v0.schema.json", "api.v1.schema.json"):
        server = REPO_ROOT / "server/contracts/schemas" / name
        kit = KIT_ROOT / "schemas" / name
        if server.read_bytes() != kit.read_bytes():
            problems.append(f"ios-kit/schemas/{name} is not byte-identical to the server export")
    appendix = _appendix_schema(spec_path)
    exported = json.loads(server_packet.read_text())
    drifted = _diff(appendix, exported, "$")
    if drifted:
        problems.append("Appendix A differs from server/contracts/schemas/capture-packet.v0.4.schema.json:")
        problems.extend(f"  {row}" for row in drifted[:12])
    for name in ("example-packet.json", "example-packet-photo-only.json"):
        left = KIT_ROOT / "examples/packets" / name
        right = REPO_ROOT / "data/fixtures/packets/examples" / name
        if not right.is_file():
            problems.append(f"missing fixture example {right}")
        elif left.read_bytes() != right.read_bytes():
            problems.append(f"examples/packets/{name} differs from data/fixtures")
    meter = REPO_ROOT / "data/fixtures/packets/mini-arkit/packet.json"
    copy = KIT_ROOT / "examples/packets/example-packet-meter-stills.json"
    if not meter.is_file():
        problems.append("SD-29 packet data/fixtures/packets/mini-arkit/packet.json is not on this tree")
    elif not copy.is_file() or copy.read_bytes() != meter.read_bytes():
        problems.append("example-packet-meter-stills.json is not a byte copy of the SD-29 packet")
    if problems:
        print("parity failed")
        print("\n".join(problems))
        return 1
    print("parity ok")
    return 0


def check_prompts() -> int:
    catalogue = load_catalogue()
    problems: list[str] = []
    capture_codes, hint_codes = _code_lists()
    for code in capture_codes:
        _expect_one(catalogue, problems, f"capture_check {code}", allow_generic=False, capture_check_code=code)
    for code in hint_codes:
        _expect_one(catalogue, problems, f"hint {code}", allow_generic=False, hint_code=code)
    for kind in KINDS:
        for band in BANDS:
            _expect_one(catalogue, problems, f"{kind}×{band}", kind=kind, band=band)
    for cause in CAUSES:
        _expect_one(catalogue, problems, f"cause {cause}", cause=cause)
    for criterion in CRITERIA:
        winner = _expect_one(catalogue, problems, f"criterion {criterion}", criterion=criterion)
        if winner and winner.prompt_id != f"reason.{criterion}":
            problems.append(f"criterion {criterion} selected {winner.prompt_id}")
    for action in MEMBER_ACTIONS:
        _expect_one(catalogue, problems, f"member {action}", member_action=action)
    for kind, (title, body) in OUTCOME_HEADLINES.items():
        winner = _expect_one(catalogue, problems, f"outcome {kind}", outcome=kind)
        if winner and (winner.title != title or winner.body != body):
            problems.append(f"outcome {kind} headline drifted")
    for entry in catalogue.entries:
        if not entry.prompt_id.startswith(CUSTOMER_PREFIXES):
            continue
        if entry.prompt_id.startswith("reason.") or entry.prompt_id.startswith("outcome."):
            continue
        try:
            rendered = render(entry, **LONGEST)
        except KeyError as exc:
            problems.append(f"{entry.prompt_id}: {exc}")
            continue
        if len(rendered.title) > catalogue.title_chars:
            problems.append(f"{entry.prompt_id}: title {len(rendered.title)} chars")
        if len(rendered.body) > catalogue.body_chars:
            problems.append(f"{entry.prompt_id}: body {len(rendered.body)} chars")
        lowered = f"{rendered.title} {rendered.body}".lower()
        for word in FORBIDDEN:
            if word in lowered:
                problems.append(f"{entry.prompt_id}: forbidden {word}")
        if any(ch.isdigit() and "." in token for token in rendered.body.split() for ch in token):
            problems.append(f"{entry.prompt_id}: distance is not a whole number")
    if problems:
        print("prompts failed")
        print("\n".join(problems[:40]))
        return 1
    print(f"prompts ok ({len(catalogue.entries)} entries)")
    return 0


def check_ar(path: Path) -> int:
    result = json.loads(path.read_text())
    problems = snake_case_keys(result)
    outcome = result.get("outcome") or {}
    frame = outcome.get("frameToArkit")
    if frame is None:
        print(f"{path.name}: no AR frame")
        return 1 if problems else 0
    recovered = frame_to_arkit(frame)
    placement = (outcome.get("recommendedPlacement") or {}).get("boxArkitWorld")
    box = (outcome.get("recommendedPlacement") or {}).get("boxSceneM")
    if placement and box:
        mapped = transform_points(frame, [box["centerM"]])[0]
        translation = placement["pose"][12:15]
        if _err(mapped, translation) > 1e-6:
            problems.append(f"box centre error {_err(mapped, translation)}")
    views = list(outcome.get("viewsNeeded") or []) + list(result.get("viewsNeeded") or [])
    seen: set[str] = set()
    for view in views:
        if view.get("id") in seen:
            continue
        seen.add(view.get("id"))
        region = view.get("targetRegionArkit") or {}
        polygon = region.get("polygon") or []
        for point in polygon:
            back = transform_points(recovered, [point])[0]
            again = transform_points(frame, [back])[0]
            if _err(point, again) > 1e-6:
                problems.append(f"{view.get('id')} round-trip {_err(point, again)}")
        if region.get("surface") == "wall":
            for point in polygon:
                if abs(point[2] + 3.25) > 1e-6:
                    problems.append(f"{view.get('id')} wall z {point[2]}")
        if region.get("surface") == "ground":
            for point in polygon:
                if abs(point[1] + 1.40) > 1e-6:
                    problems.append(f"{view.get('id')} ground y {point[1]}")
        pose = view.get("suggestedCameraPoseArkit")
        if not pose:
            continue
        eye_y, eye_z = pose[13], pose[14]
        if view.get("band") == "wall" and (abs(eye_y) > 0.02 or abs(eye_z + 0.25) > 0.02):
            problems.append(f"{view.get('id')} wall camera y={eye_y} z={eye_z}")
        if view.get("band") == "ground" and abs(eye_y + 0.10) > 0.02:
            problems.append(f"{view.get('id')} ground camera y={eye_y}")
    if problems:
        print("ar failed")
        print("\n".join(problems[:20]))
        return 1
    print(f"ar ok {path.name}")
    return 0


def check_size() -> int:
    problems: list[str] = []
    for path in KIT_ROOT.rglob("*"):
        if path.is_file() and path.stat().st_size > MAX_FILE_BYTES:
            problems.append(f"{path.relative_to(KIT_ROOT)} is {path.stat().st_size} bytes")
    archive = REPO_ROOT / "dist/ios-kit-0.4.0.zip"
    if archive.is_file():
        size = archive.stat().st_size
        print(f"zip {size} bytes")
        if size >= MAX_ZIP_BYTES:
            problems.append(f"zip is {size} bytes")
    else:
        print("zip: absent")
    if problems:
        print("size failed")
        print("\n".join(problems))
        return 1
    print("size ok")
    return 0


def _expect_one(catalogue, problems: list[str], label: str, *, allow_generic: bool = True, **query: str):
    hits = [entry for entry in matching_entries(catalogue, **query) if entry.prompt_id != "vn.generic"]
    if len(hits) == 1:
        return hits[0]
    if len(hits) == 0:
        try:
            winner = select_prompt(catalogue, **query)
        except KeyError:
            problems.append(f"{label}: no prompt")
            return None
        if winner.prompt_id == "vn.generic" and allow_generic:
            return winner
        problems.append(f"{label}: no prompt")
        return None
    problems.append(f"{label}: {len(hits)} matches")
    return hits[0] if hits else None


def _code_lists() -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        from server.contracts.capture_check import CaptureCheckCode, HintCode
    except ImportError:
        return CAPTURE_CHECK_CODES, HINT_CODES
    return get_args(CaptureCheckCode), get_args(HintCode)


def _appendix_schema(spec_path: Path) -> dict:
    text = spec_path.read_text()
    start = text.index("## Appendix A")
    end = text.index("## Appendix B")
    chunk = text[start:end]
    body = chunk.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(body)


def _diff(left, right, prefix: str) -> list[str]:
    if type(left) is not type(right):
        return [f"{prefix}: {type(left).__name__} vs {type(right).__name__}"]
    if isinstance(left, dict):
        rows: list[str] = []
        for key in sorted(set(left) | set(right)):
            if key not in left:
                rows.append(f"{prefix}.{key}: missing in Appendix A")
            elif key not in right:
                rows.append(f"{prefix}.{key}: missing in server schema")
            else:
                rows.extend(_diff(left[key], right[key], f"{prefix}.{key}"))
        return rows
    if isinstance(left, list):
        if left != right:
            return [f"{prefix}: list differs"]
        return []
    if left != right:
        return [f"{prefix}: {left!r} vs {right!r}"]
    return []


def _err(left, right) -> float:
    return max(abs(a - b) for a, b in zip(left, right, strict=True))
