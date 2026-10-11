import copy
import html
import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import api
from scene import parse_scene
from solver import solve

SERVER = Path(__file__).resolve().parents[1]
RESULT_VALIDATOR = Draft202012Validator(
    json.loads((SERVER / "schemas" / "result.schema.json").read_text())
)
EXAMPLE_BYTES = (SERVER / "tests" / "fixtures" / "example-scene.json").read_bytes()
EXAMPLE = json.loads(EXAMPLE_BYTES)
# A short wall solves in milliseconds; tests about transport, not placement, upload this one. It
# names one keyframe image and one still, like a real bundle.
SMALL = {
    "meter": {"pos": [0.0, 5.0, 0.0], "wall_id": "w1"},
    "walls": [{"id": "w1", "baseline": [[-8.0, 0.0], [8.0, 0.0]], "height_ft": 9}],
    "ground": [{"type": "lawn", "polygon": [[-8, 0], [8, 0], [8, 20], [-8, 20]]}],
    "coverage": {
        "observed": [
            {"band": "wall", "span_ft": [-8, 8]},
            {"band": "ground", "span_ft": [-8, 8], "out_ft": 20},
        ]
    },
    "keyframes": [
        {
            "id": "k1",
            "pose": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2.0, 4.5, 9.0, 1],
            "intrinsics": [1450.0, 1450.0, 960.0, 720.0],
            "w": 1920,
            "h": 1440,
            "img": "k1.jpg",
        }
    ],
    "stills": {"meter_close": "meter_close.jpg"},
}
SMALL_BYTES = json.dumps(SMALL).encode()
JPEG = b"\xff\xd8\xff\xe0 synthetic test image \xff\xd9"
PLACEMENTS = "/v1/placements"
SITE_PLAN = "/v1/placements/site-plan.svg"
_JSON = {"content-type": "application/json"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(api.app)


def bundle(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def example_bundle(
    prefix: str = "", drop: str | None = None, scene: bytes = EXAMPLE_BYTES
) -> bytes:
    files = {"scene.json": scene, "k1.jpg": JPEG, "meter_close.jpg": JPEG}
    return bundle({prefix + k: v for k, v in files.items() if k != drop})


def post_zip(client: TestClient, data: bytes, media: str = "application/zip"):
    return client.post(PLACEMENTS, content=data, headers={"content-type": media})


def error(resp) -> dict:
    body = resp.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message", "path"}, body
    return body["error"]


def same_decision(a: dict, b: dict) -> None:
    assert a["decision"] == b["decision"]
    assert a["spot"] == b["spot"]
    assert [(c["id"], c["outcome"]) for c in a["checks"]] == [
        (c["id"], c["outcome"]) for c in b["checks"]
    ]


# --- health ---------------------------------------------------------------------------------------


def test_health_reports_the_loaded_policy(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["schema_version"] == "1.0"
    policy = api.LOADED.rules.policy
    assert body["policy"] == {
        "id": policy.id,
        "version": policy.version,
        "auto_approve": policy.auto_approve and policy.id is not None,
        "allow_reject": policy.allow_reject,
        "sources": list(api.LOADED.sources),
        "rules_sha256": api.LOADED.sha256,
        "notice": policy.notice,
    }


def test_health_policy_matches_what_a_placement_reports(client: TestClient) -> None:
    placed = client.post(PLACEMENTS, content=SMALL_BYTES, headers=_JSON).json()
    assert client.get("/health").json()["policy"] == placed["policy"]


# --- accepted uploads -----------------------------------------------------------------------------


def test_bare_json_returns_a_schema_valid_result(client: TestClient) -> None:
    resp = client.post(PLACEMENTS, content=EXAMPLE_BYTES, headers=_JSON)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    RESULT_VALIDATOR.validate(result)
    direct = solve(parse_scene(copy.deepcopy(EXAMPLE), api.LOADED.rules), api.LOADED)
    same_decision(result, direct)


def test_zip_bundle_gives_the_same_decision_as_bare_json(client: TestClient) -> None:
    bare = client.post(PLACEMENTS, content=EXAMPLE_BYTES, headers=_JSON).json()
    resp = post_zip(client, example_bundle())
    assert resp.status_code == 200, resp.text
    zipped = resp.json()
    RESULT_VALIDATOR.validate(zipped)
    same_decision(zipped, bare)
    # The input hash is over scene.json's bytes, so both routes name the same input.
    assert zipped["stats"]["input_sha256"] == bare["stats"]["input_sha256"]


@pytest.mark.parametrize("media", ["application/octet-stream", "application/json"])
def test_zip_is_recognised_by_its_magic_bytes(client: TestClient, media: str) -> None:
    resp = post_zip(client, example_bundle(scene=SMALL_BYTES), media)
    assert resp.status_code == 200, resp.text


def test_zip_with_scene_inside_one_top_level_folder(client: TestClient) -> None:
    data = example_bundle("capture-2026-09-26/", scene=SMALL_BYTES)
    # A macOS Finder zip also carries __MACOSX/, which must not count as a second folder.
    with zipfile.ZipFile(io.BytesIO(data), "a") as zf:
        zf.writestr("__MACOSX/capture-2026-09-26/._scene.json", b"resource fork")
    assert post_zip(client, data).status_code == 200


def test_multipart_bundle_field_accepts_zip_and_json(client: TestClient) -> None:
    as_zip = client.post(
        PLACEMENTS,
        files={"bundle": ("capture.zip", example_bundle(scene=SMALL_BYTES), "application/zip")},
    )
    as_json = client.post(
        PLACEMENTS, files={"bundle": ("scene.json", SMALL_BYTES, "application/json")}
    )
    assert as_zip.status_code == 200, as_zip.text
    assert as_json.status_code == 200, as_json.text
    same_decision(as_zip.json(), as_json.json())


# --- refusals -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("missing", "path"),
    [("k1.jpg", "/keyframes/0/img"), ("meter_close.jpg", "/stills/meter_close")],
)
def test_zip_missing_a_referenced_image_is_422_naming_it(
    client: TestClient, missing: str, path: str
) -> None:
    resp = post_zip(client, example_bundle(drop=missing))
    assert resp.status_code == 422
    err = error(resp)
    assert err["code"] == "missing_bundle_file"
    assert missing in err["message"]
    assert err["path"] == path


def test_bare_json_skips_the_image_check(client: TestClient) -> None:
    # SMALL names k1.jpg and meter_close.jpg; a bare upload carries no files to check.
    assert client.post(PLACEMENTS, content=SMALL_BYTES, headers=_JSON).status_code == 200


@pytest.mark.parametrize("name", ["../x", "a/../../x", "/etc/x", "C:/x", "..\\x"])
def test_zip_entry_escaping_the_bundle_is_400(client: TestClient, name: str) -> None:
    data = bundle({"scene.json": EXAMPLE_BYTES, "k1.jpg": JPEG, "meter_close.jpg": JPEG, name: b""})
    resp = post_zip(client, data)
    assert resp.status_code == 400
    err = error(resp)
    assert err["code"] == "unsafe_zip_entry"
    assert repr(name) in err["message"]


def test_zip_without_scene_json_is_400(client: TestClient) -> None:
    resp = post_zip(client, bundle({"a/scene.json": b"{}", "b/k1.jpg": JPEG}))
    assert resp.status_code == 400
    assert error(resp)["code"] == "missing_scene_json"


def test_corrupt_zip_is_400(client: TestClient) -> None:
    resp = post_zip(client, b"PK\x03\x04 not really a zip")
    assert resp.status_code == 400
    assert error(resp)["code"] == "unreadable_zip"


def test_malformed_json_is_422(client: TestClient) -> None:
    resp = client.post(PLACEMENTS, content=b'{"meter": ', headers=_JSON)
    assert resp.status_code == 422
    err = error(resp)
    assert err["code"] == "invalid_json"
    assert err["path"] is None


def test_nan_is_not_json(client: TestClient) -> None:
    text = EXAMPLE_BYTES.decode().replace('"plus_minus_ft": 0.3}', '"plus_minus_ft": NaN}', 1)
    resp = client.post(PLACEMENTS, content=text.encode(), headers=_JSON)
    assert resp.status_code == 422
    assert error(resp)["code"] == "invalid_json"


def test_unknown_scene_field_is_422_with_its_path(client: TestClient) -> None:
    scene = copy.deepcopy(EXAMPLE)
    scene["objects"][1]["plusminus_ft"] = 0.3
    resp = client.post(PLACEMENTS, json=scene)
    assert resp.status_code == 422
    err = error(resp)
    assert err["code"] == "invalid_scene"
    assert err["path"] == "/objects/1"
    assert "plusminus_ft" in err["message"]


def test_scene_error_after_the_schema_carries_its_path(client: TestClient) -> None:
    scene = copy.deepcopy(EXAMPLE)
    scene["meter"]["wall_id"] = "nowhere"
    resp = client.post(PLACEMENTS, json=scene)
    assert resp.status_code == 422
    assert error(resp)["path"] == "/meter/wall_id"


@pytest.mark.parametrize("media", ["text/plain", "application/xml", "image/jpeg"])
def test_unsupported_content_type_is_415(client: TestClient, media: str) -> None:
    resp = client.post(PLACEMENTS, content=EXAMPLE_BYTES, headers={"content-type": media})
    assert resp.status_code == 415
    assert error(resp)["code"] == "unsupported_media_type"


def test_oversized_body_is_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", len(EXAMPLE_BYTES) - 1)
    resp = client.post(PLACEMENTS, content=EXAMPLE_BYTES, headers=_JSON)
    assert resp.status_code == 413
    assert error(resp)["code"] == "body_too_large"


def test_oversized_body_without_content_length_is_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 1000)

    def chunks():
        for _ in range(10):
            yield b" " * 200

    resp = client.post(PLACEMENTS, content=chunks(), headers=_JSON)
    assert resp.status_code == 413


def test_bundle_that_unpacks_too_large_is_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "MAX_UNZIPPED_BYTES", len(EXAMPLE_BYTES) + 10)
    resp = post_zip(client, example_bundle())
    assert resp.status_code == 413
    assert error(resp)["code"] == "bundle_too_large"


