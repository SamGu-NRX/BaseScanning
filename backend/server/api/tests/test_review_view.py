"""The reviewer page boundary: GET /v1/captures/{id}/review.

The stored review.html is composed once, but served many times. The #255 review
found the served page unjudgeable long after render: baked signed URLs expire
after HSS_INGEST_RESULT_URL_TTL_S and the three.js import points at a CDN. These
tests drive the route the way a reviewer does — through the FastAPI app with the
real template rendered into volume storage — and read the exact HTML back.
"""

from __future__ import annotations

import base64
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
from jwt import encode as jwt_encode

from server.api.app import create_app
from server.api.limits import Limiter
from server.api.routes import result as result_routes
from server.api.tests.conftest import REVIEWER, TOKEN, create_body
from server.common.backends.storage_volume import mint_token
from server.common.config import Settings
from server.contracts.jobs import RunRecord, StageRecord

RESULT_DATA = Path(__file__).resolve().parents[3] / "server" / "result" / "tests" / "data"
# The template is read as a file, not imported: server.api may not import a stage module.
REVIEW_DIR = Path(__file__).resolve().parents[2] / "result" / "review"
RUN_ID = "run_review_view"
REVIEWER_HEADERS = {"Authorization": f"Bearer {REVIEWER}"}


def _create(client: TestClient, packet_id: str = "packet-review-0001") -> str:
    made = client.post("/v1/captures", json=create_body(packet_id), headers={"Idempotency-Key": packet_id})
    assert made.status_code == 201, made.text
    return made.json()["captureId"]


def _render_review_html(
    capture_id: str, *, signed_photo: str | None, signed_link: str | None, photos: list[dict] | None = None
) -> bytes:
    """A page exactly as compose.py writes it: signed URLs where storage could sign."""

    if photos is None:
        photos = [{"id": "kf_1", "url": signed_photo or "stills/kf_01.jpg", "purpose": "keyframe"}]
    links = [signed_link or "verdict.C.json"]
    context = {
        "capture_id": capture_id,
        "run_id": RUN_ID,
        "profiles": [{"name": "C", "decision": "eligible", "device": True}],
        "true_decision": "eligible",
        "failure_code": None,
        "degraded": [],
        "scale": None,
        "timings": {},
        "vlm_cost": None,
        "decidability": None,
        "photos": photos,
        "criteria": [],
        "walls": [],
        "views": [],
        "meter": {"status": "unverified", "value": None, "candidates": [], "evidence": None},
        "links": links,
    }
    env = Environment(loader=FileSystemLoader(REVIEW_DIR), autoescape=select_autoescape(["html"]))
    template = env.get_template("template.html")
    rendered = template.render(
        **context,
        css=(REVIEW_DIR / "style.css").read_text(),
        three_url="https://unpkg.com/three@0.160.0/build/three.module.js",
    )
    return rendered.encode()


def _seed(
    client: TestClient,
    capture_id: str,
    review_html: bytes | None,
    *,
    stages: list[StageRecord] | None = None,
    scene: dict | None = None,
    still: bytes | None = None,
    preview: bytes | None = None,
    run_id: str = RUN_ID,
    created_at: str = "2026-09-26T00:00:00.000Z",
) -> None:
    deps = client.app_state
    artifacts = {}
    if review_html is not None:
        review_key = f"captures/{capture_id}/runs/{run_id}/result/review.html"
        deps.storage.put_bytes(review_key, review_html, "text/html")
        artifacts["result/review.html"] = review_key
    if scene is not None:
        scene_key = f"captures/{capture_id}/runs/{run_id}/scene/scene.json"
        deps.storage.put_bytes(scene_key, json.dumps(scene).encode(), "application/json")
        artifacts["scene/scene.json"] = scene_key
    if still is not None:
        deps.storage.put_bytes(f"captures/{capture_id}/stills/kf_01.jpg", still, "image/jpeg")
    if preview is not None:
        preview_key = f"captures/{capture_id}/runs/{run_id}/result/preview.ply"
        deps.storage.put_bytes(preview_key, preview, "application/octet-stream")
        artifacts["result/preview.ply"] = preview_key
    deps.state.put_run(
        RunRecord(
            run_id=run_id,
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at=created_at,
            deadline_at="2026-09-26T01:00:00.000Z",
            artifacts=artifacts,
            degraded=["dense"],
        )
    )
    for record in stages or []:
        deps.state.upsert_stage(record)


def test_review_is_reviewer_only(client: TestClient):
    capture_id = _create(client)
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    device = client.get(f"/v1/captures/{capture_id}/review")
    assert device.status_code == 403
    assert device.json()["errors"][0]["code"] == "forbidden"
    files = client.get(f"/v1/captures/{capture_id}/review/files/verdicts/verdict.C.json")
    assert files.status_code == 403
    ok = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert ok.status_code == 200


