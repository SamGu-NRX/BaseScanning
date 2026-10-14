import hashlib
import io
import json
import re
import struct
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from hsverify import e2e
from hsverify.e2e import (
    Endpoint,
    SceneInput,
    bundle_zip,
    discover_endpoint,
    hostile_inputs,
    judge,
    judge_hostile,
    load_app_export,
    load_input,
    zip_bomb,
)
from hsverify.resultcheck import Need, RuleSet

RULES = RuleSet(
    width_ft=31 / 12,
    depth_ft=22 / 12,
    needs={"ground_surface": (Need("ground", 0.0),), "gas_clearance": (Need("ground", 1.0),)},
    errors={
        "tap": 0.3,
        "vlm": 1.5,
        "tape": 0.05,
        "wall": 0.3,
        "mesh": 0.5,
        "plane": 0.75,
        "meter": 0.3,
        "drift_per_ft": 0.16,
    },
    step_ft=1 / 6,  # the server's 2 in
    wall_join_ft=0.6,
    battery_height_ft=3.25,
    headroom_ft=6.5,
)

SCENE = {
    "meter": {"pos": [0.0, 4.0, 0.0], "wall_id": "w1"},
    "walls": [{"id": "w1", "baseline": [[-10.0, 0.0], [10.0, 0.0]]}],
    "coverage": {"observed": [{"band": "wall", "span_ft": [-10.0, 10.0]}]},
}


def openapi(paths: dict) -> dict:
    return {
        "paths": paths,
        "components": {
            "schemas": {
                "Upload": {"properties": {"bundle": {"type": "string", "format": "binary"}}}
            }
        },
    }


def json_op() -> dict:
    return {"post": {"requestBody": {"content": {"application/json": {"schema": {}}}}}}


def test_single_json_endpoint_is_found():
    assert discover_endpoint(openapi({"/place": json_op()})) == Endpoint(
        "/place", "application/json"
    )


def test_multipart_endpoint_names_its_file_field():
    op = {
        "post": {
            "requestBody": {
                "content": {
                    "multipart/form-data": {"schema": {"$ref": "#/components/schemas/Upload"}}
                }
            }
        }
    }
    assert discover_endpoint(openapi({"/scenes": op})) == Endpoint(
        "/scenes", "multipart/form-data", "bundle"
    )


def test_one_path_with_json_and_upload_uses_json_as_the_app_does():
    both = {
        "post": {
            "requestBody": {
                "content": {
                    "application/json": {"schema": {}},
                    "application/zip": {"schema": {"type": "string", "format": "binary"}},
                    "multipart/form-data": {"schema": {"$ref": "#/components/schemas/Upload"}},
                }
            }
        }
    }
    assert discover_endpoint(openapi({"/v1/placements": both})) == Endpoint(
        "/v1/placements", "application/json", None, accepts_zip=True
    )


def test_a_post_answering_with_svg_is_not_the_placement_endpoint():
    svg = json_op()
    svg["post"]["responses"] = {"200": {"content": {"image/svg+xml": {}}}}
    found = discover_endpoint(openapi({"/v1/placements": json_op(), "/v1/plan.svg": svg}))
    assert found.path == "/v1/placements"


def test_a_scene_named_path_wins_among_several():
    found = discover_endpoint(openapi({"/login": json_op(), "/placement": json_op()}))
    assert found.path == "/placement"


def test_ambiguity_is_loud():
    with pytest.raises(SystemExit, match="Pass --endpoint"):
        discover_endpoint(openapi({"/a": json_op(), "/b": json_op()}))
    with pytest.raises(SystemExit, match="Pass --endpoint"):
        discover_endpoint(openapi({}))
    assert discover_endpoint(openapi({"/a": json_op(), "/b": json_op()}), "/b").path == "/b"


def test_named_post_without_a_body_schema_takes_json():
    raw_body = {"post": {"responses": {}}}
    found = discover_endpoint(openapi({"/v1/placements": raw_body}), "/v1/placements")
    assert found == Endpoint("/v1/placements", "application/json")
    with pytest.raises(SystemExit, match="not a POST"):
        discover_endpoint(openapi({"/v1/placements": raw_body}), "/v1/other")


def test_case_file_is_loaded_with_its_expectations(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "id": "c1",
                "scene": SCENE,
                "expect": {"decision_not": ["pass"]},
                "rules_assumed": {"gas": 3.0},
            }
        )
    )
    item = load_input(path)
    assert item.name == "c1"
    assert item.expect == {"decision_not": ["pass"]}
    assert json.loads(item.raw) == SCENE


