"""Regression tests for the reviewer page (PR #247 Greptile findings).

The confirmation form must post JSON with the reviewer's bearer credentials to
the real /v1 route, and the point-cloud preview must load and draw preview.ply.
"""

from __future__ import annotations

from pathlib import Path

from server.result.review.render import render_review

THREE_URL = "https://unpkg.com/three@0.160.0/build/three.module.js"


def _context() -> dict:
    return {
        "capture_id": "cap_test",
        "run_id": "run_test",
        "profiles": [{"name": "A", "decision": "manual_review", "device": True}],
        "true_decision": "manual_review",
        "failure_code": None,
        "degraded": [],
        "scale": None,
        "timings": {},
        "vlm_cost": None,
        "decidability": None,
        "photos": [],
        "criteria": [],
        "walls": [],
        "views": [],
        "meter": {
            "status": "confirmed",
            "value": "1234567",
            "candidates": [],
            "evidence": {"image": "still-1"},
        },
        "profile": "A",
        "links": [],
    }


def _render(tmp_path: Path) -> str:
    out = tmp_path / "review.html"
    render_review(_context(), out, three_url=THREE_URL)
    return out.read_text()


def test_confirmation_form_posts_to_the_v1_route(tmp_path: Path) -> None:
    html = _render(tmp_path)
    assert 'action="/v1/captures/cap_test/confirmations"' in html
    # The old page posted to /captures/..., a route this API does not serve.
    assert 'action="/captures/' not in html


def test_confirmation_form_submits_json_with_bearer_credentials(tmp_path: Path) -> None:
    html = _render(tmp_path)
    assert '"Content-Type": "application/json"' in html
    assert "Bearer " in html
    assert "JSON.stringify" in html
    # Every field the ConfirmationRequest contract requires is present.
    for field in ("fieldId", "kind", "value", "imageId", "clientAt"):
        assert f"{field}:" in html


def test_rejection_status_reads_the_errors_shape(tmp_path: Path) -> None:
    html = _render(tmp_path)
    # Every API failure path returns {"errors": [{"code", "pointer", "message"}]}
    # (server.api.errors.error_response, and the RequestValidationError handler
    # in server.api.app builds the same shape) — body.detail is never set, so
    # reading it left the reviewer with only "Rejected: HTTP 422".
    assert "body.errors[0].message" in html
    assert "body.detail" not in html


def test_preview_loads_and_draws_preview_ply(tmp_path: Path) -> None:
    html = _render(tmp_path)
    assert 'fetch("preview.ply")' in html
    assert "parseBinaryPly" in html
    assert "THREE.Points" in html
    assert "THREE.BufferGeometry" in html
    # The fallback note is hidden only after the cloud was drawn, and the
    # failure path surfaces a message instead of leaving a blank canvas.
    assert "note.hidden = true" in html
    assert "Point cloud preview unavailable" in html
