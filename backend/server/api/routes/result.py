"""Serve the device result and the reviewer HTML once WS-10 has written them."""

from __future__ import annotations

import html as html_module
import json
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import ValidationError

from server.api.deps import get_deps, load_owned, require_principal
from server.api.errors import fail
from server.common.backends.storage_volume import mint_token, read_token
from server.common.logging import log_event
from server.contracts.result import ResultResponse
from server.orchestrator.graph import STAGES
from server.orchestrator.stateio import get_json

# The pinned three.js module the review page imports. Vendored so the served page
# renders the 3D preview without network access; the stored page may still name a CDN.
_REVIEW_ASSETS = Path(__file__).resolve().parents[1] / "assets"
_THREE_ASSET = _REVIEW_ASSETS / "three.module.js"
_THREE_LOCAL_URL = "/v1/review-static/three.module.js"
_THREE_IMPORT = re.compile(r'(import\s*\*\s*as\s*THREE\s*from\s*")([^"]*)(")')
_FIGURE = re.compile(r"<figure>.*?</figure>", re.DOTALL)
_FIGCAPTION_ID = re.compile(r"<figcaption>[^<]*·\s*([^<]+)</figcaption>")
_IMG_SRC = re.compile(r'(<img[^>]*?src=")([^"]*)(")')
_RAW_FILES_SECTION = re.compile(r"(<h2>Raw files</h2>\s*<ul>)(.*?)(</ul>)", re.DOTALL)
_STAGE_ORDER = (
    "validate",
    "poses",
    "objects",
    "reads",
    "scale",
    "dense",
    "coverage",
    "scene",
    "objects_3d",
    "placement",
    "criteria",
    "result",
)
# The reviewer reads a stage as ran, stand-in or failed; skipped and stale stages
# were answered by the orchestrator instead of a worker, so they read as stand-in.
_STATUS_LABEL = {
    "done": "ran",
    "failed": "failed",
    "skipped": "stand-in (skipped)",
    "stale": "stand-in (stale)",
}
# The query parameter carrying the page's signed review token. Browser <img>, <a>
# and ES-module requests cannot send the reviewer's bearer header (Greptile finding
# on PR #284, routes/result.py:125), so every rewritten link carries a short-lived
# HMAC token instead — the upload-token machinery, scoped to the capture and file.
_REVIEW_TOKEN_PARAM = "rtok"
_PREVIEW_FETCH = re.compile(r'fetch\("preview\.ply"\)')
_THREE_DEGRADED_NOTE = (
    '<section id="review-degraded" role="alert" '
    'style="border:2px solid #b00;padding:8px;margin:8px 0">'
    "<strong>3D preview unavailable in this deployment:</strong> the vendored "
    "three.module.js is missing from the served image, so the page kept its "
    "original CDN import and offline viewers cannot load it. Package "
    "backend/server/api/assets/three.module.js into the image to restore the viewer."
    "</section>"
)


def _latest(deps, capture_id: str):
    runs = deps.state.runs_for_capture(capture_id)
    if not runs:
        return None
    return max(runs, key=lambda item: item.created_at)


