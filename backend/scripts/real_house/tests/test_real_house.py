import json
import zipfile
from pathlib import Path

import redact
import score_kds
import scrub_packet
import session_v2_to_packet
import truth_to_scene
from PIL import Image


def _truth() -> dict:
    return {
        "format": "real-house-truth/v0",
        "house_id": "real-001",
        "meter": {"meter_number": "REDACTED", "manufacturer": "Itron"},
        "walls": [
            {
                "id": "w1",
                "role": "house",
                "length_m": 9.75,
                "height_m": 2.4,
                "left_end": "corner",
                "right_end": "limit",
            }
        ],
        "measurements": [
            {
                "id": "meter.height",
                "value_m": 1.52,
                "plus_minus_m": 0.01,
                "tool": "L1",
                "photo": "k01.jpg",
            }
        ],
        "objects": [
            {
                "id": "o1",
                "class": "window",
                "wall_id": "w1",
                "s_m": [2.41, 3.33],
                "bottom_m": 1.18,
                "top_m": 2.54,
            }
        ],
        "ground_regions": [{"id": "g1", "material": "lawn", "from_wall_m": 0.0, "to_wall_m": 4.3, "slope_deg": 1.5}],
    }


def test_redact_replaces_number_fields():
    cleaned = redact.redact({"meter_number": "123", "nested": {"number_candidates": ["9"]}, "keep": 1})
    assert cleaned["meter_number"] == "REDACTED"
    assert cleaned["nested"]["number_candidates"] == "REDACTED"
    assert cleaned["keep"] == 1


def test_truth_to_scene_anchors_at_the_meter():
    scene = truth_to_scene.truth_to_scene(_truth(), "real-001")
    assert scene["format"] == "base-scene/v0"
    assert scene["frame"]["units"] == "m"
    assert scene["frame"]["convention"] == "meter_anchored/v0"
    assert scene["walls"][0]["height_m"] == 2.4
    assert "source" not in scene["walls"][0]
    assert scene["provenance"]["producer"]["path"] == "gt-tape"
    assert scene["provenance"]["scale_source"] == "ground_truth"
    assert "packet_id" not in scene["provenance"]["inputs"]
    assert scene["coverage"]["method"].startswith("none:")
    assert scene["objects"][0]["id"] == "meter"
    assert scene["objects"][0]["geometry"]["kind"] == "point"
    window = scene["objects"][1]["geometry"]
    assert window["kind"] == "wall_rect"
    assert window["s_m"] == [2.41, 3.33]
    assert scene["chain"]["ends"]["right"]["kind"] == "limit"
    assert scene["chain"]["ends"]["left"]["kind"] == "limit"


def test_wall_without_height_exits_2():
    truth = _truth()
    del truth["walls"][0]["height_m"]
    try:
        truth_to_scene.truth_to_scene(truth, "real-001")
    except truth_to_scene.TruthError as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected a missing height")


def test_unredacted_meter_number_exits_4():
    truth = _truth()
    truth["meter"]["meter_number"] = "LIVE-NUMBER"
    try:
        truth_to_scene.truth_to_scene(truth, "real-001")
    except truth_to_scene.TruthError as exc:
        assert exc.code == 4
    else:
        raise AssertionError("expected privacy failure")


def test_scrub_removes_location_and_dropped_frame(tmp_path: Path):
    src = tmp_path / "in"
    (src / "keyframes").mkdir(parents=True)
    image = src / "keyframes" / "k00002.jpg"
    Image.new("RGB", (8, 8), "white").save(image)
    packet = {
        "keyframes": [{"id": "k00001"}, {"id": "k00002"}],
        "stills": [{"id": "s1", "location": {"lat": 1, "lon": 2}}],
        "taps": [{"id": "k00002"}],
        "files": [{"path": "keyframes/k00002.jpg", "bytes": 1, "sha256": "0", "role": "keyframe"}],
        "streams": {"location": {"lat": 1}},
        "scaleReference": {"ext": {"serviceAddress": "SHOULD-BE-REMOVED"}},
        "meter_number": "555",
    }
    (src / "packet.json").write_text(json.dumps(packet))
    out = tmp_path / "out"
    report = scrub_packet.scrub(src, out, ["k00002"])
    assert report["dropped"] == ["k00002"]
    assert "streams.location" in report["removed_fields"]
    assert "scaleReference.ext.serviceAddress" in report["removed_fields"]
    cleaned = json.loads((out / "packet.json").read_text())
    assert "location" not in cleaned.get("streams", {})
    assert cleaned["meter_number"] == "REDACTED"
    assert cleaned["keyframes"] == [{"id": "k00001"}]
    assert not (out / "keyframes" / "k00002.jpg").exists()


def test_score_kds_reports_inches():
    gt = truth_to_scene.truth_to_scene(_truth(), "real-001")
    recon = json.loads(json.dumps(gt))
    recon["objects"][1]["geometry"]["s_m"][0] += 0.0254
    report = score_kds.score_kds(gt, recon)
    edge = next(row for row in report["errors"] if row["id"] == "opening.edge_s[o1:left]")
    assert abs(edge["error_in"] - 1.0) < 1e-6
    assert report["max_in"] >= 1.0


def test_session_v2_requires_pose_and_writes_replay(tmp_path: Path):
    good = tmp_path / "good.zip"
    pose = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    image = tmp_path / "k1.jpg"
    Image.new("RGB", (4, 4), "black").save(image)
    session = {"keyframes": [{"id": "k1", "img": "k1.jpg", "pose": pose, "intrinsics": [1, 1, 2, 2], "w": 4, "h": 4}]}
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr("session.json", json.dumps(session))
        archive.write(image, "k1.jpg")
    out = session_v2_to_packet.convert(good, tmp_path / "packet", "arkit")
    packet = json.loads(out.read_text())
    assert packet["formatVersion"] == "0.4"
    assert packet["source"]["kind"] == "replay"
    assert "imuRaw" in packet["source"]["notes"]
    assert "location" not in packet
    bad = tmp_path / "bad.zip"
    session["keyframes"][0].pop("pose")
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("session.json", json.dumps(session))
        archive.write(image, "k1.jpg")
    try:
        session_v2_to_packet.convert(bad, tmp_path / "bad-out", "arkit")
    except FileNotFoundError:
        return
    raise AssertionError("expected missing pose")
