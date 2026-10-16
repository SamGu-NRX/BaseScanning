"""Regression tests for the reviewer page (PR #247 Greptile findings).

The confirmation form must post JSON with the reviewer's bearer credentials to
the real /v1 route, and the point-cloud preview must load and draw preview.ply.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

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


_NODE_PARSER_CHECK = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[1], "utf8");
const start = html.indexOf("function parseBinaryPly(buffer) {");
if (start < 0) { console.error("parseBinaryPly not found in the rendered page"); process.exit(2); }
let depth = 0, end = -1;
for (let i = html.indexOf("{", start); i < html.length; i++) {
  if (html[i] === "{") depth++;
  else if (html[i] === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
}
const parseBinaryPly = eval("(" + html.slice(start, end) + ")");
// The standard PLY header the scene pipeline writes: xyz floats plus a uchar
// generated flag. A parser that reads the property NAME as its TYPE (an
// off-by-one destructure found while dogfooding PR #284) throws
// "unsupported property type x" on this exact input.
const header =
  "ply\nformat binary_little_endian 1.0\nelement vertex 4\n" +
  "property float x\nproperty float y\nproperty float z\nproperty uchar generated\nend_header\n";
const stride = 13; // 3 float32 + 1 uint8
const body = Buffer.alloc(stride * 4);
[[0, 0, 0, 1], [1, 0, 0, 1], [0, 1, 0, 0], [1, 1, 0.5, 0]].forEach((p, v) => {
  body.writeFloatLE(p[0], v * stride);
  body.writeFloatLE(p[1], v * stride + 4);
  body.writeFloatLE(p[2], v * stride + 8);
  body.writeUInt8(p[3], v * stride + 12);
});
const bytes = Buffer.concat([Buffer.from(header, "ascii"), body]);
// parseBinaryPly scans from byte 0 of the ArrayBuffer, exactly what a browser
// fetch() hands it; a pooled node Buffer's .buffer may start at an offset, so
// copy into a fresh, aligned ArrayBuffer.
const ab = new ArrayBuffer(bytes.length);
new Uint8Array(ab).set(bytes);
const cloud = parseBinaryPly(ab); // the page passes res.arrayBuffer(), also an ArrayBuffer
const want = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0.5]);
for (let i = 0; i < want.length; i++) {
  if (cloud.positions[i] !== want[i]) { console.error("positions[" + i + "]=" + cloud.positions[i] + " want " + want[i]); process.exit(1); }
}
if (!cloud.hasGenerated || cloud.generated[0] !== 1 || cloud.generated[3] !== 0) { console.error("generated flags parsed wrong"); process.exit(1); }
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_parse_binary_ply_reads_standard_xyz_properties(tmp_path: Path) -> None:
    """The rendered page's PLY parser reads the standard xyz header.

    Extracts parseBinaryPly from the rendered template and runs it in node
    against the binary PLY shape the scene pipeline writes (float xyz + uchar
    generated), so a header-parsing regression fails here instead of in the
    browser as "Point cloud preview unavailable".
    """
    html_path = tmp_path / "review.html"
    html_path.write_text(_render(tmp_path))
    made = subprocess.run(
        ["node", "-e", _NODE_PARSER_CHECK, str(html_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert made.returncode == 0, f"node parser check failed: {made.stderr or made.stdout}"