def test_case_pointing_at_a_scene_outside_git(tmp_path):
    scene = tmp_path / "scene.json"
    scene.write_text(json.dumps(SCENE))
    case = tmp_path / "case.json"
    case.write_text(
        json.dumps(
            {
                "id": "real-1",
                "real": True,
                "scene_path": str(scene),
                "expect": {"decision_not": ["pass"]},
            }
        )
    )
    item = load_input(case)
    assert (item.name, item.real, item.expect) == ("real-1", True, {"decision_not": ["pass"]})
    scene.unlink()
    assert load_input(case).skip_reason.endswith("not built here; see the case's README")


class FakeServer(BaseHTTPRequestHandler):
    """Answers every scene with a consistent manual_review; `decide_pass` breaks C5."""

    decide_pass = False

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers["Content-Type"].startswith("multipart/"):
            start = body.index(b"PK")  # the zip starts after the part headers
            import io
            import zipfile

            raw = zipfile.ZipFile(io.BytesIO(body[start:])).read("scene.json")
        else:
            raw = body
        scene = json.loads(raw)
        passing = self.decide_pass
        result = {
            "schema_version": "1.0",
            "decision": "pass" if passing else "manual_review",
            "summary": "",
            "reasons": [{"code": "policy_not_approved", "message": ""}],
            "policy": {
                "id": None,
                "version": None,
                "auto_approve": passing,
                "sources": [],
                "rules_sha256": "0" * 64,
            },
            "spot": None,
            "route": None,
            # Every check a server lists, passing with no numbers, so no check is left out.
            "checks": [
                {
                    "id": i,
                    "label": i,
                    "outcome": "pass",
                    "reason": "",
                    "measured_ft": None,
                    "plus_minus_ft": None,
                    "threshold_ft": None,
                    "comparison": "at_least",
                    "rule": {"key": i, "source": "", "placeholder": False},
                }
                for i in (
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
                )
            ],
            "missing_evidence": [],
            "ends": {},
            "sweep": [
                {
                    "wall_id": scene["walls"][0]["id"],
                    "start_ft": [1.0, 2.0],
                    "outcome": "pass" if passing else "unsure",
                    "failing": [],
                    "unsure": [],
                }
            ],
            "stats": {
                "candidates": 1,
                "pass": int(passing),
                "unsure": int(not passing),
                "fail": 0,
                "elapsed_ms": 1.0,
                "input_sha256": hashlib.sha256(raw).hexdigest(),
            },
        }
        payload = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_url():
    server = HTTPServer(("127.0.0.1", 0), FakeServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    FakeServer.decide_pass = False


SCHEMAS = {"scene": {}, "result": {}}


def item() -> SceneInput:
    raw = json.dumps(SCENE).encode()
    return SceneInput("fake", SCENE, raw, expect={"decision_not": ["pass"]})


@pytest.mark.parametrize(
    "endpoint",
    [Endpoint("/place", "application/json"), Endpoint("/place", "multipart/form-data", "file")],
)
def test_consistent_server_passes(fake_url, endpoint):
    record = judge(fake_url, endpoint, item(), SCHEMAS, RULES)
    assert record["status"] == "pass", record["problems"]
    assert record["decision"] == "manual_review"


def test_server_breaking_coverage_rule_fails(fake_url):
    FakeServer.decide_pass = True
    record = judge(fake_url, Endpoint("/place", "application/json"), item(), SCHEMAS, RULES)
    assert record["status"] == "fail"
    joined = "\n".join(record["problems"])
    assert "ruled out for this case" in joined
    assert "decision pass without a passing spot" in joined
    assert "no coverage:" not in joined  # Initial invariant failure stops transformed requests.
    assert re.search(r"needs ground \[.*\] observed, none seen", joined)


def test_a_result_breaking_its_schema_counts_against_the_contract(fake_url):
    strict = {"scene": {}, "result": {"required": ["nothing_has_this"]}}
    record = judge(fake_url, Endpoint("/place", "application/json"), item(), strict, RULES)
    assert record["status"] == "fail"
    assert record["contract_problems"] and record["contract_problems"] == record["problems"]


def test_bad_input_is_not_blamed_on_the_server(fake_url):
    strict = {"scene": {"required": ["nothing_has_this"]}, "result": {}}
    record = judge(fake_url, Endpoint("/place", "application/json"), item(), strict, RULES)
    assert record["status"] == "bad input"


class RefusingServer(BaseHTTPRequestHandler):
    """Refuses every request with 422 after `delay` seconds."""

    delay = 0.0
    content_types: ClassVar[list[str]] = []

    def do_POST(self):
        RefusingServer.content_types.append(self.headers["Content-Type"])
        self.rfile.read(int(self.headers["Content-Length"]))
        time.sleep(self.delay)
        self.send_response(422)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


@pytest.fixture
def refusing_url():
    server = HTTPServer(("127.0.0.1", 0), RefusingServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    RefusingServer.delay = 0.0


def test_a_quick_refusal_of_a_hostile_input_passes(refusing_url):
    hostile = hostile_inputs()[3]
    record = judge_hostile(refusing_url, Endpoint("/p", "application/json"), hostile, SCHEMAS)
    assert record["status"] == "pass" and record["http_status"] == 422


def test_a_slow_refusal_or_a_hang_fails(refusing_url, monkeypatch):
    monkeypatch.setattr(e2e, "HOSTILE_BUDGET_MS", 100.0)
    RefusingServer.delay = 0.3
    hostile = hostile_inputs()[3]
    record = judge_hostile(refusing_url, Endpoint("/p", "application/json"), hostile, SCHEMAS)
    assert record["status"] == "fail" and "budget 100" in record["problems"][0]
    RefusingServer.delay = 6.0  # longer than budget + 5 s: the request times out
    record = judge_hostile(refusing_url, Endpoint("/p", "application/json"), hostile, SCHEMAS)
    assert record["status"] == "fail" and record["problems"][0].startswith("no answer within")


def test_hostile_inputs_are_small_to_send():
    items = hostile_inputs()
    assert all(i.hostile for i in items)
    assert max(len(bundle_zip(i)) for i in items) < 2_000_000  # the harness stays light


def test_the_compressed_scene_expands_to_its_size():
    with zipfile.ZipFile(io.BytesIO(zip_bomb(3))) as z:
        assert z.getinfo("scene.json").file_size == 3 * 2**20 + len(b'{"meter": 1}')


def test_a_sim_report_folder_is_an_app_export_with_its_sha(tmp_path):
    with zipfile.ZipFile(tmp_path / "scan.zip", "w") as z:
        z.writestr("scene.json", json.dumps(SCENE))
    (tmp_path / "report.json").write_text(json.dumps({"sha": "abc123", "app_export": "scan.zip"}))
    item = load_app_export(tmp_path)
    assert (item.app_export, item.app_sha, item.real) == (True, "abc123", True)
    (tmp_path / "report.json").write_text(json.dumps({"sha": "abc123"}))
    with pytest.raises(SystemExit, match="never reached the upload"):
        load_app_export(tmp_path)


def test_a_prebuilt_zip_goes_as_application_zip_when_the_endpoint_takes_it(refusing_url):
    RefusingServer.content_types.clear()
    bomb = hostile_inputs()[4]
    json_only = Endpoint("/p", "application/json")
    assert judge_hostile(refusing_url, json_only, bomb, SCHEMAS)["status"] == "fail"
    zip_too = Endpoint("/p", "application/json", accepts_zip=True)
    assert judge_hostile(refusing_url, zip_too, bomb, SCHEMAS)["status"] == "pass"
    assert RefusingServer.content_types == ["application/zip"]


# --- What a run may claim --------------------------------------------------------------------

META = {
    "started_at": "2026-09-26T12:00:00-05:00",
    "server_ref": "origin/t3/server",
    "server_sha": "a" * 40,
    "endpoint": "POST /v1/placements (application/json)",
    "load_average_1_5_15": [1.0, 1.0, 1.0],
    "peak_memory": {"harness_mb": 50.0},
}
CHECKED = {
    "name": "a",
    "status": "pass",
    "decision": "manual_review",
    "http_status": 200,
    "latency_ms": 120.0,
    "real": True,
    "contract_problems": [],
}


@pytest.mark.parametrize(
    "records",
    [
        [],
        [{"name": "s", "status": "skipped", "problems": [], "skip_reason": "no data"}],
        [{"name": "b", "status": "bad input", "problems": ["scene schema: x"]}],
        [{"name": "h", "status": "pass", "hostile": True, "http_status": 422, "problems": []}],
    ],
    ids=["no scenes", "skipped only", "bad input only", "hostile only"],
)
def test_a_run_that_checked_no_answer_claims_nothing(tmp_path, records):
    report = e2e.write_report(tmp_path, META, records)
    assert (report["scenes_checked"], report["contract_ok"], report["all_passed"]) == (
        0,
        False,
        False,
    )


def test_a_checked_scene_credits_the_contract(tmp_path):
    report = e2e.write_report(tmp_path, META, [CHECKED])
    assert (report["scenes_checked"], report["contract_ok"], report["latency_ms"]) == (
        1,
        True,
        120.0,
    )
    broken = CHECKED | {"status": "fail", "contract_problems": ["x"], "problems": ["x"]}
    assert e2e.write_report(tmp_path, META, [broken])["contract_ok"] is False


def test_a_fast_refusal_of_a_real_scene_is_not_latency(tmp_path):
    refused = {
        "name": "r",
        "status": "fail",
        "real": True,
        "http_status": 422,
        "latency_ms": 8.0,
        "problems": ["HTTP 422"],
        "contract_problems": ["HTTP 422"],
    }
    report = e2e.write_report(tmp_path, META, [refused])
    assert report["latency_ms"] is None and report["scenes_answered"] == 0


def test_an_oversized_bundle_is_refused_before_it_is_read(tmp_path):
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z, z.open("scene.json", "w") as f:
        chunk = b" " * 2**20
        for _ in range(e2e.MAX_SCENE_BYTES // 2**20 + 1):
            f.write(chunk)
    with pytest.raises(SystemExit, match=r"declares \d+ bytes, limit"):
        load_input(archive)


def test_a_bundle_with_too_many_entries_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(e2e, "MAX_BUNDLE_ENTRIES", 3)
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for i in range(4):
            z.writestr(f"k{i}.jpg", b"x")
    with pytest.raises(SystemExit, match="4 entries, limit 3"):
        load_input(archive)


@pytest.mark.parametrize("false_footer_count", [False, True])
def test_bundle_counts_directories_before_zipfile_allocation(
    tmp_path, monkeypatch, false_footer_count
):
    monkeypatch.setattr(e2e, "MAX_BUNDLE_ENTRIES", 3)
    archive = tmp_path / "directories.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("scene.json", b"{}")
        for i in range(3):
            z.writestr(f"directory{i}/", b"")
    if false_footer_count:
        data = bytearray(archive.read_bytes())
        struct.pack_into("<HH", data, len(data) - 22 + 8, 1, 1)
        archive.write_bytes(data)

    def metadata_allocation_forbidden(*args, **kwargs):
        pytest.fail("ZipFile allocated metadata before archive admission")

    monkeypatch.setattr(e2e.zipfile, "ZipFile", metadata_allocation_forbidden)
    with pytest.raises(SystemExit, match="4 entries, limit 3"):
        e2e.read_bundle(archive)


@pytest.mark.parametrize("status", [200, 413])
def test_response_read_is_bounded_for_success_and_http_error(monkeypatch, status):
    from urllib.error import HTTPError

    class Response(io.BytesIO):
        headers: ClassVar[dict] = {}

        def read(self, size=-1):
            assert 0 <= size <= 4 * 2**20 + 1, "response read must have a byte limit"
            return super().read(size)

    response = Response(b"x" * (4 * 2**20 + 1))
    response.status = status

    def urlopen(*args, **kwargs):
        if status != 200:
            raise HTTPError("http://unused", status, "too large", {}, response)
        return response

    monkeypatch.setattr(e2e.urllib.request, "urlopen", urlopen)
    code, body, _ = e2e.post("http://unused", Endpoint("/place", "application/json"), item())
    assert code is None
    assert b"response exceeds" in body


@pytest.mark.parametrize("status", [200, 413])
def test_incomplete_response_is_a_clean_transport_failure(monkeypatch, status):
    from http.client import IncompleteRead
    from urllib.error import HTTPError

    class Response(io.BytesIO):
        headers: ClassVar[dict] = {}

        def read(self, size=-1):
            raise IncompleteRead(b"partial", 50)

    response = Response()
    response.status = status

    def urlopen(*args, **kwargs):
        if status != 200:
            raise HTTPError("http://unused", status, "truncated", {}, response)
        return response

    monkeypatch.setattr(e2e.urllib.request, "urlopen", urlopen)
    code, body, _ = e2e.post("http://unused", Endpoint("/place", "application/json"), item())
    assert code is None and b"IncompleteRead" in body


def test_short_content_length_response_is_a_clean_transport_failure(monkeypatch):
    response = io.BytesIO(b"{}")
    response.status = 200
    response.headers = {"Content-Length": "10"}
    monkeypatch.setattr(e2e.urllib.request, "urlopen", lambda *args, **kwargs: response)
    code, body, _ = e2e.post("http://unused", Endpoint("/place", "application/json"), item())
    assert code is None and b"truncated response" in body


def test_a_200_that_is_not_a_json_object_is_a_failure_not_a_crash(monkeypatch):
    for body in (b"<html>oops</html>", b"[1, 2]"):
        monkeypatch.setattr(e2e, "post", lambda *args, body=body, **kwargs: (200, body, 5.0))
        endpoint = Endpoint("/place", "application/json")
        record = judge("http://unused", endpoint, item(), SCHEMAS, RULES)
        assert record["status"] == "fail"
        assert "not a JSON object" in record["contract_problems"][0]
        hostile = judge_hostile("http://unused", endpoint, hostile_inputs()[3], SCHEMAS)
        assert hostile["status"] == "fail" and "not a JSON object" in hostile["problems"][0]


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "skipped", "skip_reason": "missing scene"},
        {"status": "fail", "expectation_problems": ["wrong decision"], "contract_problems": []},
    ],
)
def test_run_acceptance_is_separate_from_contract_invariants(tmp_path, extra):
    report = e2e.write_report(tmp_path, META, [CHECKED, {"name": "other", **extra}])
    assert not report["all_passed"]
    assert report["contract_ok"]