def _strip_hidden(value):
    """Drop device-facing references to verdicts and the internal result."""
    if isinstance(value, str):
        if "verdicts/" in value or "result.internal.json" in value:
            return None
        return value
    if isinstance(value, list):
        return [_strip_hidden(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_hidden(item) for key, item in value.items()}
    return value


def _artifacts(deps, run) -> dict:
    """Re-read the run and the result blob. A scan can observe stage=done first."""

    if run is None:
        return {}
    fresh = deps.state.get_run(run.run_id) or run
    artifacts = dict(fresh.artifacts or {})
    extra = get_json(deps.state, f"stage_artifacts:{fresh.run_id}:result") or {}
    if isinstance(extra, dict):
        artifacts.update({str(key): str(value) for key, value in extra.items() if value is not None})
    return artifacts


def _review_files(deps, capture_id: str, run) -> dict[str, str]:
    """Everything the capture published: page-relative name → storage key.

    The stored review page names artifacts either as bare packet-relative names
    (rendered offline) or as signed URLs whose tokens are opaque, so the mapping
    goes from the names the page displays to the keys storage actually holds.
    """

    if run is None:
        return {}
    run_prefix = f"captures/{capture_id}/runs/{run.run_id}/"
    capture_prefix = f"captures/{capture_id}/"
    try:
        keys = deps.storage.list_prefix(capture_prefix)
    except Exception:
        return {}
    files: dict[str, str] = {}
    for key in keys:
        if not isinstance(key, str) or not key or key.endswith("/"):
            continue
        if key.startswith(run_prefix):
            relative = key[len(run_prefix) :]
            files.setdefault(relative, key)
            files.setdefault(relative.rsplit("/", 1)[-1], key)
        elif key.startswith(capture_prefix):
            files.setdefault(key[len(capture_prefix) :], key)
    return files


def _file_url(capture_id: str, name: str) -> str:
    return f"/v1/captures/{capture_id}/review/files/{quote(name)}"


def _token_secret(settings) -> bytes | None:
    """Review tokens reuse the upload-token HMAC machinery and its secret.

    A deployment without UPLOAD_URL_HMAC_SECRET cannot mint review tokens; the
    page keeps its bearer-gated links and says so (see _runtime_section).
    """

    secret = getattr(settings, "UPLOAD_URL_HMAC_SECRET", None)
    return secret.encode() if secret else None


def _mint_review_token(settings, *, op: str, capture_id: str, run_id: str, name: str | None, holder: str) -> str | None:
    """A signed review token, or None when the deployment has no HMAC secret.

    The token is scoped to the capture, the operation and (for files) the exact
    file name, names the reviewer it was minted for, and expires after
    HSS_INGEST_RESULT_URL_TTL_S — the same short life the composer's own signed
    URLs get, but minted at serve time so the page never carries a stale one.
    """

    secret = _token_secret(settings)
    if secret is None:
        return None
    ttl = int(getattr(settings, "HSS_INGEST_RESULT_URL_TTL_S", 900) or 900)
    payload = {"op": op, "cap": capture_id, "run": run_id, "for": holder, "exp": int(time.time()) + ttl}
    if name is not None:
        payload["name"] = name
    return mint_token(payload, secret)


def _review_token(
    request: Request, settings, *, op: str, capture_id: str | None = None, name: str | None = None
) -> dict | None:
    """A valid signed review token for exactly this request, or None.

    HMAC, expiry, operation and capture binding are all checked; a token minted
    for one capture or file never serves another. capture_id=None (the vendored
    module) still requires the token to name some capture.
    """

    token = request.query_params.get(_REVIEW_TOKEN_PARAM)
    if not token:
        return None
    secret = _token_secret(settings)
    if secret is None:
        return None
    payload = read_token(token, secret)
    if not payload or payload.get("_expired"):
        return None
    if payload.get("op") != op:
        return None
    if capture_id is None:
        if not payload.get("cap"):
            return None
    elif payload.get("cap") != capture_id:
        return None
    if name is not None and payload.get("name") != name:
        return None
    return payload


def _capture_or_fail(deps, capture_id: str) -> None:
    """Existence and tomb checks for a token-authenticated file request.

    The signed token itself binds the request to this capture, so the bearer
    path's principal-ownership check does not run on it.
    """

    if deps.state.get_kv(f"tomb:{capture_id}"):
        raise fail(410, "capture_deleted", "/", "capture was deleted")
    if deps.state.get_capture(capture_id) is None:
        raise fail(404, "capture_not_found", "/", "capture not found")


def _mint_suffix_factory(deps, capture_id: str, run, holder: str):
    """A per-link signer: file name → ``?rtok=…``, or ``""`` when it cannot mint."""

    def mint(name: str | None) -> str:
        token = _mint_review_token(
            deps.settings,
            op="review-static" if name is None else "review-file",
            capture_id=capture_id,
            run_id=run.run_id,
            name=name,
            holder=holder,
        )
        return f"?{_REVIEW_TOKEN_PARAM}={token}" if token else ""

    return mint


def _rewrite_three_import(html: str, signed: str = "") -> tuple[str, bool]:
    """Point the page's three.js import at the vendored module the route serves.

    Returns the page and whether the rewrite degraded: a deployment whose image
    lacks the vendored file keeps the page's original CDN import and the served
    page says so explicitly, instead of silently importing a dead URL (Greptile
    finding on PR #284, routes/result.py:346).
    """

    if not _THREE_ASSET.is_file():
        return html, True
    rewritten = _THREE_IMPORT.sub(
        lambda match: match.group(1) + _THREE_LOCAL_URL + signed + match.group(3), html, count=1
    )
    return rewritten, False


def _rewrite_photos(html: str, deps, capture_id: str, files: dict[str, str], mint) -> str:
    """Give each photo figure a link the route can satisfy, signed URL or bare name."""

    scene_key = files.get("scene/scene.json")
    if not scene_key:
        return html
    try:
        scene = json.loads(deps.storage.get_bytes(scene_key))
    except Exception:
        return html
    id_to_image = {
        str(kf.get("id")): str(kf.get("image"))
        for kf in scene.get("keyframes") or []
        if kf.get("id") and kf.get("image")
    }
    if not id_to_image:
        return html

    def fix_figure(match: re.Match[str]) -> str:
        figure = match.group(0)
        caption = _FIGCAPTION_ID.search(figure)
        image = id_to_image.get(caption.group(1).strip()) if caption else None
        if not image or image not in files:
            return figure
        return _IMG_SRC.sub(
            lambda m: m.group(1) + _file_url(capture_id, image) + mint(image) + m.group(3), figure, count=1
        )

    return _FIGURE.sub(fix_figure, html)


def _rewrite_raw_files(html: str, capture_id: str, files: dict[str, str], mint) -> str:
    """Rebuild the raw-file list from what storage holds right now.

    The stored list bakes signed URLs that expire (the #255 review finding), and a
    bare-name link would 404 under the review page's URL. Serving every link the
    route can satisfy makes the page survive past the TTL.
    """

    entries = sorted(
        name
        for name in files
        if "/" in name
        and (name.startswith("verdicts/") or name.startswith("result/"))
        and name.endswith((".json", ".ply"))
    )
    if not entries:
        return html
    items = "".join(
        f'<li><a href="{_file_url(capture_id, name)}{mint(name)}">{html_module.escape(name)}</a></li>'
        for name in entries
    )

    def replace(match: re.Match[str]) -> str:
        return match.group(1) + items + match.group(3)

    return _RAW_FILES_SECTION.sub(replace, html, count=1)


def _runtime_section(deps, capture_id: str, run) -> str:
    """Stage status, codes and provenance, injected at serve time from live state.

    A stage that succeeded reads as ran, failed or stand-in. Whether it was a
    stand-in comes from the run's recorded callables — the run_hooks blob records
    whether the run ran stage stubs, and the graph records which stages are
    exempt (real work even on the stub path) — never a guess (Greptile finding
    on PR #284, routes/result.py:52).
    """

    try:
        stages = deps.state.stages(run.run_id)
    except Exception:
        stages = []
    hooks = get_json(deps.state, f"run_hooks:{run.run_id}") or {}
    stubbed = bool(hooks.get("stage_stubs"))
    order = {name: index for index, name in enumerate(_STAGE_ORDER)}
    rows = []
    for record in sorted(stages, key=lambda rec: order.get(str(rec.stage), len(_STAGE_ORDER))):
        status = str(record.status)
        label = _STATUS_LABEL.get(status, status)
        if status == "done" and stubbed:
            spec = STAGES.get(str(record.stage))
            if spec is not None and not spec.stubs_exempt:
                label = "stand-in (stub)"
        code = html_module.escape(str(record.error_code)) if record.error_code else ""
        rows.append(
            "<tr><td>" + html_module.escape(str(record.stage)) + f"</td><td>{html_module.escape(label)}</td>"
            f"<td>{code}</td></tr>"
        )
    degraded = ", ".join(str(item) for item in (run.degraded or [])) or "none"
    git_sha = html_module.escape(str(getattr(deps.settings, "GIT_SHA", "") or "unknown"))
    notes = ""
    if deps.settings.HSS_AUTH_MODE != "none" and _token_secret(deps.settings) is None:
        notes = (
            "<p>Photo and download links need the reviewer bearer header on this deployment: "
            "UPLOAD_URL_HMAC_SECRET is not configured, so the page could not sign per-file review links.</p>"
        )
    return (
        '<section id="review-runtime"><h2>Run status</h2>'
        f"<p>Run <code>{html_module.escape(run.run_id)}</code> · capture <code>{html_module.escape(capture_id)}</code>"
        f" · git SHA <code>{git_sha}</code></p>"
        f"<p>Degraded: {html_module.escape(degraded)}</p>"
        + notes
        + "<table><thead><tr><th>stage</th><th>status</th><th>code</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )


def _serve_review_html(deps, capture_id: str, run, raw: bytes, holder: str = "") -> HTMLResponse:
    """Rewrite the stored page so every reference stays live at serve time.

    compose.py (not this route's) baked signed URLs that expire after
    HSS_INGEST_RESULT_URL_TTL_S and a CDN three.js import; both fail long after
    the page was rendered. Every rewrite is best-effort: a failure falls back to
    the stored bytes rather than breaking the page. Rewritten links carry a
    short-lived signed review token so the browser can fetch them without a
    bearer header (Greptile finding on PR #284, routes/result.py:125).
    """

    page = raw.decode("utf-8", errors="replace")
    try:
        files = _review_files(deps, capture_id, run)
        mint = _mint_suffix_factory(deps, capture_id, run, holder)
        page, three_degraded = _rewrite_three_import(page, mint(None))
        page = _rewrite_photos(page, deps, capture_id, files, mint)
        page = _rewrite_raw_files(page, capture_id, files, mint)
        suffix = mint("result/preview.ply")
        page = _PREVIEW_FETCH.sub(f'fetch("preview.ply{suffix}")', page, count=1)
        section = _runtime_section(deps, capture_id, run)
        if three_degraded:
            section = _THREE_DEGRADED_NOTE + section
        if "</body>" in page:
            page = page.replace("</body>", section + "</body>", 1)
        else:
            page = page + section
    except Exception:
        log_event("review.rewrite_failed", capture_id=capture_id, level="warning")
        return HTMLResponse(raw)
    return HTMLResponse(page)


def register(app: FastAPI) -> None:
    @app.get("/v1/captures/{capture_id}/result")
    def get_result(capture_id: str, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "get", 120)
        record = load_owned(deps, capture_id, principal)
        run = _latest(deps, capture_id)
        stage = deps.state.get_stage(run.run_id, "result") if run else None
        ready = stage is not None and stage.status == "done"
        artifacts = _artifacts(deps, run)
        key = artifacts.get("result/result.json")
        if key:
            ready = True
        if not ready:
            body = ResultResponse(
                runId=run.run_id if run else record.run_id,
                status=record.status,
                viewsNeeded=[],
                memberActions=[],
                outcome=None,
            )
            log_event("result.served", ready=False, capture_id=capture_id, run_id=body.runId)
            return JSONResponse(body.model_dump(mode="json"))
        if not key:
            raise fail(404, "result_not_ready", "/", "result is not ready")
        try:
            payload = json.loads(deps.storage.get_bytes(key))
        except Exception as exc:
            log_event("storage.error", op="get", capture_id=capture_id, level="error")
            raise fail(503, "storage_unavailable", "/", "could not read the result", **{"Retry-After": "5"}) from exc
        payload["status"] = record.status
        payload["runId"] = run.run_id
        profile = deps.settings.RULES_PROFILE_DEFAULT
        verdict_key = artifacts.get(f"verdicts/verdict.{profile}.json")
        if principal.reviewer and isinstance(verdict_key, str) and "result.internal.json" not in verdict_key:
            payload["verdictUrl"] = deps.storage.signed_get_url(verdict_key, deps.settings.HSS_INGEST_RESULT_URL_TTL_S)
        else:
            payload = _strip_hidden(payload)
            payload["verdictUrl"] = None
        preview_key = artifacts.get("result/preview.ply")
        if preview_key:
            payload["previewUrl"] = deps.storage.signed_get_url(preview_key, deps.settings.HSS_INGEST_RESULT_URL_TTL_S)
        payload["reviewUrl"] = f"/v1/captures/{capture_id}/review"
        try:
            ResultResponse.model_validate(payload)
        except ValidationError as exc:
            log_event(
                "result.schema_drift",
                locs=[list(err.get("loc") or []) for err in exc.errors()],
                capture_id=capture_id,
                run_id=run.run_id,
            )
        log_event("result.served", ready=True, capture_id=capture_id, run_id=run.run_id)
        return JSONResponse(payload)

    @app.get("/v1/captures/{capture_id}/review")
    def get_review(capture_id: str, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "get", 120)
        if deps.settings.HSS_AUTH_MODE != "none" and not principal.reviewer:
            raise fail(403, "forbidden", "/", "review is for reviewers")
        load_owned(deps, capture_id, principal)
        run = _latest(deps, capture_id)
        key = _artifacts(deps, run).get("result/review.html") if run else None
        if not key:
            raise fail(404, "result_not_ready", "/", "review is not ready")
        try:
            raw = deps.storage.get_bytes(key)
        except FileNotFoundError as exc:
            raise fail(404, "result_not_ready", "/", "review is not ready") from exc
        return _serve_review_html(deps, capture_id, run, raw, principal.label)

    @app.get("/v1/captures/{capture_id}/review/files/{name:path}")
    def get_review_file(capture_id: str, name: str, request: Request):
        """Serve a run artifact so the review page's links never carry a TTL.

        A browser <img> or <a> request cannot send the reviewer's bearer header
        (Greptile finding on PR #284, routes/result.py:125), so the page's links
        carry a short-lived signed review token: HMAC'd with the deployment's
        upload-token secret, bound to this capture and file name, expired after
        HSS_INGEST_RESULT_URL_TTL_S. The bearer path is unchanged: an anonymous
        request 401s and a device token still 403s.
        """

        deps = get_deps(request)
        payload = _review_token(request, deps.settings, op="review-file", capture_id=capture_id, name=name)
        if payload is not None:
            deps.charge(str(payload.get("for") or "review-token"), "get", 120)
            _capture_or_fail(deps, capture_id)
        else:
            principal = require_principal(request)
            deps.charge(principal.label, "get", 120)
            if deps.settings.HSS_AUTH_MODE != "none" and not principal.reviewer:
                raise fail(403, "forbidden", "/", "review is for reviewers")
            load_owned(deps, capture_id, principal)
        run = _latest(deps, capture_id)
        safe = name.strip("/")
        if not run or not safe or ".." in safe or "\\" in safe:
            raise fail(404, "result_not_ready", "/", "file is not ready")
        candidates = [f"captures/{capture_id}/runs/{run.run_id}/{safe}", f"captures/{capture_id}/{safe}"]
        key = next((item for item in candidates if deps.storage.exists(item)), None)
        if key is None:
            raise fail(404, "result_not_ready", "/", "file is not ready")
        try:
            data = deps.storage.get_bytes(key)
        except FileNotFoundError as exc:
            raise fail(404, "result_not_ready", "/", "file is not ready") from exc
        media_type = mimetypes.guess_type(safe)[0] or "application/octet-stream"
        return Response(content=data, media_type=media_type)

    @app.get("/v1/review-static/three.module.js")
    def get_review_three(request: Request):
        """The vendored three.js module the served review page imports offline.

        A browser ES-module import cannot send the reviewer's bearer header, so
        the served page's import URL carries a signed review token; a valid
        bearer principal keeps working exactly as the middleware allowed before.
        """

        deps = get_deps(request)
        payload = _review_token(request, deps.settings, op="review-static", capture_id=None)
        if payload is None:
            require_principal(request)
        if not _THREE_ASSET.is_file():
            raise fail(404, "result_not_ready", "/", "static asset is missing")
        return FileResponse(
            _THREE_ASSET,
            media_type="text/javascript",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/v1/captures/{capture_id}/preview.ply")
    def get_preview(capture_id: str, request: Request):
        """Serve the run's result/preview.ply so the review page's relative fetch works.

        The page's viewer fetch is a plain browser request with no bearer header,
        so it carries the same signed review token as the page's other links.
        """
        deps = get_deps(request)
        payload = _review_token(
            request, deps.settings, op="review-file", capture_id=capture_id, name="result/preview.ply"
        )
        if payload is not None:
            deps.charge(str(payload.get("for") or "review-token"), "get", 120)
            _capture_or_fail(deps, capture_id)
        else:
            principal = require_principal(request)
            deps.charge(principal.label, "get", 120)
            if deps.settings.HSS_AUTH_MODE != "none" and not principal.reviewer:
                raise fail(403, "forbidden", "/", "review is for reviewers")
            load_owned(deps, capture_id, principal)
        run = _latest(deps, capture_id)
        key = _artifacts(deps, run).get("result/preview.ply") if run else None
        if not key:
            raise fail(404, "result_not_ready", "/", "preview is not ready")
        try:
            data = deps.storage.get_bytes(key)
        except FileNotFoundError as exc:
            raise fail(404, "result_not_ready", "/", "preview is not ready") from exc
        return Response(content=data, media_type="application/octet-stream")
