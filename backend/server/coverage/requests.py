"""Coverage-stage alias. Request construction lives in ``server.common``."""

from server.common.requests import GROUND_ANGLE, WALL_ANGLE, framing_ground_request, views_needed

__all__ = ["GROUND_ANGLE", "WALL_ANGLE", "framing_ground_request", "views_needed"]