def test_unexercised_large_input_probe_fails_contract():
    item = hostile_inputs()[-1]
    item.bundle = b"archive"
    record = judge_hostile("unused", Endpoint("/place", "application/json"), item, {})
    assert record["status"] == "fail" and record["contract_problems"]


def test_bundle_uses_exact_canonical_scene(tmp_path):
    archive = tmp_path / "scene.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("old-scene.json", b"old")
        z.writestr("scene.json", b"canonical")
    assert e2e.read_bundle(archive)[0] == b"canonical"


def test_bundle_reads_one_wrapped_capture_and_ignores_macos_metadata(tmp_path):
    archive = tmp_path / "scene.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("capture/", b"")
        z.writestr("capture/scene.json", b"canonical")
        z.writestr("capture/frame.jpg", b"photo")
        z.writestr("__MACOSX/capture/._scene.json", b"metadata")
        z.writestr("__MACOSX/capture/frame.jpg", b"metadata")
    assert e2e.read_bundle(archive) == (b"canonical", {"frame.jpg": b"photo"})


def test_bundle_root_scene_takes_precedence_over_wrapped_scene(tmp_path):
    archive = tmp_path / "scene.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("scene.json", b"canonical")
        z.writestr("capture/scene.json", b"other")
    assert e2e.read_bundle(archive)[0] == b"canonical"


