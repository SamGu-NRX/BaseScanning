"""Coverage stage.

``run`` is loaded lazily so coverage helpers can be imported from the scene
builder without initialising the dense stage first.
"""

STAGE = "coverage"

__all__ = ["STAGE", "run"]


def __getattr__(name: str):
    if name == "run":
        from server.coverage.run import run as fn

        globals()["run"] = fn
        return fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
