"""Photo-egress policy for deployed stage containers.

A missing or unrecognized ``SMP_ENVIRONMENT`` is prod. Prod keeps provider
calls off unless ``VISION_EXTERNAL_CALLS`` is the explicit decision ``on``.
"""

from __future__ import annotations

import os

_KNOWN = frozenset({"dev", "prod"})


def normalize_environment(raw: str | None) -> str:
    if raw in _KNOWN:
        return raw
    return "prod"


def photo_egress_allowed(*, environment: str | None, vision: str | None) -> bool:
    """True only when this process may send capture bytes to a model provider.

    A missing or unknown environment is not a prod opt-in, so ``vision="on"``
    does not send photos.
    """

    if environment == "prod":
        return vision == "on"
    if environment == "dev":
        return vision != "off"
    return False


def apply_deployed_privacy(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Stage entrypoints call this before reading settings.

    Missing and unknown environments become ``prod`` with photo egress off.
    An explicit ``prod`` keeps egress only when ``VISION_EXTERNAL_CALLS`` is ``on``.
    """

    target = os.environ if environ is None else environ
    raw = target.get("SMP_ENVIRONMENT")
    environment = normalize_environment(raw)
    target["SMP_ENVIRONMENT"] = environment
    if raw not in _KNOWN or target.get("VISION_EXTERNAL_CALLS") != "on":
        if environment == "prod":
            target["VISION_EXTERNAL_CALLS"] = "off"
    return target