@pytest.mark.parametrize(
    "names",
    [
        ["old-scene.json"],
        ["scene.json", "scene.json"],
        ["capture/scene.json", "capture/scene.json"],
        ["first/scene.json", "second/scene.json"],
        ["capture/scene.json", "other/frame.jpg"],
        ["capture/nested/scene.json"],
    ],
)
def test_bundle_rejects_missing_or_duplicate_canonical_scene(tmp_path, names):
    archive = tmp_path / "scene.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name in names:
            z.writestr(name, b"{}")
    with pytest.raises(SystemExit, match=r"exactly one canonical scene\.json"):
        e2e.read_bundle(archive)


def test_transformed_answer_checks_its_own_hash(monkeypatch):
    calls = []
    original = {"decision": "pass", "stats": {}, "sweep": []}
    monkeypatch.setattr(
        e2e, "post", lambda *args, **kwargs: (200, json.dumps(original).encode(), 1)
    )
    monkeypatch.setattr(e2e, "answer", lambda *args: (original, None))
    monkeypatch.setattr(e2e, "schema_errors", lambda *args: [])

    def check_hash(scene, result, sent=None, rules=None, *, meter=None):
        calls.append((scene, sent))
        return ["stale input hash"] if sent is not None else []

    monkeypatch.setattr(e2e, "invariant_problems", check_hash)
    monkeypatch.setattr(e2e, "with_ground_short_of", lambda *args: None)
    monkeypatch.setattr(e2e, "with_more_error", lambda *args: None)
    monkeypatch.setattr(e2e, "tape_to_tap", lambda *args: None)
    monkeypatch.setattr(e2e, "with_less_coverage", lambda *args: None)
    scene = {
        "walls": [{"id": "w1", "baseline": [[-1, 0], [1, 0]]}],
        "meter": {"pos": [0, 1, 0], "wall_id": "w1"},
    }
    probe = SceneInput(name="test", scene=scene, raw=json.dumps(scene).encode(), source="test")
    failures = e2e.property_problems(
        "unused", Endpoint("/p", "application/json"), probe, original, {"result": {}}, RULES
    )
    assert calls and all(raw == json.dumps(scene, indent=1).encode() for scene, raw in calls)
    assert any("stale input hash" in p for p in failures)


