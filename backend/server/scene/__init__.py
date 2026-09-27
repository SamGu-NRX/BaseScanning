"""Scene stage and object merge.

``run`` is loaded lazily so importing ``server.scene.harness`` does not pull the
dense stage back in while that stage is still initialising.
"""

STAGE = "scene"

__all__ = ["STAGE", "build_scene", "merge_objects", "run"]


def __getattr__(name: str):
    if name == "run":
        from server.scene.run import run as fn

        globals()["run"] = fn
        return fn
    if name == "build_scene":
        from server.scene.run import build_scene

        return build_scene
    if name == "merge_objects":
        from server.scene.merge import merge_objects

        return merge_objects
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