def test_empty_body_is_400(client: TestClient) -> None:
    resp = client.post(PLACEMENTS, content=b"", headers=_JSON)
    assert resp.status_code == 400
    assert error(resp)["code"] == "empty_body"


def test_multipart_with_a_field_other_than_bundle_is_400(client: TestClient) -> None:
    resp = client.post(PLACEMENTS, files={"scene": ("scene.json", EXAMPLE_BYTES)})
    assert resp.status_code == 400
    assert error(resp)["code"] == "unexpected_form_field"


def test_multipart_with_two_files_is_400(client: TestClient) -> None:
    resp = client.post(
        PLACEMENTS,
        files=[("bundle", ("a.json", EXAMPLE_BYTES)), ("bundle", ("b.json", EXAMPLE_BYTES))],
    )
    assert resp.status_code == 400
    assert error(resp)["code"] == "unreadable_multipart"


def test_unknown_route_uses_the_error_envelope(client: TestClient) -> None:
    resp = client.get("/v1/nothing-here")
    assert resp.status_code == 404
    assert error(resp)["code"] == "not_found"


@pytest.mark.parametrize("value", ["abc", "0", "-5", "inf", "nan"])
def test_bad_size_limit_env_fails_loudly(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("HOUSESCAN_MAX_UPLOAD_MB", value)
    with pytest.raises(ValueError, match="HOUSESCAN_MAX_UPLOAD_MB"):
        api._limit_bytes("HOUSESCAN_MAX_UPLOAD_MB", 256)


def test_size_limit_env_is_megabytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOUSESCAN_MAX_UPLOAD_MB", "1.5")
    assert api._limit_bytes("HOUSESCAN_MAX_UPLOAD_MB", 256) == 3 * 512 * 1024


# --- CORS -----------------------------------------------------------------------------------------


def test_cors_preflight_allows_any_origin(client: TestClient) -> None:
    resp = client.options(
        PLACEMENTS,
        headers={
            "origin": "https://example.org",
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "POST" in resp.headers["access-control-allow-methods"]


# --- site plan ------------------------------------------------------------------------------------


def test_site_plan_is_svg_for_the_same_upload(client: TestClient) -> None:
    data = example_bundle(scene=SMALL_BYTES)
    placed = post_zip(client, data).json()
    svg = client.post(SITE_PLAN, content=data, headers={"content-type": "application/zip"})
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.text.startswith("<svg")
    assert html.escape(placed["summary"]) in svg.text


def test_site_plan_refuses_bad_input_like_placements(client: TestClient) -> None:
    resp = client.post(SITE_PLAN, content=b"{", headers=_JSON)
    assert resp.status_code == 422
    assert error(resp)["code"] == "invalid_json"


# --- speed ----------------------------------------------------------------------------------------


def test_solving_the_example_takes_under_a_second() -> None:
    scene = parse_scene(copy.deepcopy(EXAMPLE), api.LOADED.rules)
    started = time.perf_counter()
    solve(scene, api.LOADED)
    assert time.perf_counter() - started < 1.0


# --- OpenAPI --------------------------------------------------------------------------------------


def test_openapi_publishes_the_scene_request_body(client: TestClient) -> None:
    # Clients (the verification harness, a future SDK) discover the upload formats here.
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/v1/placements", "/v1/placements/site-plan.svg"):
        content = paths[path]["post"]["requestBody"]["content"]
        assert set(content) == {"application/json", "application/zip", "multipart/form-data"}
        assert content["multipart/form-data"]["schema"]["required"] == ["bundle"]
