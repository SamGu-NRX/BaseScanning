import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import pytest

# A manifest with well-formed but meaningless hashes: enough for schema checks.
# Satisfiability tests build their own files under tmp_path and hash them.
VALID_MANIFEST = {
    "evidence_pack_format_version": "1.0",
    "capture_schema_version": "synthetic-capture/2026.10",
    "capture": {
        "capture_id": "capture-synth-0001",
        "captured_at": "2026-10-10T00:00:00Z",
        "capture_root": "tools/capture-evidence-pack/fixtures/example",
        "source_refs": [
            {
                "role": "frame",
                "path": "capture/frame_0001.json",
                "sha256": "a" * 64,
                "bytes": 10,
            }
        ],
    },
    "result": {
        "result_id": "result-synth-0001",
        "path": "result/result.json",
        "sha256": "b" * 64,
        "bytes": 10,
    },
    "missing_fields": [
        {
            "field": "capture.depth_map",
            "detail": "the synthetic device produced no depth map",
        }
    ],
    "reproduction": {
        "command": "python tools/capture-evidence-pack/read.py tools/capture-evidence-pack/evidence/example.zip"
    },
    "geometry_correctness": {
        "assertion": None,
        "note": "identity-only pack: no geometry-correctness check ran",
    },
    "packaged_files": [
        {"path": "report.md", "sha256": "c" * 64, "bytes": 10},
    ],
}


@pytest.fixture
def valid_manifest():
    import copy

    return copy.deepcopy(VALID_MANIFEST)