def hostile_records():
    return [
        {
            "name": name,
            "hostile": True,
            "hostile_probe_executed": True,
            "http_status": 422,
            "latency_ms": 1.0,
            "status": "pass",
            "contract_problems": [],
        }
        for name in e2e.HOSTILE_PROBE_NAMES
    ]


@pytest.mark.parametrize("missing", range(5))
def test_every_required_hostile_probe_must_be_exercised(tmp_path, missing):
    probes = hostile_records()
    omitted = probes.pop(missing)
    probes.append(probes[0].copy())  # duplicate evidence cannot replace the missing probe
    report = e2e.write_report(tmp_path, META, [CHECKED, *probes])
    assert report["all_passed"] and report["contract_ok"]
    assert not report["hostile_probes_complete"] and not report["full_contract_ok"]
    assert omitted["name"] not in report["hostile_probes_executed"]


def test_probe_name_without_execution_is_not_full_evidence(tmp_path):
    probes = hostile_records()
    del probes[0]["hostile_probe_executed"]
    assert not e2e.write_report(tmp_path, META, [CHECKED, *probes])["full_contract_ok"]
    assert e2e.write_report(tmp_path, META, [CHECKED, *hostile_records()])["full_contract_ok"]


def test_hostile_execution_is_recorded_only_after_sending(monkeypatch):
    monkeypatch.setattr(e2e, "post", lambda *args, **kwargs: (422, b"{}", 1.0))
    probe = SceneInput(e2e.HOSTILE_PROBE_NAMES[0], {}, b"{}", hostile=True)
    record = judge("unused", Endpoint("/place", "application/json"), probe, SCHEMAS, RULES)
    assert record["hostile_probe_executed"] is True
    probe.bundle = b"archive"
    record = judge("unused", Endpoint("/place", "application/json"), probe, SCHEMAS, RULES)
    assert not record.get("hostile_probe_executed", False)


