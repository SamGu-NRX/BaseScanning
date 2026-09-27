"""The only module in ``server.eval`` that imports the criteria and poses stages.

SD-84: plain imports of the names the owners export (``evaluate_scene`` from
``server.criteria``, ``run_poses`` from ``server.poses``). The imports stay inside
these functions so a missing stage is ``ImportError: not_merged:<ws>`` at call
time, and the dependent rows SKIP. No stub modules are added in those packages.
"""

from __future__ import annotations


def criteria_engine():
    """``(CriteriaConfig, evaluate_scene)`` from ``server.criteria``."""
    try:
        from server.criteria import CriteriaConfig, evaluate_scene
    except ImportError as exc:
        raise ImportError("not_merged:ws-09") from exc
    return CriteriaConfig, evaluate_scene


def poses_run():
    """``server.poses.run_poses``."""
    try:
        from server.poses import run_poses
    except ImportError as exc:
        raise ImportError("not_merged:ws-04") from exc
    return run_poses


__all__ = ["criteria_engine", "poses_run"]
