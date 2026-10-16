"""The reviewer page boundary: GET /v1/captures/{id}/review.

The stored review.html is composed once, but served many times. The #255 review
found the served page unjudgeable long after render: baked signed URLs expire
after HSS_INGEST_RESULT_URL_TTL_S and the three.js import points at a CDN. These
tests drive the route the way a reviewer does — through the FastAPI app with the
real template rendered into volume storage — and read the exact HTML back.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

from server.api.tests.conftest import REVIEWER, TOKEN, create_body
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


def _render_review_html(capture_id: str, *, signed_photo: str | None, signed_link: str | None) -> bytes:
    """A page exactly as compose.py writes it: signed URLs where storage could sign."""

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
) -> None:
    deps = client.app_state
    artifacts = {}
    if review_html is not None:
        review_key = f"captures/{capture_id}/runs/{RUN_ID}/result/review.html"
        deps.storage.put_bytes(review_key, review_html, "text/html")
        artifacts["result/review.html"] = review_key
    if scene is not None:
        scene_key = f"captures/{capture_id}/runs/{RUN_ID}/scene/scene.json"
        deps.storage.put_bytes(scene_key, json.dumps(scene).encode(), "application/json")
        artifacts["scene/scene.json"] = scene_key
    if still is not None:
        deps.storage.put_bytes(f"captures/{capture_id}/stills/kf_01.jpg", still, "image/jpeg")
    deps.state.put_run(
        RunRecord(
            run_id=RUN_ID,
            capture_id=capture_id,
            packet_sha256="a" * 64,
            status="done",
            branch="image",
            flow="photo",
            tier="photo_only",
            profile_default="C",
            created_at="2026-09-26T00:00:00.000Z",
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
    time.sleep(2.5)  # the baked tokens expired: ttl was 2s
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
    assert f'src="/v1/captures/{capture_id}/review/files/stills/kf_01.jpg"' in body
    assert "verdicts/verdict.C.json" in body
    assert "result/result.internal.json" in body
    photo = client.get(f"/v1/captures/{capture_id}/review/files/stills/kf_01.jpg", headers=REVIEWER_HEADERS)
    assert photo.status_code == 200
    assert photo.headers["content-type"].startswith("image/jpeg")


def test_three_js_import_is_served_locally(client: TestClient):
    capture_id = _create(client, "packet-review-0005")
    _seed(client, capture_id, _render_review_html(capture_id, signed_photo=None, signed_link=None))
    served = client.get(f"/v1/captures/{capture_id}/review", headers=REVIEWER_HEADERS)
    assert 'import * as THREE from "/v1/review-static/three.module.js"' in served.text
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
