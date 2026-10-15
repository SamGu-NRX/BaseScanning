"""Dense geometry stage.

``run`` is loaded lazily so a pure-function import does not initialise the scene
package while this package is still loading.
"""

STAGE = "dense"

__all__ = ["STAGE", "run"]


def __getattr__(name: str):
    if name == "run":
        from server.dense.run import run as fn

        globals()["run"] = fn
        return fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
