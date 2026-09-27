"""Bearer checks.

``HSS_AUTH_MODE=none`` accepts every request as the device principal ``demo``.
Static tokens and JWT verification stay in this module and are unused in that mode.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Never

import jwt
from jwt import PyJWKClient

from server.api.errors import fail
from server.common.config import Settings


@dataclass(frozen=True)
class Principal:
    label: str
    reviewer: bool


def parse_tokens(raw: str | None) -> dict[str, Principal]:
    if raw is None or raw.strip() == "":
        return {}
    found: dict[str, Principal] = {}
    for part in raw.split(","):
        piece = part.strip()
        if not piece or ":" not in piece:
            raise RuntimeError("CAPTURE_API_TOKENS is malformed")
        reviewer, label, token = _token_entry(piece)
        if not label or len(token) < 32:
            raise RuntimeError("CAPTURE_API_TOKENS is malformed")
        found[token] = Principal(label=label, reviewer=reviewer)
    return found


def _token_entry(piece: str) -> tuple[bool, str, str]:
    """`reviewer:<label>:<token>` is an explicit reviewer. A label of `reviewer` is too.

    A subject or label that merely starts with `reviewer` is not.
    """
    parts = piece.split(":")
    if len(parts) == 3 and parts[0] == "reviewer":
        return True, parts[1], parts[2]
    if len(parts) != 2:
        raise RuntimeError("CAPTURE_API_TOKENS is malformed")
    label, token = parts
    return label == "reviewer", label, token


def _jwt_reviewer(claims: dict) -> bool:
    """Reviewer comes from a verified role, roles, or scope claim. Never from `sub`."""
    if claims.get("role") == "reviewer":
        return True
    roles = claims.get("roles")
    if isinstance(roles, list) and "reviewer" in roles:
        return True
    scope = claims.get("scope")
    if isinstance(scope, str) and "reviewer" in scope.split():
        return True
    return False


def _static(presented: str, tokens: dict[str, Principal]) -> Principal | None:
    matched: Principal | None = None
    for token, principal in tokens.items():
        if hmac.compare_digest(presented, token):
            matched = principal
    return matched


def authenticate(authorization: str | None, settings: Settings, tokens: dict[str, Principal]) -> Principal:
    mode = settings.HSS_AUTH_MODE
    if mode == "none":
        return Principal(label="demo", reviewer=False)
    if not authorization or not authorization.startswith("Bearer "):
        raise fail(
            401,
            "unauthorized",
            "/headers/authorization",
            "bearer token required",
            **{"WWW-Authenticate": 'Bearer realm="capture-api"'},
        )
    presented = authorization.removeprefix("Bearer ")
    if mode == "jwt":
        if not settings.HSS_AUTH_JWKS_URL or not settings.HSS_AUTH_ISSUER:
            raise fail(
                401,
                "unauthorized",
                "/headers/authorization",
                "jwt auth is not configured",
                **{"WWW-Authenticate": 'Bearer realm="capture-api"'},
            )
        try:
            key = PyJWKClient(settings.HSS_AUTH_JWKS_URL).get_signing_key_from_jwt(presented).key
            claims = jwt.decode(
                presented,
                key,
                algorithms=["RS256", "ES256"],
                audience=settings.HSS_AUTH_AUDIENCE,
                issuer=settings.HSS_AUTH_ISSUER,
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise fail(
                401,
                "unauthorized",
                "/headers/authorization",
                "bearer token rejected",
                **{"WWW-Authenticate": 'Bearer realm="capture-api"'},
            ) from exc
        label = str(claims.get("sub") or "")
        if not label:
            raise fail(
                401,
                "unauthorized",
                "/headers/authorization",
                "bearer token rejected",
                **{"WWW-Authenticate": 'Bearer realm="capture-api"'},
            )
        return Principal(label=label, reviewer=_jwt_reviewer(claims))
    if mode == "static":
        principal = _static(presented, tokens)
        if principal is None:
            raise fail(
                401,
                "unauthorized",
                "/headers/authorization",
                "bearer token rejected",
                **{"WWW-Authenticate": 'Bearer realm="capture-api"'},
            )
        return principal
    unreachable: Never = mode
    raise AssertionError(unreachable)
