"""HTTP front of the placement server.

One upload in, one solver result out. The body is a bare scene.json, a zip bundle (scene.json plus
the keyframe and still images it names), or a multipart form whose single `bundle` field holds
either. Every refusal is the same JSON envelope, {"error": {"code", "message", "path"}}, where
`path` is a JSON pointer into scene.json when one field is to blame; a client never sees a stack
trace.

The zip is read in memory and never extracted, so entry names only matter as lookups; entries with
absolute paths or `..` are still refused because a bundle carrying them was not made by our app.

Run: uv run uvicorn api:app --host 0.0.0.0 --port 8000
"""

import hmac
import io
import json
import logging
import os
import re
import zipfile
import zlib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

import siteplan
from rules import LoadedRules, load_rules
from scene import Scene, SceneError, parse_scene
from solver import SCHEMA_VERSION, SceneTooComplex, solve

log = logging.getLogger("housescan.api")

_MB = 1024 * 1024


def _limit_bytes(env: str, default_mb: float) -> int:
    raw = os.environ.get(env)
    if raw is None:
        return int(default_mb * _MB)
    try:
        mb = float(raw)
    except ValueError:
        mb = -1.0
    if not mb > 0 or mb == float("inf"):
        raise ValueError(f"{env}={raw!r} must be a positive number of megabytes")
    return int(mb * _MB)


def _limit_int(env: str, default: int) -> int:
    raw = os.environ.get(env)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value <= 0:
        raise ValueError(f"{env}={raw!r} must be a positive whole number")
    return value


# Read once at import so a bad value stops the server at startup. Tests monkeypatch these names.
MAX_UPLOAD_BYTES = _limit_bytes("HOUSESCAN_MAX_UPLOAD_MB", 256)
MAX_UNZIPPED_BYTES = _limit_bytes("HOUSESCAN_MAX_UNZIPPED_MB", 512)
# Parsing JSON takes many times its size in memory (10.5 MB took 255 MB), and a real scene.json
# is well under 1 MB, so scene.json itself is capped far below the bundle.
MAX_SCENE_BYTES = _limit_bytes("HOUSESCAN_MAX_SCENE_MB", 10)
# A bundle's central directory is parsed in full the moment ZipFile is built, so the directory's
# entry count and size are read from its end record BEFORE the bundle is opened. 20,000 entries
# is far above any real walk's file count, and a directory holding them is far under this byte
# cap; together they keep a crafted bundle from spending the request on metadata alone.
MAX_ZIP_ENTRIES = _limit_int("HOUSESCAN_MAX_ZIP_ENTRIES", 20_000)
MAX_ZIP_DIRECTORY_KB = _limit_int("HOUSESCAN_MAX_ZIP_DIRECTORY_KB", 8_192)

# Loaded at import: invalid rules must stop the server before it answers anything.
LOADED: LoadedRules = load_rules()
# Required on every route but /health while private rules are loaded: answers carry each check's
# threshold, so a server holding Base's values must not answer strangers.
API_KEY: str | None = os.environ.get("HOUSESCAN_API_KEY") or None

_ZIP_MAGIC = b"PK\x03\x04"
_JSON_TYPES = {"application/json"}
_ZIP_TYPES = {"application/zip", "application/x-zip-compressed"}
# No declared type, or a generic binary one: the body's first bytes decide.
_SNIFF_TYPES = {"", "application/octet-stream"}
_MULTIPART = "multipart/form-data"
_BUNDLE_FIELD = "bundle"
# macOS Finder adds this folder of resource forks to every zip it makes.
_MACOS_JUNK = "__MACOSX/"
_DRIVE = re.compile(r"^[A-Za-z]:")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.path = path


def _error_response(status: int, code: str, message: str, path: str | None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "path": path}}, status_code=status
    )


app = FastAPI(
    title="House scanning placement server",
    version=SCHEMA_VERSION,
    description="Turns a phone capture (scene.json or a zip bundle) into a battery placement.",
)


def _private() -> bool:
    return "private" in LOADED.sources


