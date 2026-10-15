"""Scene-stage alias. The values live in ``server.common`` so coverage can read them."""

from server.common.params import SceneParams, scene_params_from_env

__all__ = ["SceneParams", "scene_params_from_env"]
