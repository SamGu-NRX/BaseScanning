"""Dense-stage alias. Track arrays live in ``server.common``."""

from server.common.tracks import empty_tracks, read_tracks, resolve_tracks, write_tracks

__all__ = ["empty_tracks", "read_tracks", "resolve_tracks", "write_tracks"]
