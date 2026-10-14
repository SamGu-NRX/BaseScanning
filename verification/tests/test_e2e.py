import contextlib
import hashlib
import io
import json
import re
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
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
    assert "no coverage: decision pass for a scene with no coverage at all" in joined
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


class UnendingServer(BaseHTTPRequestHandler):
    """Answers `status` with a body that never ends: written as fast as the client takes it
    (`flood`), one byte at a time (`trickle`), or never reached because the headers come one
    byte at a time (`slow headers`). Each answer stops on its own after 5 s."""

    status = 200
    mode = "flood"

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        head = f"HTTP/1.0 {self.status} X\r\nContent-Type: application/json\r\n".encode()
        until = time.monotonic() + 5
        with contextlib.suppress(OSError):
            if self.mode == "slow headers":
                self.wfile.write(head)
                while time.monotonic() < until:
                    for byte in b"X-Pad: a\r\n":
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.02)
                return
            self.wfile.write(head + b"\r\n")
            while time.monotonic() < until:
                self.wfile.write(b" " * 4096 if self.mode == "flood" else b" ")
                self.wfile.flush()
                if self.mode == "trickle":
                    time.sleep(0.02)

    def log_message(self, *args):
        pass


@pytest.fixture
def unending_url():
    class Server(ThreadingHTTPServer):
        block_on_close = False  # an answer still running must not hold up the next test

    server = Server(("127.0.0.1", 0), UnendingServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("status", [200, 500])
def test_an_endless_body_is_cut_at_the_byte_cap(unending_url, monkeypatch, status):
    monkeypatch.setattr(e2e, "MAX_RESPONSE_BYTES", 2**14)
    monkeypatch.setattr(UnendingServer, "status", status)
    got, payload, ms = e2e.post(unending_url, Endpoint("/p", "application/json"), item(), 5)
    assert got is None and payload == f"HTTP {status} body over 16384 bytes".encode()
    assert ms < 4000


@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize("mode", ["trickle", "slow headers"])
def test_a_trickled_answer_stops_at_the_overall_deadline(unending_url, monkeypatch, status, mode):
    """Every byte arrives well inside the 0.5 s socket timeout; only the deadline ends it."""
    monkeypatch.setattr(UnendingServer, "status", status)
    monkeypatch.setattr(UnendingServer, "mode", mode)
    got, payload, ms = e2e.post(unending_url, Endpoint("/p", "application/json"), item(), 0.5)
    assert (got, payload) == (None, b"no answer within 0.5 s")
    assert 500 <= ms < 2500


class ShortServer(BaseHTTPRequestHandler):
    """Answers `status`, then closes before the body is done: 10 of a promised 100 bytes
    (`length`), or partway through a chunked body (`chunked`)."""

    status = 200
    mode = "length"

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.protocol_version = "HTTP/1.1"
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        if self.mode == "length":
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"outcome"')
        else:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b'a\r\n{"outcome"\r\n14\r\n: "pa')
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize("mode", ["length", "chunked"])
def test_an_answer_cut_short_is_no_answer(monkeypatch, status, mode):
    monkeypatch.setattr(ShortServer, "status", status)
    monkeypatch.setattr(ShortServer, "mode", mode)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ShortServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        got, payload, _ = e2e.post(url, Endpoint("/p", "application/json"), item(), 5)
    finally:
        server.shutdown()
        server.server_close()
    assert got is None and payload.startswith(b"IncompleteRead")


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
    assert judge_hostile(refusing_url, json_only, bomb, SCHEMAS)["status"] == "skipped"
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


def test_a_200_that_is_not_a_json_object_is_a_failure_not_a_crash(monkeypatch):
    for body in (b"<html>oops</html>", b"[1, 2]"):
        monkeypatch.setattr(e2e, "post", lambda *args, body=body, **kwargs: (200, body, 5.0))
        endpoint = Endpoint("/place", "application/json")
        record = judge("http://unused", endpoint, item(), SCHEMAS, RULES)
        assert record["status"] == "fail"
        assert "not a JSON object" in record["contract_problems"][0]
        hostile = judge_hostile("http://unused", endpoint, hostile_inputs()[3], SCHEMAS)
        assert hostile["status"] == "fail" and "not a JSON object" in hostile["problems"][0]
