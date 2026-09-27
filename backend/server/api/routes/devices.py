"""Store a device token. Delivery is WS-10."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response

from server.api.deps import get_deps, require_principal
from server.api.errors import fail
from server.contracts.api import DeviceRegisterRequest


def register(app: FastAPI) -> None:
    @app.post("/v1/devices", status_code=204)
    def register_device(body: DeviceRegisterRequest, request: Request):
        principal = require_principal(request)
        deps = get_deps(request)
        deps.charge(principal.label, "devices", 30)
        if len(body.token) < 8:
            raise fail(422, "schema", "/token", "token is too short")
        slot = f"device:{principal.label}"
        current = deps.state.get_kv(slot) or {"tokens": []}
        tokens = list(current.get("tokens") or [])
        if body.token in tokens:
            return Response(status_code=204)
        if len(tokens) >= 10:
            raise fail(422, "too_many_devices", "/token", "a principal can store 10 device tokens")
        tokens.append(body.token)
        deps.state.put_kv(slot, {"tokens": tokens, "platform": body.platform, "bundleId": body.bundleId})
        return Response(status_code=204)