@app.middleware("http")
async def _require_key(request: Request, call_next: Any) -> Response:
    # A browser's preflight carries no credentials; the request that follows it does.
    if not _private() or request.url.path == "/health" or request.method == "OPTIONS":
        return await call_next(request)
    if API_KEY is None:
        return _error_response(
            503,
            "no_api_key",
            "This server holds private rules but has no HOUSESCAN_API_KEY, so it answers nothing.",
            None,
        )
    if not _key_matches(request.headers.get("authorization", "")):
        response = _error_response(
            401,
            "unauthorized",
            "This server holds private rules: send the header Authorization: Bearer <key>.",
            None,
        )
        response.headers["WWW-Authenticate"] = "Bearer"
        return response
    return await call_next(request)


def _key_matches(header: str) -> bool:
    scheme, _, key = header.partition(" ")
    return (
        API_KEY is not None
        and scheme.lower() == "bearer"
        and hmac.compare_digest(key.strip().encode(), API_KEY.encode())
    )


# Added after the key check, so it wraps it: refusals carry CORS headers a browser can read.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(ApiError)
async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return _error_response(exc.status, exc.code, exc.message, exc.path)


_HTTP_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "body_too_large",
    415: "unsupported_media_type",
}


@app.exception_handler(HTTPException)
async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
    return _error_response(exc.status_code, code, str(exc.detail), None)


@app.exception_handler(Exception)
async def _internal_error(_request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error", exc_info=exc)
    return _error_response(
        500, "internal_error", "The server failed while handling this upload.", None
    )


def _policy() -> dict[str, Any]:
    p = LOADED.rules.policy
    return {
        "id": p.id,
        "version": p.version,
        # Same rule as solver.solve: no policy id means nothing is approved automatically.
        "auto_approve": p.auto_approve and p.id is not None,
        # Same rule as solver.solve: reported so a client can ask why nothing was rejected.
        "allow_reject": p.allow_reject,
        "sources": list(LOADED.sources),
        "rules_sha256": LOADED.sha256,
        "notice": p.notice,
    }


@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    if _private() and not _key_matches(request.headers.get("authorization", "")):
        # Anyone may learn that private rules are loaded and a key is needed, not which rules.
        return {
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "policy": {"sources": list(LOADED.sources)},
            "auth": "bearer",
        }
    return {"status": "ok", "schema_version": SCHEMA_VERSION, "policy": _policy()}


# The contract, served by the server that honours it, so clients can fetch the version they talk to.
SCHEMAS = Path(__file__).resolve().parent / "schemas"


@app.get("/v1/schemas/scene.json")
def scene_schema() -> FileResponse:
    return FileResponse(SCHEMAS / "scene.schema.json", media_type="application/schema+json")


@app.get("/v1/schemas/result.json")
def result_schema() -> FileResponse:
    return FileResponse(SCHEMAS / "result.schema.json", media_type="application/schema+json")


# --- reading the upload ---------------------------------------------------------------------------


def _media_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";")[0].strip().lower()


async def _read_body(request: Request) -> bytes:
    """The whole body, refused with 413 as soon as it passes MAX_UPLOAD_BYTES."""
    limit = MAX_UPLOAD_BYTES
    too_large = ApiError(
        413, "body_too_large", f"The upload is larger than the {limit // _MB} MB limit."
    )
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise too_large
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


async def _bundle_field(request: Request, body: bytes) -> tuple[bytes, str]:
    """The `bundle` field of a multipart form and its declared media type."""

    async def replay() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    form_request = Request(request.scope, replay)
    try:
        form = await form_request.form(max_files=1, max_fields=1)
    except HTTPException as exc:
        raise ApiError(400, "unreadable_multipart", str(exc.detail)) from None
    try:
        extra = sorted(k for k in form if k != _BUNDLE_FIELD)
        if extra:
            raise ApiError(
                400,
                "unexpected_form_field",
                f"The form may only carry one field named {_BUNDLE_FIELD!r}; got {extra}.",
            )
        field = form.get(_BUNDLE_FIELD)
        if field is None:
            raise ApiError(400, "missing_bundle", f"The form has no field named {_BUNDLE_FIELD!r}.")
        if isinstance(field, UploadFile):
            return await field.read(), (field.content_type or "").split(";")[0].strip().lower()
        return field.encode(), ""
    finally:
        await form.close()


