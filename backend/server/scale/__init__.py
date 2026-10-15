"""Scale stage. Vision stays in WS-08; this package has no ``identify_meter`` entry (SD-46)."""

from server.scale.run import execute as run

STAGE = "scale"
__all__ = ["STAGE", "run"]