@pytest.mark.parametrize("no_hostile", [False, True])
def test_cli_shortened_run_cannot_claim_full_contract(tmp_path, monkeypatch, no_hostile):
    from pathlib import Path

    # Only public rules are read. All HTTP, git and hostile payload generation are mocked.
    public_rules = (Path(__file__).resolve().parents[2] / "server" / "rules.yaml").read_text()
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "scene.json").write_text(json.dumps(SCENE))
    monkeypatch.setattr(e2e.gitref, "fetch", lambda: None)
    monkeypatch.setattr(e2e.gitref, "resolve", lambda _: "a" * 40)
    monkeypatch.setattr(
        e2e.gitref, "show", lambda sha, path: public_rules if path.endswith("rules.yaml") else "{}"
    )
    monkeypatch.setattr(
        e2e.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(openapi({"/place": json_op()})).encode()),
    )
    monkeypatch.setattr(
        e2e,
        "hostile_inputs",
        lambda: [SceneInput(name, {}, b"{}", hostile=True) for name in e2e.HOSTILE_PROBE_NAMES],
    )

    def fake_judge(url, endpoint, probe, *args):
        if probe.hostile:
            return next(r for r in hostile_records() if r["name"] == probe.name)
        return CHECKED | {"name": probe.name}

    monkeypatch.setattr(e2e, "judge", fake_judge)
    monkeypatch.setattr(e2e, "peak_rss_mb", lambda: {})
    out = tmp_path / "out"
    args = ["--server-url", "http://unused", "--cases", str(cases), "--out", str(out)]
    if no_hostile:
        args.append("--no-hostile")
    assert e2e.main(args) == 0
    data = json.loads((out / "report.json").read_text())
    assert data["all_passed"] and data["contract_ok"]
    assert data["full_contract_ok"] is (not no_hostile)
    assert data["hostile_probes_executed"] == (
        [] if no_hostile else sorted(e2e.HOSTILE_PROBE_NAMES)
    )
    from hsverify import scoreboard

    metric = next(m for m in scoreboard.load_metrics()[1] if m.id == "S2-8")
    metric.probe["glob"] = str(out / "report.json")
    status = scoreboard.probe_report(metric.probe, data["sha"]).status
    assert status == ("gap" if no_hostile else "met")
