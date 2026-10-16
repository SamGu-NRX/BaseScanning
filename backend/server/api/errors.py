"""HTTP errors always use the ApiError shape, except healthz and the volume BadDigest body."""

from __future__ import annotations

from fastapi.responses import JSONResponse


class ApiFailure(Exception):
    def __init__(self, status: int, errors: list[dict], headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.errors = errors
        self.headers = headers or {}


def fail(status: int, code: str, pointer: str, message: str, **headers: str) -> ApiFailure:
    return ApiFailure(status, [{"code": code, "pointer": pointer, "message": message}], headers)


def error_response(failure: ApiFailure) -> JSONResponse:
    return JSONResponse({"errors": failure.errors}, status_code=failure.status, headers=failure.headers)


def pointer_from_loc(loc: tuple) -> str:
    parts: list[str] = []
    for item in loc:
        if item in {"body", "query", "header", "path"}:
            continue
        parts.append(str(item))
    return "/" + "/".join(parts) if parts else "/"