def test_review_404_result_not_ready(client: TestClient):
    capture_id = _create(client, "packet-review-0002")
    _seed(client, capture_id, None)
    body = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert body.status_code == 404
    assert body.json()["errors"][0]["code"] == "result_not_ready"
    file_body = client.get(f"/v1/captures/{capture_id}/review/files/verdicts/verdict.C.json", headers=REVIEWER_HEADERS)
    assert file_body.status_code == 404
    assert file_body.json()["errors"][0]["code"] == "result_not_ready"


def test_stale_signed_links_are_served_fresh(client: TestClient):
    """A page rendered with signed URLs that expired stays usable past the TTL."""

    capture_id = _create(client, "packet-review-0003")
    verdict_key = f"captures/{capture_id}/runs/{RUN_ID}/verdicts/verdict.C.json"
    verdict_bytes = (RESULT_DATA / "synthetic" / "verdicts" / "verdict.C.json").read_bytes()
    still_key = f"captures/{capture_id}/stills/kf_01.jpg"
    client.app_state.storage.put_bytes(verdict_key, verdict_bytes, "application/json")
    stale_photo = client.app_state.storage.signed_get_url(still_key, 2)
    stale_link = client.app_state.storage.signed_get_url(verdict_key, 2)
    html = _render_review_html(capture_id, signed_photo=stale_photo, signed_link=stale_link)
    assert stale_photo in html.decode()  # the stored page really carries the signed URL
    _seed(
        client,
        capture_id,
        html,
        scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
        still=b"\xff\xd8fakejpeg",
    )
    time.sleep(3.2)  # tokens mint exp=int(now)+2 with a strict `<` check, so expiry
    # is only guaranteed once more than a full second has passed past the TTL
    expired = client.get(stale_photo)
    assert expired.status_code != 200  # the old page would show a broken image now
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert served.status_code == 200
    body = served.text
    assert "/v1/uploads/" not in body, "an expired signed URL reached the served page"
    assert f"/v1/captures/{capture_id}/review/files/stills/kf_01.jpg" in body
    fresh = client.get(f"/v1/captures/{capture_id}/review/files/verdicts/verdict.C.json", headers=REVIEWER_HEADERS)
    assert fresh.status_code == 200
    assert fresh.content == verdict_bytes


def test_photos_and_raw_files_point_at_the_route(client: TestClient):
    capture_id = _create(client, "packet-review-0004")
    html = _render_review_html(capture_id, signed_photo=None, signed_link=None)
    _seed(
        client,
        capture_id,
        html,
        scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
        still=b"\xff\xd8fakejpeg",
    )
    verdict_key = f"captures/{capture_id}/runs/{RUN_ID}/verdicts/verdict.C.json"
    client.app_state.storage.put_bytes(
        verdict_key,
        (RESULT_DATA / "synthetic" / "verdicts" / "verdict.C.json").read_bytes(),
        "application/json",
    )
    for name in ("result/result.json", "result/result.internal.json"):
        client.app_state.storage.put_bytes(
            f"captures/{capture_id}/runs/{RUN_ID}/{name}", json.dumps({"stub": True}).encode(), "application/json"
        )
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    body = served.text
    assert f'src="/v1/captures/{capture_id}/review/files/stills/kf_01.jpg?rtok=' in body
    assert "verdicts/verdict.C.json" in body
    assert "result/result.internal.json" in body
    photo = client.get(f"/v1/captures/{capture_id}/review/files/stills/kf_01.jpg", headers=REVIEWER_HEADERS)
    assert photo.status_code == 200
    assert photo.headers["content-type"].startswith("image/jpeg")


def test_three_js_import_is_served_locally(client: TestClient):
    capture_id = _create(client, "packet-review-0005")
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert 'import * as THREE from "/v1/review-static/three.module.js?rtok=' in served.text
    asset = client.get("/v1/review-static/three.module.js")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("text/javascript")
    assert len(asset.content) > 1_000_000  # the real pinned module, not a stub


def test_runtime_section_lists_stages_codes_and_sha(client: TestClient):
    capture_id = _create(client, "packet-review-0006")
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    deps = client.app_state
    deps.state.upsert_stage(
        StageRecord(run_id=RUN_ID, stage="scene", status="done", attempt=1, heartbeat="2026-09-26T00:00:00.000Z")
    )
    deps.state.upsert_stage(
        StageRecord(run_id=RUN_ID, stage="placement", status="skipped", attempt=1, heartbeat="2026-09-26T00:00:00.000Z")
    )
    deps.state.upsert_stage(
        StageRecord(
            run_id=RUN_ID,
            stage="criteria",
            status="failed",
            attempt=1,
            heartbeat="2026-09-26T00:00:00.000Z",
            error_code="criteria_failed",
        )
    )
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    body = served.text
    assert "Run status" in body
    assert RUN_ID in body
    assert "0123456789abcdef" in body  # GIT_SHA from the test settings
    assert "dense" in body  # the run's degraded list
    assert "<td>scene</td><td>ran</td>" in body
    assert "<td>placement</td><td>stand-in (skipped)</td>" in body
    assert "<td>criteria</td><td>failed</td><td>criteria_failed</td>" in body


