import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ssr_overlap_check.py"
HASHES = ROOT / "data" / "hygiene" / "ssr_shingles.sha256"


def load_checker():
    spec = importlib.util.spec_from_file_location("ssr_overlap_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_hash_file_is_digests_only():
    text = HASHES.read_text()
    lines = text.splitlines()
    assert lines
    assert text.endswith("\n")
    assert lines == sorted(set(lines))
    assert all(len(line) == 64 and all(ch in "0123456789abcdef" for ch in line) for line in lines)


def test_flags_a_planted_shingle_and_ignores_a_shorter_run():
    checker = load_checker()
    words = [f"zzqtoken{index:02d}" for index in range(12)]
    digest = checker.shingle_digest(words)
    assert checker.overlapping_shingle_count(" ".join(words), {digest}) == 1
    assert checker.overlapping_shingle_count(" ".join(words[:11]), {digest}) == 0
    shifted = words[1:] + ["zzqtoken99"]
    assert checker.overlapping_shingle_count(" ".join(shifted), {digest}) == 0


def test_emit_writes_hashes_without_source_words(tmp_path: Path):
    checker = load_checker()
    source = tmp_path / "source.txt"
    words = [f"sampleword{index:02d}" for index in range(14)]
    source.write_text(" ".join(words) + "\n")
    dest = tmp_path / "out.sha256"
    count = checker.emit_shingles(source, dest)
    body = dest.read_text()
    assert count == 3
    assert "sampleword" not in body
    assert checker.overlapping_shingle_count(source.read_text(), set(body.splitlines())) == 3


def test_repo_has_no_ssr_shingle_overlap():
    result = subprocess.run(
        ["python3", str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ssr overlap: 0" in result.stdout


# These thirteen results had a contiguous copied key run removed by reordering.
# score.py reads json["decision"] and must still see the model field.
P08_REORDERED = [
    "criteria/c3-vlm/results/eth3d-facade/photo_only_meter_hint_verbatim/gpt-5.5/p08_battery_space.json",
    "criteria/c3-vlm/results/eth3d-facade/tools_laser_gt/claude-sonnet-5/p08_battery_space.json",
    "criteria/c3-vlm/results/eth3d-facade/tools_laser_gt_verbatim/gpt-5.5/p08_battery_space.json",
    "criteria/c3-vlm/results/tt-barn/photo_only_verbatim/gpt-5.5/p08_battery_space.json",
    "criteria/c3-vlm/results/yt-0gotY_syWWU/photo_only_verbatim/claude-opus-5-5/p08_battery_space.json",
    "criteria/c3-vlm/results/yt-12Ks1idWj40/photo_only/claude-opus-5-5/p08_battery_space.json",
    "criteria/c3-vlm/results/yt-12Ks1idWj40/photo_only/claude-sonnet-5/p08_battery_space.json",
    "criteria/c3-vlm/results/yt-12Ks1idWj40/photo_only/gpt-5.5/p08_battery_space.json",
    "criteria/c3-vlm/results/yt-12Ks1idWj40/photo_only_verbatim/claude-opus-5-5/p08_battery_space.json",
    "criteria/c3-vlm/results/yt-12Ks1idWj40/photo_only_verbatim/claude-sonnet-5/p08_battery_space.json",
    "criteria/c3-vlm/results/yt-12Ks1idWj40/photo_only_verbatim/gpt-5.5/p08_battery_space.json",
    "criteria/c3-vlm/results/yt-5-M-7f35Tyg/photo_only_verbatim/claude-opus-5-5/p08_battery_space.json",
    "criteria/c3-vlm/results/yt-Qm4b1cw8pV4/photo_only_verbatim/gpt-5.5/p08_battery_space.json",
]
MODEL_FIELDS = (
    "decision",
    "requires_member_action",
    "needs_more_photos",
    "missing_views",
    "distance_ft",
    "front_clearance_ft",
    "drive_path_ft",
    "justification",
    "reference_object",
)


def test_reordered_p08_results_keep_model_fields():
    for rel in P08_REORDERED:
        data = json.loads((ROOT / rel).read_text())
        body = data["json"]
        assert "prompt_echo" not in body
        for key in MODEL_FIELDS:
            assert key in body, rel
        # Same fallback score.py uses when the decision field is missing.
        assert (body.get("decision") or "manual_review") == data["verdicts"]["p08"]
        assert isinstance(body["requires_member_action"], bool)
        assert isinstance(body["needs_more_photos"], bool)
        assert isinstance(body["missing_views"], list)
        assert isinstance(body["justification"], str) and body["justification"]


def test_load_digests_rejects_non_hashes(tmp_path: Path):
    checker = load_checker()
    bad = tmp_path / "bad.sha256"
    bad.write_text("not-a-hash\n")
    with pytest.raises(SystemExit):
        checker.load_digests(bad)
