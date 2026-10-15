"""Whether a dense cloud may measure a wall or only mark coverage.

SD-65: walls and measurements are fit from SfM plus LiDAR points in both
flows. π³ / DA3 / MoGe depth (``src`` 1) is coverage, occlusion and gap
filling only. Sparse points stay ``src`` 0 and LiDAR points stay ``src`` 2.
"""

from __future__ import annotations

COLMAP_DEPTH_P90_IN = (7.0, 21.0)


def coverage_only_dense(source_frame: str, flow: str) -> bool:
    """π³ / DA3 / MoGe depth never measures, on guided and photo captures.

    ``source_frame`` and ``flow`` stay in the signature so callers do not
    change. The measurement mask is ``src != 1``.
    """
    del source_frame, flow
    return True