def test_file_route_rejects_traversal(client: TestClient):
    capture_id = _create(client, "packet-review-0007")
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    verdict_key = f"captures/{capture_id}/runs/{RUN_ID}/verdicts/verdict.C.json"
    client.app_state.storage.put_bytes(
        verdict_key,
        (RESULT_DATA / "synthetic" / "verdicts" / "verdict.C.json").read_bytes(),
        "application/json",
    )
    # A control: an encoded slash is a normal path separator, so a real file resolves.
    good = client.get(f"/v1/captures/{capture_id}/review/files/verdicts%2Fverdict.C.json", headers=REVIEWER_HEADERS)
    assert good.status_code == 200
    # Percent-encoded dots survive the test client's URL normalization, so the ".."
    # guard inside the route is what answers, not Starlette's router.
    for name in ("%2e%2e%2fsecrets.txt", "a%2f%2e%2e%2f%2e%2e%2fsecrets.txt", "verdicts%2f%2e%2e%2fsecrets.txt"):
        body = client.get(f"/v1/captures/{capture_id}/review/files/{name}", headers=REVIEWER_HEADERS)
        assert body.status_code == 404, name
        assert body.json()["errors"][0]["code"] == "result_not_ready", name


def test_device_token_cannot_reach_review_files(client: TestClient):
    capture_id = _create(client, "packet-review-0008")
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    body = client.get(
        f"/v1/captures/{capture_id}/review/files/result/review.html", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert body.status_code == 403


def test_uploaded_html_is_downloaded_instead_of_rendered(client: TestClient):
    """A stored review artifact is untrusted content, even for reviewers."""

    capture_id = _create(client, "packet-review-0008-html")
    _seed(client, capture_id, b"<script>window.pwned = true</script>")

    response = client.get(
        f"/v1/captures/{capture_id}/review/files/result/review.html",
        headers=REVIEWER_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"] == 'attachment; filename="review.html"'
    assert response.headers["x-content-type-options"] == "nosniff"


def _photo_link(body: str) -> str:
    found = re.search(r'src="(/v1/captures/[^"]+/review/files/stills/kf_01\.jpg\?rtok=[^"]+)"', body)
    assert found, "the served photo has no signed review token"
    return found.group(1)


def _verdict_link(body: str) -> str:
    found = re.search(r'href="(/v1/captures/[^"]+/review/files/verdicts/verdict\.C\.json\?rtok=[^"]+)"', body)
    assert found, "the served verdict link has no signed review token"
    return found.group(1)


def test_signed_review_links_work_without_a_bearer(client: TestClient):
    """The #284 finding: an <img> tag cannot send the reviewer's bearer header.

    Every link the served page rewrites carries a short-lived signed review
    token, so a plain browser fetch — no Authorization header at all — gets the
    photo, the verdict, the vendored module and the preview.
    """

    capture_id = _create(client, "packet-review-0009")
    verdict_bytes = (RESULT_DATA / "synthetic" / "verdicts" / "verdict.C.json").read_bytes()
    _seed(
        client,
        capture_id,
        _render_review_html(capture_id, signed_photo=None, signed_link=None),
        scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
        still=b"\xff\xd8fakejpeg",
        preview=b"idar-stub-preview",
    )
    verdict_key = f"captures/{capture_id}/runs/{RUN_ID}/verdicts/verdict.C.json"
    client.app_state.storage.put_bytes(verdict_key, verdict_bytes, "application/json")
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert served.status_code == 200
    body = served.text
    bare = TestClient(client.app)
    photo = bare.get(_photo_link(body))
    assert photo.status_code == 200
    assert photo.content == b"\xff\xd8fakejpeg"
    assert photo.headers["content-type"].startswith("image/jpeg")
    verdict = bare.get(_verdict_link(body))
    assert verdict.status_code == 200
    assert verdict.content == verdict_bytes
    module = re.search(r'import \* as THREE from "(/v1/review-static/three\.module\.js\?rtok=[^"]+)"', body)
    assert module, "the served import carries no signed review token"
    asset = bare.get(module.group(1))
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("text/javascript")
    preview_fetch = re.search(r'fetch\("preview\.ply\?rtok=([^"]+)"\)', body)
    assert preview_fetch, "the page's viewer fetch carries no signed review token"
    preview = bare.get(f"/v1/captures/{capture_id}/preview.ply?rtok={preview_fetch.group(1)}")
    assert preview.status_code == 200
    assert preview.content == b"idar-stub-preview"


def test_review_tokens_are_scoped_and_expiring(client: TestClient):
    """A token serves exactly one capture and one file, and dies at its expiry.

    The page's links are bearer-free, so the token itself is the gate: wrong
    capture, wrong file, wrong key or expired payload all refuse.
    """

    capture_id = _create(client, "packet-review-0010")
    _seed(
        client,
        capture_id,
        _render_review_html(capture_id, signed_photo=None, signed_link=None),
        scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
        still=b"\xff\xd8fakejpeg",
    )
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    photo_link = _photo_link(served.text)
    bare = TestClient(client.app)
    assert bare.get(photo_link).status_code == 200
    # the same token against another capture's URL: refused
    other = photo_link.replace(f"/v1/captures/{capture_id}/", "/v1/captures/cap-other/")
    assert bare.get(other).status_code == 401
    # the photo's token on a different file name: refused
    swapped = photo_link.replace("/files/stills/kf_01.jpg", "/files/verdicts/verdict.C.json")
    assert bare.get(swapped).status_code == 401
    # an expired token: refused
    expired = mint_token(
        {
            "op": "review-file",
            "cap": capture_id,
            "run": RUN_ID,
            "name": "stills/kf_01.jpg",
            "for": "reviewer",
            "exp": int(time.time()) - 10,
        },
        b"h" * 32,
    )
    assert bare.get(f"{photo_link.split('?')[0]}?rtok={expired}").status_code == 401
    # a token signed with the wrong key: refused
    forged = mint_token(
        {
            "op": "review-file",
            "cap": capture_id,
            "run": RUN_ID,
            "name": "stills/kf_01.jpg",
            "for": "reviewer",
            "exp": int(time.time()) + 900,
        },
        b"g" * 32,
    )
    assert bare.get(f"{photo_link.split('?')[0]}?rtok={forged}").status_code == 401
    # no token and no bearer: refused at the route itself
    assert bare.get(photo_link.split("?")[0]).status_code == 401
    # a device bearer without a token is still not a reviewer
    assert client.get(photo_link.split("?")[0]).status_code == 403


def test_jwt_mode_reviewer_subresources_load_without_a_header(tmp_path, monkeypatch):
    """In JWT deployments the same page must work for a plain browser fetch.

    A reviewer JWT builds the page; the page's photo link then loads with no
    Authorization header at all, while a device JWT and an anonymous request are
    still refused on the same URL.
    """

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    class _Signing:
        def __init__(self, material: bytes) -> None:
            self.key = material

    class _Jwks:
        def __init__(self, _url: str) -> None:
            self.url = _url

        def get_signing_key_from_jwt(self, _token: str) -> _Signing:
            return _Signing(public_pem)

    monkeypatch.setattr("server.api.auth.PyJWKClient", _Jwks)
    settings = Settings(
        STORAGE_BACKEND="volume",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "vol"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        UPLOAD_URL_HMAC_SECRET="h" * 32,
        HSS_INGEST_HOOKS="null",
        HSS_AUTH_MODE="jwt",
        HSS_AUTH_JWKS_URL="https://idp.test/jwks",
        HSS_AUTH_ISSUER="https://idp.test",
        HSS_AUTH_AUDIENCE="capture-api",
        GIT_SHA="0123456789abcdef",
        VISION_EXTERNAL_CALLS="on",
        SMP_SERVICE_NAME="capture-api",
    )
    app = create_app(settings=settings)
    test = TestClient(app)
    test.app_state = app.state.deps
    reviewer_token = jwt_encode(
        {"sub": "ada", "roles": ["reviewer"], "aud": "capture-api", "iss": "https://idp.test"},
        private_pem,
        algorithm="RS256",
    )
    made = test.post(
        "/v1/captures",
        json=create_body("packet-review-jwt-01"),
        headers={"Idempotency-Key": "packet-review-jwt-01", "Authorization": f"Bearer {reviewer_token}"},
    )
    assert made.status_code == 201, made.text
    capture_id = made.json()["captureId"]
    _seed(
        test,
        capture_id,
        _render_review_html(capture_id, signed_photo=None, signed_link=None),
        scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
        still=b"\xff\xd8fakejpeg",
    )
    served = test.get(f"/v1/captures/{capture_id}/review", headers={"Authorization": f"Bearer {reviewer_token}"})
    assert served.status_code == 200
    bare = TestClient(app)
    photo = bare.get(_photo_link(served.text))
    assert photo.status_code == 200
    assert photo.content == b"\xff\xd8fakejpeg"
    device_token = jwt_encode(
        {"sub": "dev-1", "aud": "capture-api", "iss": "https://idp.test"},
        private_pem,
        algorithm="RS256",
    )
    plain_url = _photo_link(served.text).split("?")[0]
    assert test.get(plain_url, headers={"Authorization": f"Bearer {device_token}"}).status_code == 403
    assert bare.get(plain_url).status_code == 401


def test_missing_three_asset_degrades_the_page_explicitly(client: TestClient, tmp_path, monkeypatch):
    """A deployment without the vendored module says so instead of importing a dead URL.

    The #284 finding: the route rewrote the import to /v1/review-static/... even
    when the packaged file was absent, so the page failed silently. It now keeps
    the page's original CDN import and injects a visible alert.
    """

    capture_id = _create(client, "packet-review-0012")
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    monkeypatch.setattr(result_routes, "_THREE_ASSET", tmp_path / "missing.js")
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert served.status_code == 200
    body = served.text
    assert "/v1/review-static" not in body
    assert 'import * as THREE from "https://unpkg.com/three@0.160.0/build/three.module.js"' in body
    assert "3D preview unavailable" in body
    assert 'id="review-degraded"' in body
    asset = client.get("/v1/review-static/three.module.js")
    assert asset.status_code == 404
    assert asset.json()["errors"][0]["code"] == "result_not_ready"


def test_status_table_marks_stub_stand_ins_from_run_hooks(client: TestClient):
    """A stage that succeeded under stubs reads as a stand-in, from the run's records.

    The #284 finding: the page guessed 'stand-in' from status alone. The label
    now comes from the run's recorded stage_stubs flag plus the graph's
    stubs_exempt stages, so real work under stubs never reads as a stand-in.
    """

    capture_id = _create(client, "packet-review-0013")
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    deps = client.app_state
    for stage, status in (("scene", "done"), ("placement", "done"), ("criteria", "failed")):
        deps.state.upsert_stage(
            StageRecord(run_id=RUN_ID, stage=stage, status=status, attempt=1, heartbeat="2026-09-26T00:00:00.000Z")
        )
    deps.state.put_blob(f"run_hooks:{RUN_ID}", json.dumps({"modal_remote": False, "stage_stubs": True}).encode())
    stubbed = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    body = stubbed.text
    assert "<td>scene</td><td>stand-in (stub)</td>" in body
    assert "<td>placement</td><td>ran</td>" in body  # the graph exempts placement from stubs
    assert "<td>criteria</td><td>failed</td>" in body
    # a run that never recorded stage stubs keeps the plain ran label
    plain_id = _create(client, "packet-review-0013b")
    _seed(
        client, plain_id, _render_review_html(plain_id, signed_photo=None, signed_link=None), run_id=f"{RUN_ID}_plain"
    )
    deps.state.upsert_stage(
        StageRecord(
            run_id=f"{RUN_ID}_plain", stage="scene", status="done", attempt=1, heartbeat="2026-09-26T00:00:00.000Z"
        )
    )
    plain = client.get(f"/v1/captures/{plain_id}/review", headers=REVIEWER_HEADERS)
    assert "<td>scene</td><td>ran</td>" in plain.text


def test_documented_review_paths_resolve(client: TestClient):
    """The integration doc names routes the app actually serves.

    The #284 finding: the doc described /review/files?rel=... and a capture-scoped
    review-static path, neither of which exists. Every documented review path now
    resolves past Starlette's router.
    """

    doc = (Path(__file__).resolve().parents[3] / "docs" / "INTEGRATION.md").read_text()
    paths = set(re.findall(r"GET (/v1/[^`\s]+)", doc))
    review_paths = {path for path in paths if "review" in path}
    assert "/v1/captures/{id}/review" in review_paths
    assert "/v1/captures/{id}/review/files/{name}" in review_paths
    assert "/v1/review-static/three.module.js" in review_paths
    assert not any("review/files?rel=" in path for path in paths)
    assert not any(path.startswith("/v1/captures/{id}/review-static/") for path in paths)
    capture_id = _create(client, "packet-review-0014")
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    verdict_key = f"captures/{capture_id}/runs/{RUN_ID}/verdicts/verdict.C.json"
    client.app_state.storage.put_bytes(
        verdict_key, (RESULT_DATA / "synthetic" / "verdicts" / "verdict.C.json").read_bytes(), "application/json"
    )
    for path in sorted(review_paths):
        url = path.replace("{id}", capture_id).replace("{name}", "verdicts/verdict.C.json")
        served = client.get(url, headers=REVIEWER_HEADERS)
        if served.status_code == 200:
            continue
        payload = served.json()  # every non-200 must be an app error, not a router miss
        assert payload.get("detail") != "Not Found", (path, served.status_code, served.text)


def _page_subresources(body: str, capture_id: str) -> list[str]:
    """Every same-origin URL the served page asks a browser to fetch.

    The review page is loaded once with the reviewer's bearer, but its
    subresources load from plain browser requests: <img>/<a> tags, the ES-module
    import and the viewer's fetch("preview.ply"). Those URLs are what the
    no-bearer sweep below walks.
    """

    urls = re.findall(r'(?:src|href)="(/v1/[^"]+)"', body)
    module = re.search(r'import \* as THREE from "(/v1/[^"]+)"', body)
    if module:
        urls.append(module.group(1))
    for tail in re.findall(r'fetch\("(preview\.ply[^"]*)"\)', body):
        urls.append(f"/v1/captures/{capture_id}/{tail}")
    return urls


def _token_payload(link: str) -> dict:
    """The claims inside a page link's rtok, for asserting what was minted."""

    body = link.split("rtok=")[1].split(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_every_subresource_the_page_references_loads_without_a_bearer(client: TestClient):
    """The packet-level sweep for the secret-configured mode.

    Fetch the page with the reviewer's bearer, then every subresource URL it
    references — photo, downloads, the vendored module, the preview — with no
    Authorization header at all. A browser can send none, so none may refuse.
    """

    capture_id = _create(client, "packet-review-0015")
    verdict_bytes = (RESULT_DATA / "synthetic" / "verdicts" / "verdict.C.json").read_bytes()
    _seed(
        client,
        capture_id,
        _render_review_html(capture_id, signed_photo=None, signed_link=None),
        scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
        still=b"\xff\xd8fakejpeg",
        preview=b"idar-stub-preview",
    )
    verdict_key = f"captures/{capture_id}/runs/{RUN_ID}/verdicts/verdict.C.json"
    client.app_state.storage.put_bytes(verdict_key, verdict_bytes, "application/json")
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert served.status_code == 200
    urls = _page_subresources(served.text, capture_id)
    kinds = " ".join(urls)
    assert "/review/files/stills/kf_01.jpg?rtok=" in kinds
    assert "/review/files/verdicts/verdict.C.json?rtok=" in kinds
    assert "/review-static/three.module.js?rtok=" in kinds
    assert "/preview.ply?rtok=" in kinds
    bare = TestClient(client.app)
    for url in urls:
        response = bare.get(url)
        assert response.status_code == 200, (url, response.status_code, response.text[:200])


def test_no_link_secret_keeps_the_stored_urls_and_says_so(tmp_path):
    """Without UPLOAD_URL_HMAC_SECRET no review token can be minted.

    The #284 finding: mint(None) returned an empty suffix, but the page still
    rewrote the module import and the preview fetch onto bearer-gated routes a
    browser cannot authenticate — the 3D preview died silently. The served page
    now keeps the stored page's original URLs and shows an explicit note.
    """

    settings = Settings(
        SMP_ENVIRONMENT="dev",
        STORAGE_BACKEND="local",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "store"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        CAPTURE_API_TOKENS=f"device:{TOKEN},reviewer:{REVIEWER}",
        HSS_INGEST_HOOKS="null",
        HSS_AUTH_MODE="static",
        GIT_SHA="0123456789abcdef",
        VISION_EXTERNAL_CALLS="on",
        SMP_SERVICE_NAME="capture-api",
    )
    app = create_app(settings=settings)
    test = TestClient(app)
    test.app_state = app.state.deps
    made = test.post(
        "/v1/captures",
        json=create_body("packet-review-0016"),
        headers={"Idempotency-Key": "packet-review-0016", "Authorization": f"Bearer {REVIEWER}"},
    )
    assert made.status_code == 201, made.text
    capture_id = made.json()["captureId"]
    _seed(
        test,
        capture_id,
        _render_review_html(capture_id, signed_photo=None, signed_link=None),
        scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
        still=b"\xff\xd8fakejpeg",
        preview=b"idar-stub-preview",
    )
    served = test.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert served.status_code == 200
    body = served.text
    # no token could be minted, so nothing on the page is token-signed
    assert "?rtok=" not in body
    # the stored page's original CDN import survives for the browser to load
    assert 'import * as THREE from "https://unpkg.com/three@0.160.0/build/three.module.js"' in body
    assert "/v1/review-static" not in body
    # the preview fetch is not rewritten onto the gated preview route either
    assert 'fetch("preview.ply")' in body
    assert "preview.ply?rtok" not in body
    # and the page says why the 3D preview will not load here
    assert "preview unavailable: no link secret configured" in body
    assert 'id="review-no-secret"' in body
    # the no-bearer sweep: the only same-origin fetch left is the page's own
    # original relative preview fetch, which refuses a browser (401) — the
    # exact failure the note above discloses. No other gated reference remains.
    remaining = _page_subresources(body, capture_id)
    assert remaining == [f"/v1/captures/{capture_id}/preview.ply"]
    assert TestClient(app).get(remaining[0]).status_code == 401
    # auth is configured, so no subresource route opens to a bare request either
    assert TestClient(app).get("/v1/review-static/three.module.js").status_code == 401
    assert TestClient(app).get(f"/v1/captures/{capture_id}/review/files/stills/kf_01.jpg").status_code == 401
    # the run-status section still explains the bearer-gated links
    assert "UPLOAD_URL_HMAC_SECRET is not configured" in body


def _amendment(
    client: TestClient,
    capture_id: str,
    *,
    first_id: str,
    second_id: str,
    first_still: bytes,
    second_still: bytes,
    first_ply: bytes,
    second_ply: bytes,
) -> None:
    """Run 1's artifacts, then an amendment that publishes run 2 under the capture.

    Stills and previews live under each run's own prefix, the way the result
    stage publishes them, so both runs hold a file under the same name.
    """

    _seed(
        client,
        capture_id,
        _render_review_html(capture_id, signed_photo=None, signed_link=None),
        scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
        preview=first_ply,
        run_id=first_id,
        created_at="2026-09-26T00:00:00.000Z",
    )
    client.app_state.storage.put_bytes(
        f"captures/{capture_id}/runs/{first_id}/stills/kf_01.jpg", first_still, "image/jpeg"
    )

    def publish_second_run() -> None:
        _seed(
            client,
            capture_id,
            _render_review_html(capture_id, signed_photo=None, signed_link=None),
            scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
            preview=second_ply,
            run_id=second_id,
            created_at="2026-09-26T01:00:00.000Z",
        )
        client.app_state.storage.put_bytes(
            f"captures/{capture_id}/runs/{second_id}/stills/kf_01.jpg", second_still, "image/jpeg"
        )
        # a verdict only the amendment produced, under the same name run 1 would use
        client.app_state.storage.put_bytes(
            f"captures/{capture_id}/runs/{second_id}/verdicts/verdict.C.json", b'{"run": "two"}', "application/json"
        )

    return publish_second_run


def test_amended_run_links_serve_their_own_run(client: TestClient):
    """A link minted for run 1 keeps serving run 1's bytes after an amendment.

    The Greptile security finding: get_review_file and get_preview picked the
    capture's latest run instead of the run named in the signed token, so after
    an amendment started a second run, an earlier link read the later run's
    files. The token's run is now honored, and a file the token's run lacks is
    refused rather than served from another run.
    """

    capture_id = _create(client, "packet-review-0017")
    first_id, second_id = f"{RUN_ID}_amend1", f"{RUN_ID}_amend2"
    first_still, second_still = b"\xff\xd8run-one-jpeg", b"\xff\xd8run-two-jpeg"
    publish_second_run = _amendment(
        client,
        capture_id,
        first_id=first_id,
        second_id=second_id,
        first_still=first_still,
        second_still=second_still,
        first_ply=b"ply-one",
        second_ply=b"ply-two",
    )
    # the reviewer opens the page while run 1 is the capture's latest
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert served.status_code == 200
    photo_link = _photo_link(served.text)
    preview_fetch = re.search(r'fetch\("preview\.ply\?rtok=([^"]+)"\)', served.text)
    assert preview_fetch, "the page's viewer fetch carries no signed review token"
    assert _token_payload(photo_link)["run"] == first_id
    # then the amendment starts a second run under the same capture
    publish_second_run()
    bare = TestClient(client.app)
    photo = bare.get(photo_link)
    assert photo.status_code == 200
    assert photo.content == first_still, "an earlier link read the amendment's bytes"
    preview = bare.get(f"/v1/captures/{capture_id}/preview.ply?rtok={preview_fetch.group(1)}")
    assert preview.status_code == 200
    assert preview.content == b"ply-one", "an earlier preview link read the amendment's bytes"
    # a file the token's run lacks is refused, never served from the later run
    absent = mint_token(
        {
            "op": "review-file",
            "cap": capture_id,
            "run": first_id,
            "name": "verdicts/verdict.C.json",
            "for": "reviewer",
            "exp": int(time.time()) + 900,
        },
        b"h" * 32,
    )
    refused = bare.get(f"/v1/captures/{capture_id}/review/files/verdicts/verdict.C.json?rtok={absent}")
    assert refused.status_code == 404
    assert refused.json()["errors"][0]["code"] == "result_not_ready"
    # the bearer path is unchanged: a reviewer's own fetch follows the latest run
    latest = client.get(f"/v1/captures/{capture_id}/review/files/stills/kf_01.jpg", headers=REVIEWER_HEADERS)
    assert latest.status_code == 200
    assert latest.content == second_still


def test_auth_none_without_a_link_secret_rewrites_to_live_routes(tmp_path):
    """Codex finding on PR #284 (routes/result.py:411).

    Under HSS_AUTH_MODE=none a bare browser request authenticates as the demo
    principal, so the page's links load without a token. The no-secret fallback
    still kept the CDN import and stored URLs and claimed the 3D preview was
    unavailable. In this mode the page now points every link at the live routes,
    unsigned, and each one loads with no Authorization header.
    """

    settings = Settings(
        SMP_ENVIRONMENT="dev",
        STORAGE_BACKEND="local",
        STATE_BACKEND="memory",
        VOLUME_ROOT=str(tmp_path / "store"),
        CAPTURE_API_PUBLIC_URL="http://testserver",
        HSS_INGEST_HOOKS="null",
        HSS_AUTH_MODE="none",
        GIT_SHA="0123456789abcdef",
        VISION_EXTERNAL_CALLS="on",
        SMP_SERVICE_NAME="capture-api",
    )
    assert settings.UPLOAD_URL_HMAC_SECRET is None
    app = create_app(settings=settings)
    test = TestClient(app)
    test.app_state = app.state.deps
    made = test.post(
        "/v1/captures", json=create_body("packet-review-0018"), headers={"Idempotency-Key": "packet-review-0018"}
    )
    assert made.status_code == 201, made.text
    capture_id = made.json()["captureId"]
    _seed(
        test,
        capture_id,
        _render_review_html(capture_id, signed_photo=None, signed_link=None),
        scene={"keyframes": [{"id": "kf_1", "image": "stills/kf_01.jpg"}]},
        still=b"\xff\xd8fakejpeg",
        preview=b"idar-stub-preview",
    )
    verdict_bytes = (RESULT_DATA / "synthetic" / "verdicts" / "verdict.C.json").read_bytes()
    test.app_state.storage.put_bytes(
        f"captures/{capture_id}/runs/{RUN_ID}/verdicts/verdict.C.json", verdict_bytes, "application/json"
    )
    served = test.get(f"/v1/captures/{capture_id}/review")
    assert served.status_code == 200
    body = served.text
    assert 'id="review-no-secret"' not in body
    assert "3D preview unavailable" not in body
    assert "?rtok=" not in body
    assert 'import * as THREE from "/v1/review-static/three.module.js"' in body
    assert f'src="/v1/captures/{capture_id}/review/files/stills/kf_01.jpg"' in body
    urls = _page_subresources(body, capture_id)
    assert f"/v1/captures/{capture_id}/review/files/verdicts/verdict.C.json" in urls
    assert f"/v1/captures/{capture_id}/preview.ply" in urls
    bare = TestClient(app)
    for url in urls:
        response = bare.get(url)
        assert response.status_code == 200, (url, response.status_code, response.text[:200])


def _gallery(client: TestClient, capture_id: str, count: int) -> None:
    """A run whose page shows ``count`` photos, each a still in storage."""

    names = [f"stills/kf_{index:03d}.jpg" for index in range(count)]
    photos = [{"id": f"kf_{index}", "url": name, "purpose": "keyframe"} for index, name in enumerate(names)]
    _seed(
        client,
        capture_id,
        _render_review_html(capture_id, signed_photo=None, signed_link=None, photos=photos),
        scene={"keyframes": [{"id": f"kf_{index}", "image": name} for index, name in enumerate(names)]},
    )
    for name in names:
        client.app_state.storage.put_bytes(f"captures/{capture_id}/{name}", b"\xff\xd8" + name.encode(), "image/jpeg")


def _freeze_limiter(client: TestClient) -> None:
    """No refill during the test, so bucket exhaustion does not depend on timing."""

    frozen = datetime(2026, 10, 22, tzinfo=UTC)
    client.app_state.limiter = Limiter(lambda: frozen)


def test_a_gallery_over_the_get_budget_loads_and_leaves_reads_alone(client: TestClient):
    """Greptile finding on PR #284 (routes/result.py:532).

    Every signed photo request spent the reviewer's 120/min ``get`` budget, so
    a 150-photo gallery got 429 partway through, and the reviewer's own result
    and review reads were refused after it. Page resources now spend their own
    bucket.
    """

    capture_id = _create(client, "packet-review-0019")
    _gallery(client, capture_id, 150)
    _freeze_limiter(client)
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert served.status_code == 200
    links = re.findall(r'src="(/v1/captures/[^"]+/review/files/stills/[^"]+\?rtok=[^"]+)"', served.text)
    assert len(links) == 150
    bare = TestClient(client.app)
    statuses = [bare.get(link).status_code for link in links]
    assert statuses == [200] * 150, f"first refusal at photo {next(i for i, s in enumerate(statuses) if s != 200)}"
    assert client.get(f"/v1/captures/{capture_id}/result", headers=REVIEWER_HEADERS).status_code == 200
    assert client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS).status_code == 200


def test_page_resources_are_rate_limited_including_the_module(client: TestClient):
    """The page bucket is a real limit, and the vendored module spends it too.

    The module route charged nothing, so a signed import link could be fetched
    without bound. With HSS_INGEST_MAX_FILES=3 the page bucket holds six
    requests; the seventh is refused, and the reviewer's ``get`` reads are not.
    """

    capture_id = _create(client, "packet-review-0020")
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    client.app_state.settings.HSS_INGEST_MAX_FILES = 3
    _freeze_limiter(client)
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    module = re.search(r'import \* as THREE from "(/v1/review-static/three\.module\.js\?rtok=[^"]+)"', served.text)
    assert module, "the served import carries no signed review token"
    bare = TestClient(client.app)
    statuses = [bare.get(module.group(1)).status_code for _ in range(7)]
    assert statuses == [200] * 6 + [429]
    assert client.get(f"/v1/captures/{capture_id}/result", headers=REVIEWER_HEADERS).status_code == 200
