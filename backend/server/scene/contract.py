"""Check an emitted scene against the WS-00 scene contract."""

from __future__ import annotations

from pydantic import ValidationError

from server.contracts.scene import Scene, scene_consistency_errors
from server.scene.errors import SceneError


def check_scene(scene: dict) -> None:
    """Raise ``SceneError`` when the document is not a scene v0 contract."""
    try:
        Scene.model_validate(scene)
    except ValidationError as exc:
        raise SceneError("input_missing:scene", exc.errors()[0]["msg"]) from exc
    errors = scene_consistency_errors(scene)
    if errors:
        raise SceneError("input_missing:scene", errors[0])
