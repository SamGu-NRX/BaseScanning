import json
import struct

import numpy as np
import pytest

from hsverify import replaycheck
from hsverify.replaycheck import (
    accuracy,
    check_format,
    jpeg_size,
    pose_matrix,
    rigid_problems,
    yaw_translation_fit,
)


def yaw(deg: float) -> np.ndarray:
    t = np.radians(deg)
    return np.array([[np.cos(t), 0, np.sin(t)], [0, 1, 0], [-np.sin(t), 0, np.cos(t)]])


def flat(m: np.ndarray) -> list[float]:
    return [float(x) for x in m.T.reshape(-1)]  # column by column


def pose(r: np.ndarray, t) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3], m[:3, 3] = r, t
    return m


def write_jpeg_header(path, w: int, h: int) -> None:
    # SOI, then a baseline SOF0 segment with the size; enough for jpeg_size.
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    path.write_bytes(b"\xff\xd8" + sof + b"\xff\xd9")


def test_pose_is_read_column_by_column():
    m = pose(yaw(30), [1, 2, 3])
    assert np.allclose(pose_matrix(flat(m)), m)


def test_rigid_problems():
    assert rigid_problems(pose(yaw(10), [0, 0, 0])) == []
    bad = pose(yaw(10), [0, 0, 0])
    bad[:3, :3] *= 1.1
    assert rigid_problems(bad)


def test_yaw_fit_recovers_known_transform():
    rng = np.random.default_rng(0)
    src = rng.normal(size=(20, 3)) * 5
    dst = src @ yaw(123).T + np.array([4.0, -1.0, 2.0])
    r, t = yaw_translation_fit(src, dst)
    assert np.allclose(r, yaw(123), atol=1e-9)
    assert np.allclose(t, [4.0, -1.0, 2.0], atol=1e-9)


def test_jpeg_size(tmp_path):
    write_jpeg_header(tmp_path / "a.jpg", 1280, 720)
    assert jpeg_size(tmp_path / "a.jpg") == (1280, 720)


def session_with(tmp_path, positions, spacing=0.5):
    (tmp_path / "keyframes").mkdir()
    keyframes = []
    for i, p in enumerate(positions, 1):
        write_jpeg_header(tmp_path / "keyframes" / f"k{i}.jpg", 640, 480)
        keyframes.append(
            {
                "id": f"k{i}",
                "img": f"keyframes/k{i}.jpg",
                "w": 640,
                "h": 480,
                "intrinsics": [500, 500, 320, 240],
                "pose": flat(pose(yaw(0), p)),
                "timestamp": float(i),
                "tracking": "normal",
                "reason": "motion",
            }
        )
    return {
        "format": "measure-lab-session",
        "formatVersion": 2,
        "session": {},
        "gates": {"keyframeSpacingMeters": spacing, "keyframeSpacingDegrees": 15.0},
        "keyframes": keyframes,
    }


def test_valid_session_has_no_format_errors(tmp_path):
    s = session_with(tmp_path, [[0, 0, -0.6 * i] for i in range(5)])
    errors, summary = check_format(tmp_path, s)
    assert errors == []
    assert summary["keyframes"] == 5


def test_cli_rejects_portrait_keyframe_with_matching_dimensions_and_intrinsics(tmp_path, capsys):
    session = session_with(tmp_path, [[0, 0, 0]])
    keyframe = session["keyframes"][0]
    keyframe.update(w=480, h=640, intrinsics=[500, 500, 240, 320])
    write_jpeg_header(tmp_path / keyframe["img"], 480, 640)
    (tmp_path / "session.json").write_text(json.dumps(session))

    assert replaycheck.main([str(tmp_path), "--json"]) == 1

    report = json.loads(capsys.readouterr().out)
    assert any("k1: portrait keyframe" in error for error in report["format_errors"])


def test_format_errors_are_specific(tmp_path):
    s = session_with(tmp_path, [[0, 0, -0.6 * i] for i in range(3)])
    s["keyframes"][1]["w"] = 1920  # disagrees with the JPEG
    s["keyframes"][2]["pose"] = flat(pose(yaw(0), [0, 0, -0.7]))  # 0.1 m after the previous
    (tmp_path / "keyframes" / "k1.jpg").unlink()
    errors, _ = check_format(tmp_path, s)
    joined = "\n".join(errors)
    assert "k1: image keyframes/k1.jpg not found" in joined
    assert "k2: JPEG is (640, 480)" in joined
    assert "1 motion keyframes closer than the declared spacing gate" in joined


def test_malformed_fields_are_format_errors_not_crashes(tmp_path):
    s = session_with(tmp_path, [[0, 0, -0.6 * i] for i in range(4)])
    del s["gates"]["keyframeSpacingDegrees"]
    s["keyframes"][1]["intrinsics"] = [500, 500, 320]
    s["keyframes"][2]["pose"] = s["keyframes"][2]["pose"][:15]
    s["keyframes"][3]["intrinsics"] = [500, "500", 320, 240]
    errors, _ = check_format(tmp_path, s)
    assert "gates set keyframeSpacingMeters without keyframeSpacingDegrees" in errors
    for name in ("k2", "k3", "k4"):
        assert f"{name}: intrinsics must be 4 finite numbers and pose 16" in errors


@pytest.mark.parametrize("scale", [1.0, 0.64])
def test_accuracy_separates_scale_from_alignment(tmp_path, scale):
    truth_pos = [[0.0, 0.0, -float(i)] for i in range(40)]  # straight walk along -z
    s = session_with(tmp_path, [[p[0], p[1], p[2] * scale] for p in truth_pos])
    # Ground truth in its own world: turned 90 degrees and shifted.
    truth = {
        "keyframes": [
            {"keyframe": kf["id"], "pose": flat(pose(yaw(90), yaw(90) @ np.array(p) + [7, 0, 3]))}
            for kf, p in zip(s["keyframes"], truth_pos, strict=True)
        ]
    }
    result = accuracy(json.loads(json.dumps(s)), truth)
    assert result["arkit_to_truth_length_ratio"] == pytest.approx(scale, abs=1e-3)
    assert result["best_fit_error_after_scale_m"]["p90"] == pytest.approx(0, abs=1e-6)
    assert result["max_vertical_axis_mismatch"] == pytest.approx(0, abs=1e-9)
    expected_final = (1 - scale) * 39
    assert result["anchored_error_m_final"] == pytest.approx(expected_final, abs=1e-3)


def test_oversized_integer_is_a_format_error(tmp_path):
    session = session_with(tmp_path, [[0, 0, 0]])
    session["keyframes"][0]["intrinsics"][0] = 10**400
    errors, _ = check_format(tmp_path, session)
    assert any("finite numbers" in e for e in errors)


def test_invalid_replay_with_ground_truth_still_reports(tmp_path, capsys):
    session = session_with(tmp_path, [[0, 0, 0]])
    del session["keyframes"][0]["pose"]
    (tmp_path / "session.json").write_text(json.dumps(session))
    (tmp_path / "ground_truth.json").write_text("{}")
    assert replaycheck.main([str(tmp_path), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["format_errors"] and "pose_accuracy" not in data
