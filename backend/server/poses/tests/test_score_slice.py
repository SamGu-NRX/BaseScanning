"""Wall lookup for the facade scorer. Opening rows are not keyed by wall id."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _score_poses():
    path = Path(__file__).resolve().parents[3] / "scripts" / "score_poses.py"
    spec = importlib.util.spec_from_file_location("score_poses", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_w1_includes_openings_and_the_meter():
    module = _score_poses()
    walls = {"meter": "w1", "win1": "w1", "door": "w3"}
    result = {
        "key_distances": {
            "rows": [
                {"id": "opening.edge_s[win1:left]", "status": "scored", "coverage_truth": "observed", "error": 1.0},
                {"id": "opening.bottom[win1]", "status": "scored", "coverage_truth": "observed", "error": 8.0},
                {"id": "meter.height", "status": "scored", "coverage_truth": "observed", "error": 2.0},
                {"id": "opening.top[door]", "status": "scored", "coverage_truth": "observed", "error": 9.0},
                {"id": "wall.length[w1]", "status": "scored", "coverage_truth": "extent_limited", "error": 40.0},
            ]
        }
    }
    w1 = module._kd_slice(result, "w1", walls)
    assert w1["n"] == 3
    heights = module._observed_errors(result, module._is_height_row)
    in_plane = module._observed_errors(result, lambda row_id: not module._is_height_row(row_id))
    assert heights["n"] == 3
    assert in_plane["n"] == 1
    source = module.inspect.getsource(module.geomscore.score_cloud)
    assert module._A7_STOCK in source or 'np.asarray(cams_src[n]["K"], float)' in source
    patched = module.patched_score_cloud()
    assert patched.__name__ in {"score_cloud_a7", "score_cloud"}