def _reject_constant(name: str) -> float:
    raise ValueError(f"{name} is not a number JSON allows")


def _load_json(data: bytes, what: str) -> Any:
    try:
        return json.loads(data, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ApiError(
            422, "invalid_json", f"{what} is not valid JSON: {exc.msg} at line {exc.lineno}"
        ) from None
    except (ValueError, RecursionError) as exc:
        raise ApiError(422, "invalid_json", f"{what} is not valid JSON: {exc}") from None


def _unsafe(name: str) -> bool:
    n = name.replace("\\", "/")
    return n.startswith("/") or bool(_DRIVE.match(n)) or ".." in n.split("/")


def _check_scene_size(size: int) -> None:
    if size > MAX_SCENE_BYTES:
        raise ApiError(
            413,
            "scene_too_large",
            f"scene.json is {size} bytes, over the {MAX_SCENE_BYTES // _MB} MB limit.",
        )


def _zip_directory(data: bytes) -> tuple[int, int]:
    """The central directory's entry count and size, read from the zip's end record before any
    metadata is parsed. `zipfile.ZipFile` reads the whole directory and builds every entry while
    it is constructed, so a later count comes too late. `zipfile._EndRecData` is the function
    ZipFile itself uses (ZIP64 included), so the sizes checked here are the ones it will read."""
    try:
        with io.BytesIO(data) as f:
            end = zipfile._EndRecData(f)
    except OSError:
        end = None
    if not end:
        raise ApiError(
            400, "unreadable_zip", "The bundle has no zip end-of-central-directory record."
        )
    return end[zipfile._ECD_ENTRIES_TOTAL], end[zipfile._ECD_SIZE]


def _open_bundle(data: bytes) -> tuple[bytes, set[str], str]:
    """scene.json's bytes, every file name in the bundle, and the folder scene.json sits in."""
    entries, directory_bytes = _zip_directory(data)
    if entries > MAX_ZIP_ENTRIES:
        raise ApiError(
            413,
            "bundle_too_many_entries",
            f"The bundle lists {entries} entries, over the {MAX_ZIP_ENTRIES} limit.",
        )
    if directory_bytes > MAX_ZIP_DIRECTORY_KB * 1024:
        raise ApiError(
            413,
            "bundle_directory_too_large",
            f"The bundle's zip directory is {directory_bytes} bytes, over the "
            f"{MAX_ZIP_DIRECTORY_KB} KB limit.",
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zlib.error, ValueError, EOFError) as exc:
        raise ApiError(400, "unreadable_zip", f"The bundle is not a readable zip: {exc}") from None
    with zf:
        infos = zf.infolist()
        for info in infos:
            if _unsafe(info.filename):
                raise ApiError(
                    400,
                    "unsafe_zip_entry",
                    f"The bundle entry {info.filename!r} has an absolute path or '..'.",
                )
        total = sum(info.file_size for info in infos)
        if total > MAX_UNZIPPED_BYTES:
            raise ApiError(
                413,
                "bundle_too_large",
                f"The bundle unpacks to {total} bytes, over the "
                f"{MAX_UNZIPPED_BYTES // _MB} MB limit.",
            )
        names = {i.filename for i in infos if not i.is_dir()}
        prefix = _scene_folder(names)
        _check_scene_size(zf.getinfo(prefix + "scene.json").file_size)
        try:
            # Read at most one byte past the cap: a declared size can lie.
            with zf.open(prefix + "scene.json") as entry:
                scene_bytes = entry.read(MAX_SCENE_BYTES + 1)
            _check_scene_size(len(scene_bytes))
        except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError) as exc:
            raise ApiError(
                400, "unreadable_zip", f"scene.json in the bundle can't be read: {exc}"
            ) from None
    return scene_bytes, names, prefix


def _scene_folder(names: set[str]) -> str:
    if "scene.json" in names:
        return ""
    tops = {n.split("/", 1)[0] for n in names if not n.startswith(_MACOS_JUNK)}
    if len(tops) == 1:
        (top,) = tops
        if f"{top}/scene.json" in names:
            return f"{top}/"
    raise ApiError(
        400,
        "missing_scene_json",
        "The bundle has no scene.json at its root or inside a single top-level folder.",
    )


def _pointer_token(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _check_bundle_files(raw: dict[str, Any], names: set[str], prefix: str) -> None:
    """Every image scene.json names must be in the bundle, next to scene.json."""
    wanted = [
        (f"/keyframes/{i}/img", kf["img"]) for i, kf in enumerate(raw.get("keyframes", []))
    ] + [(f"/stills/{_pointer_token(k)}", v) for k, v in raw.get("stills", {}).items()]
    missing = [(path, name) for path, name in wanted if prefix + name not in names]
    if missing:
        listed = ", ".join(repr(name) for _, name in missing)
        raise ApiError(
            422,
            "missing_bundle_file",
            f"scene.json names files the bundle doesn't contain: {listed}.",
            missing[0][0],
        )


async def _upload(request: Request) -> tuple[bytes, bool]:
    """The scene payload and whether it is a zip bundle."""
    media = _media_type(request)
    if media not in _JSON_TYPES | _ZIP_TYPES | _SNIFF_TYPES | {_MULTIPART}:
        raise ApiError(
            415,
            "unsupported_media_type",
            f"Content-Type {media!r} is not supported; send application/json, application/zip "
            f"or multipart/form-data with a {_BUNDLE_FIELD!r} file.",
        )
    body = await _read_body(request)
    if media == _MULTIPART:
        body, media = await _bundle_field(request, body)
    if not body:
        raise ApiError(400, "empty_body", "The upload is empty.")
    return body, media in _ZIP_TYPES or body.startswith(_ZIP_MAGIC)


def _parse(payload: bytes, is_zip: bool) -> Scene:
    if is_zip:
        scene_bytes, names, prefix = _open_bundle(payload)
        raw = _load_json(scene_bytes, "scene.json")
    else:
        scene_bytes, names, prefix = payload, None, ""
        _check_scene_size(len(payload))
        raw = _load_json(payload, "The body")
    try:
        scene = parse_scene(raw, LOADED.rules, scene_bytes)
    except SceneError as exc:
        raise ApiError(422, "invalid_scene", exc.message, exc.path) from None
    if names is not None:
        _check_bundle_files(raw, names, prefix)
    return scene


def _solve(payload: bytes, is_zip: bool) -> tuple[Scene, dict[str, Any]]:
    scene = _parse(payload, is_zip)
    try:
        return scene, solve(scene, LOADED)
    except SceneTooComplex as exc:
        raise ApiError(
            422, "scene_too_complex", f"The server can't place this scene: {exc}."
        ) from None


# The routes read the raw body so one endpoint can take JSON, a zip or a form, so FastAPI can't
# infer the request body; publish it explicitly for clients that read /openapi.json.
SCENE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": "A scene: bare scene.json, a zip bundle (scene.json plus the images it "
        "names), or a multipart form whose one field `bundle` holds either. See "
        "schemas/scene.schema.json.",
        "content": {
            "application/json": {"schema": {"type": "object", "title": "scene.json"}},
            "application/zip": {"schema": {"type": "string", "format": "binary"}},
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [_BUNDLE_FIELD],
                    "properties": {_BUNDLE_FIELD: {"type": "string", "format": "binary"}},
                }
            },
        },
    }
}


@app.post(
    "/v1/placements",
    openapi_extra=SCENE_BODY,
    responses={200: {"description": "The result (schemas/result.schema.json)"}},
)
async def placements(request: Request) -> JSONResponse:
    payload, is_zip = await _upload(request)
    _scene, result = await run_in_threadpool(_solve, payload, is_zip)
    return JSONResponse(result)


@app.post(
    "/v1/placements/site-plan.svg",
    openapi_extra=SCENE_BODY,
    response_class=Response,
    responses={200: {"content": {"image/svg+xml": {}}, "description": "The site plan"}},
)
async def site_plan(request: Request) -> Response:
    payload, is_zip = await _upload(request)

    def render() -> str:
        scene, result = _solve(payload, is_zip)
        return siteplan.render(scene, result, LOADED)

    svg = await run_in_threadpool(render)
    return Response(content=svg, media_type="image/svg+xml")
