"""Hand-computed cases for the field-session pipeline, on a synthetic session and survey.

Scene (ARKit world, y up): a wall in the plane z = 0 facing +z, ground at y = 0. Ground contacts
at (0, 0, 0) and (10, 0, 0), a meter on the wall at (2, 1.5, 0), a fence foot at (3, 0, 3). Two
keyframes look along -z with fx = fy = 500 and centre (320, 240): k00001 at (3, 1.5, 12) taps the
first contact and the fence foot, k00002 at (6, 1.5, 12) taps the second contact and the meter.
"""

import hashlib
import json
import zipfile

import cv2
import numpy as np
import pytest

from evals import field
from evals.field import rotated_K, upright_turns

FT = 0.3048
CAMERAS = {"k00001": np.array([3.0, 1.5, 12.0]), "k00002": np.array([6.0, 1.5, 12.0])}
POINTS = {
    "p1": np.array([0.0, 0.0, 0.0]),
    "p2": np.array([10.0, 0.0, 0.0]),
    "p3": np.array([2.0, 1.5, 0.0]),
    "p4": np.array([3.0, 0.0, 3.0]),
}
TAPPED_FROM = {"p1": "k00001", "p4": "k00001", "p2": "k00002", "p3": "k00002"}


def _pixel(X, kid):
    """Continuous pixel of a world point in a keyframe (identity rotation)."""
    x, y, z = X - CAMERAS[kid]
    return [320 + 500 * x / -z, 240 - 500 * y / -z], -z


def _session():
    taps, points = [], []
    for i, (pid, X) in enumerate(POINTS.items()):
        pixel, _ = _pixel(X, TAPPED_FROM[pid])
        taps.append({"id": f"t{i}", "keyframe": TAPPED_FROM[pid], "pixel": pixel, "point": pid})
        points.append({"id": pid, "kind": "ground", "position": X.tolist(), "taps": [f"t{i}"]})

    def keyframe(kid):
        pose = np.eye(4)
        pose[:3, 3] = CAMERAS[kid]
        return {
            "id": kid,
            "img": f"keyframes/{kid}.jpg",
            "w": 640,
            "h": 480,
            "intrinsics": [500.0, 500.0, 320.0, 240.0],
            "pose": pose.T.reshape(-1).tolist(),
            "timestamp": 50.0,
        }

    def m(mid, a, b, key, value, ref=None):
        return {
            "id": mid,
            "time": 100.0,
            "from": a,
            "to": b,
            "referenceWall": ref,
            "values": {key: value},
            "compared": key,
            "accepted": True,
        }

    # The rig reads 2% short on every value.
    return {
        "format": "measure-lab-session",
        "formatVersion": 2,
        "units": {"length": "meters"},
        "session": {"id": "synthetic-field", "startedAtUptime": 40.0},
        "keyframes": [keyframe(k) for k in CAMERAS],
        "taps": taps,
        "points": points,
        "walls": [
            {"id": "w1", "contacts": ["p1", "p2"], "cameraPosition": CAMERAS["k00001"].tolist()}
        ],
        "measurements": [
            m("m1", "p1", "p2", "alongWall", 10 * 0.98, "w1"),
            m("m2", "p3", "w1", "heightAboveGround", 1.5 * 0.98),
            m("m3", "p4", "w1", "gapToWall", 3 * 0.98),
            m("m4", "p3", "p1", "straight", 2.5 * 0.98),
        ],
        "refusals": [],
    }


def _depth(kid, scale):
    """A keyframe's depth: true z-depth x scale in a small patch around each pixel it taps."""
    d = np.full((480, 640), np.nan)
    for pid, X in POINTS.items():
        if TAPPED_FROM[pid] != kid:
            continue
        (u, v), z = _pixel(X, kid)
        cu, cv_ = int(u - 0.5), int(v - 0.5)
        d[cv_ - 2 : cv_ + 3, cu - 2 : cu + 3] = z * scale
    return d


@pytest.fixture(autouse=True)
def _free_space_is_not_under_test(monkeypatch):
    # Unpacking needs 3 GB left free; these tests write a few KB and must not depend on how full
    # the machine is. test_unpack_needs_free_space_to_remain restores the bound and mocks the disk.
    monkeypatch.setattr(field, "MIN_FREE_AFTER_UNPACK", 0)


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    session = _session()
    folder = tmp_path / "synthetic-field"
    (folder / "keyframes").mkdir(parents=True)
    (folder / "session.json").write_text(json.dumps(session))
    noise = np.random.default_rng(0).integers(0, 255, (480, 640), np.uint8)
    for kid in CAMERAS:
        cv2.imwrite(str(folder / "keyframes" / f"{kid}.jpg"), noise)
    archive = tmp_path / "session.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for f in sorted(folder.rglob("*")):
            z.write(f, f.relative_to(tmp_path))
    capture = hashlib.sha256(archive.read_bytes()).hexdigest()
    # MoGe-2's output, as the runner writes it, reading 10% long (turns 0: already upright).
    work = tmp_path / "field" / "work" / "synthetic-field"
    (work / "moge2").mkdir(parents=True)
    (work / "turns.json").write_text(json.dumps(dict.fromkeys(CAMERAS, 0)))
    (work / "capture.json").write_text(json.dumps({"capture": capture}))
    for kid in CAMERAS:
        np.savez(
            work / "moge2" / f"{kid}.npz",
            depth=_depth(kid, 1.1).astype(np.float32),
            valid=np.isfinite(_depth(kid, 1.1)),
            intrinsics=np.array([500.0, 500.0, 319.5, 239.5]),
        )

    def measured(mid, meters):
        return {
            "id": mid,
            "candidate": None,
            "from": "a",
            "to": "b",
            "status": "measured",
            "value_ft": round(meters / FT, 6),
            "plus_minus_ft": 0.01,
            "method": "tape",
            "measured_by": ["s"],
        }

    truth = {
        "format": 1,
        "unit": "ft",
        "house": "synthetic",
        "captures": [capture],
        "scale_reference": "scale",
        "candidates": [],
        "measurements": [
            measured("scale", 2.5),
            measured("wall-length", 10.0),
            measured("meter-height", 1.5),
            measured("facing", 3.0),
            {
                "id": "pool",
                "candidate": None,
                "from": "a",
                "to": "b",
                "status": "absent",
                "method": "look",
                "measured_by": ["s"],
            },
        ],
        "checks": [],
    }
    mapping = {
        "format": 1,
        "unit": "ft",
        "pipeline": "measure-lab",
        "session": "synthetic-field",
        "plus_minus_ft_by_key": {"straight": 0.3},
        "measurements": {
            "scale": {"session_measurement": "m4", "key": "straight"},
            "wall-length": {"session_measurement": "m1", "key": "alongWall"},
            "meter-height": {"session_measurement": "m2", "key": "heightAboveGround"},
            "facing": {"session_measurement": "m3", "key": "gapToWall"},
            "pool": "absent",
        },
    }

    paths = {}
    for name, doc in (("truth", truth), ("map", mapping), ("rules", {"format": 1})):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(doc))
    return archive, paths, capture, tmp_path / "results"


def _values(doc):
    return {m["id"]: m["value_ft"] for m in doc["measurements"]}


def test_rows_recompute_the_rigs_measurements_from_depth(case):
    archive, paths, capture, out = case
    report = field.score(archive, paths["truth"], paths["map"], paths["rules"], out)
    native = json.loads((out / "moge2.json").read_text())
    tape = json.loads((out / "moge2-tape.json").read_text())
    tri = json.loads((out / "moge2-triangulated.json").read_text())
    # Depth 10% long, each keyframe scaling about its own camera: the first contact (from k00001 at
    # x = 3) lands at x = 3 - 1.1 * 3 = -0.3, the second (from k00002 at x = 6) at 6 + 1.1 * 4 =
    # 10.4, so the wall reads 10.7 m, not 11.0. The meter and the fence keep a 10% error against
    # a wall that moved with them: heights and gaps scale about the camera the same way.
    assert _values(native)["wall-length"] == pytest.approx(10.7 / FT, abs=1e-5)
    # The taped 2.5 m reference joins taps on different keyframes; solving along the rays gives
    # depth x 1/1.1, which puts every point back (to float32 depth storage, about 0.01 mm); a
    # length ratio would leave the wall about 1 in off.
    for k, meters in {"wall-length": 10.0, "meter-height": 1.5, "facing": 3.0}.items():
        assert _values(tape)[k] == pytest.approx(meters / FT, abs=1e-4)
    # Each keyframe's only neighbour shares no texture with it, so triangulation fits nothing.
    assert all(m.get("missing") == "failed" for m in tri["measurements"] if m["id"] != "pool")
    # Format: capture id, no scale reference row, absent kept, no uncertainty, no decisions.
    assert native["capture"] == capture and native["outcomes"] is None
    assert "scale" not in _values(native)
    assert {"id": "pool", "value_ft": None, "missing": "absent"} in native["measurements"]
    assert all(m.get("plus_minus_ft") is None for m in native["measurements"])
    assert native["timing"]["capture_s"] == 60.0
    assert native["rules_sha256"] == hashlib.sha256(paths["rules"].read_bytes()).hexdigest()
    # AR scale: the rig read 2% short on the only accepted straight span of 29 ft or more, the
    # 32.8 ft wall. Its bound: 1.96 * hypot(2 in, 0.01 ft) / 32.81 ft = 1.00%, so 2% is not decided.
    assert "AR scale error: -2.00% ± 1.00% (95%, 1 spans, longest 32.8 ft" in report
    assert "Within 2%: cannot tell." in report


def test_scale_report_skips_measurements_the_rig_did_not_accept(case):
    archive, paths, _, _ = case
    session = field.load_session(archive.parent / "synthetic-field")
    session["measurements"][0]["accepted"] = False  # the wall span
    lines = field.ar_scale_report(
        session, json.loads(paths["truth"].read_text()), json.loads(paths["map"].read_text()), 2.0
    )
    assert any("no: the rig did not accept it" in x for x in lines)
    assert lines[-1].startswith("AR scale error: not resolved")


def test_fit_scale_weights_long_spans():
    # Spans of 30 ft and 10 ft, each with 1 in of error: 30 ft read 1% short, 10 ft 10% short.
    # Weighted by t^2: scale = (29.7*30 + 9*10) / (900 + 100) = 0.981.
    est = field.fit_scale(np.array([29.7, 9.0]), np.array([30.0, 10.0]), np.full(2, 1 / 12))
    assert est.scale == pytest.approx(0.981)
    assert est.bound == pytest.approx(1.96 / np.sqrt(1000 * 144))
    assert field.within(1.0, 0.5, 2.0) == "yes"
    assert field.within(3.0, 0.5, 2.0) == "no"
    assert field.within(1.8, 0.5, 2.0) == "cannot tell"


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_rotated_K_follows_np_rot90(turns):
    K = np.array([[500.0, 0, 300.0], [0, 510.0, 200.0], [0, 0, 1]])
    w, h = 640, 480
    img = np.zeros((h, w))
    img[150, 400] = 1  # pixel (u, v) = (400, 150)
    rot = np.rot90(img, turns)
    v2, u2 = np.argwhere(rot == 1)[0]
    # The same ray in the turned camera: turning the image turns the camera about its axis.
    x, y = (400 - 300) / 500, (150 - 200) / 510
    x2, y2 = {0: (x, y), 1: (y, -x), 2: (-x, -y), 3: (-y, x)}[turns]
    Kr = rotated_K(K, w, h, turns)
    assert Kr[0, 0] * x2 + Kr[0, 2] == pytest.approx(u2)
    assert Kr[1, 1] * y2 + Kr[1, 2] == pytest.approx(v2)


def test_upright_turns_for_a_portrait_phone():
    # ARKit identity pose: the landscape image is already upright (0 turns). Rolled a quarter turn
    # so world up points to image right, one counter-clockwise turn (1) puts it on top; rolled the
    # other way, as a phone held upright in portrait is, one clockwise turn (3) does.
    T = np.eye(4)
    T[:3, :3] = np.diag([1.0, -1.0, -1.0])  # OpenCV axes of the ARKit identity pose
    assert upright_turns(T) == 0
    roll = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])  # camera +x now points world up
    T[:3, :3] = roll @ np.diag([1.0, -1.0, -1.0])
    assert upright_turns(T) == 1
    T[:3, :3] = roll.T @ np.diag([1.0, -1.0, -1.0])
    assert upright_turns(T) == 3


@pytest.mark.parametrize(
    ("text", "feet"),
    [("30 0 1/8", 30.010417), ("4 11", 4.916667), ("12", 12.0), ("0", 0.0), ("2 3/4", 2.0625)],
)
def test_parse_tape_reads_feet_inches_fraction(text, feet):
    # "2 3/4" is 2 ft and 3/4 in.
    assert field.parse_tape(text) == pytest.approx(feet, abs=1e-6)


@pytest.mark.parametrize("text", ["30 12", "FILL ft in", "", "3 4 1/2 x"])
def test_parse_tape_refuses_what_it_cannot_read(text):
    with pytest.raises(ValueError):
        field.parse_tape(text)


def test_stamp_fills_in_capture_session_and_feet(case, tmp_path):
    archive, paths, capture, out = case
    survey = json.loads(paths["truth"].read_text())
    survey["captures"] = []
    survey["measurements"][1]["value_ft"] = "32 9 3/4"
    paths["truth"].write_text(json.dumps(survey))
    mapping = json.loads(paths["map"].read_text())
    mapping["session"] = field.MAP_SESSION_PLACEHOLDER
    paths["map"].write_text(json.dumps(mapping))
    stamped = field.stamp(archive, paths["truth"], paths["map"], out)
    survey = json.loads(paths["truth"].read_text())
    assert survey["captures"] == [capture]
    assert survey["measurements"][1]["value_ft"] == pytest.approx(32 + 9.75 / 12, abs=1e-6)
    assert json.loads(stamped.read_text())["session"] == "synthetic-field"
    # Idempotent: a second stamp adds nothing.
    field.stamp(archive, paths["truth"], paths["map"], out)
    assert json.loads(paths["truth"].read_text())["captures"] == [capture]


def test_stamp_refuses_a_map_bound_to_another_session_before_writing(case):
    archive, paths, _, out = case
    survey_before = paths["truth"].read_text()
    mapping = json.loads(paths["map"].read_text())
    mapping["session"] = "some-other-session"
    paths["map"].write_text(json.dumps(mapping))
    with pytest.raises(ValueError, match="is the map for session 'some-other-session'"):
        field.stamp(archive, paths["truth"], paths["map"], out)
    assert paths["truth"].read_text() == survey_before
    assert not (out / "inputs" / "map.json").exists()


@pytest.mark.parametrize("text", ["30 2 5", "30 1/4 2", "1 1/0", "1/2", "30 -2"])
def test_parse_tape_refuses_partial_or_misordered_readings(text):
    # "30 2 5" would drop the 5; "30 1/4 2" would read the fraction as inches then drop it.
    with pytest.raises(ValueError):
        field.parse_tape(text)


@pytest.mark.parametrize("bad", ["../escape", "/abs", "..", "a/b", "", "sp ace"])
def test_safe_id_refuses_path_components(bad):
    with pytest.raises(ValueError):
        field.safe_id(bad, "session id")
    assert field.safe_id("synthetic-field_1.2", "session id") == "synthetic-field_1.2"


def test_session_file_stays_inside_the_session(tmp_path):
    (tmp_path / "keyframes").mkdir()
    (tmp_path / "keyframes" / "k.jpg").write_bytes(b"x")
    assert (
        field.session_file(tmp_path, "keyframes/k.jpg")
        == (tmp_path / "keyframes" / "k.jpg").resolve()
    )
    for bad in ["/etc/hosts", "../outside.jpg", "keyframes/../../outside.jpg"]:
        with pytest.raises(ValueError):
            field.session_file(tmp_path, bad)


def test_score_refuses_predictions_prepared_for_another_capture(case):
    archive, paths, _, out = case
    stamp = field.FIELD_DIR / "work" / "synthetic-field" / "capture.json"
    stamp.write_text(json.dumps({"capture": "0" * 64}))  # same session id, different archive
    with pytest.raises(ValueError, match="prepared for another capture"):
        field.score(archive, paths["truth"], paths["map"], paths["rules"], out)


def test_score_refuses_a_map_for_another_session(case):
    archive, paths, _, out = case
    mapping = json.loads(paths["map"].read_text())
    mapping["session"] = "some-other-session"
    paths["map"].write_text(json.dumps(mapping))
    with pytest.raises(ValueError, match="map is for session"):
        field.score(archive, paths["truth"], paths["map"], paths["rules"], out)


def test_a_failed_tape_reference_fails_only_the_tape_row(case, monkeypatch):
    archive, paths, _, out = case

    def no_scale(*args, **kwargs):
        raise ValueError("no model depth at a reference tap")

    monkeypatch.setattr(field, "tape_scale", no_scale)
    report = field.score(archive, paths["truth"], paths["map"], paths["rules"], out)
    assert "every value failed" in report
    tape_row = json.loads((out / "moge2-tape.json").read_text())
    assert all(m["value_ft"] is None for m in tape_row["measurements"])
    native = json.loads((out / "moge2.json").read_text())
    assert any(m["value_ft"] is not None for m in native["measurements"])


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members:
            z.writestr(name, data)
    return path


SESSION_JSON = json.dumps({"format": "measure-lab-session", "formatVersion": 2})


@pytest.mark.parametrize(
    "name",
    ["../session.json", "/session.json", "a/../../session.json", "C:/session.json", "a\\b.jpg"],
)
def test_unpack_refuses_member_paths_that_leave_the_folder(tmp_path, monkeypatch, name):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    archive = _zip(tmp_path / "s.zip", [("s/session.json", SESSION_JSON), (name, "x")])
    with pytest.raises(ValueError, match="not a plain relative path"):
        field.unpack(archive)
    assert not (tmp_path / "session.json").exists()
    assert not (tmp_path / "field").exists()  # refused before anything was written


def test_unpack_refuses_duplicates_and_too_many_or_too_large_members(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    dup = tmp_path / "dup.zip"
    # "s/./a.jpg" names the same file as "s/a.jpg" once normalised.
    _zip(dup, [("s/session.json", SESSION_JSON), ("s/a.jpg", "1"), ("s/./a.jpg", "2")])
    with pytest.raises(ValueError, match="appears twice"):
        field.unpack(dup)
    monkeypatch.setattr(field, "MAX_ZIP_MEMBERS", 2)
    many = _zip(
        tmp_path / "many.zip", [("s/session.json", SESSION_JSON), ("s/a", "1"), ("s/b", "2")]
    )
    with pytest.raises(ValueError, match="more than 2"):
        field.unpack(many)
    monkeypatch.setattr(field, "MAX_ZIP_MEMBERS", 10)
    monkeypatch.setattr(field, "MAX_ZIP_BYTES", 100)
    big = _zip(tmp_path / "big.zip", [("s/session.json", SESSION_JSON), ("s/a", "0" * 200)])
    with pytest.raises(ValueError, match="more than 100"):
        field.unpack(big)


def test_unpack_counts_bytes_even_when_a_header_understates_them(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    monkeypatch.setattr(field, "MAX_ZIP_BYTES", 100)
    archive = _zip(tmp_path / "s.zip", [("s/session.json", SESSION_JSON), ("s/a", "0" * 200)])
    real = zipfile.ZipFile.infolist

    def understated(self):
        infos = real(self)
        for info in infos:
            info.file_size = 1  # as a crafted header would claim
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", understated)
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        field.unpack(archive)
    assert not list((tmp_path / "field").glob("*/s/a"))  # nothing left half-written


def test_unpack_needs_free_space_to_remain(tmp_path, monkeypatch):
    from collections import namedtuple

    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    monkeypatch.setattr(field, "MIN_FREE_AFTER_UNPACK", 3 * 1024**3)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(field.shutil, "disk_usage", lambda p: Usage(0, 0, 1024**3))
    archive = _zip(tmp_path / "s.zip", [("s/session.json", SESSION_JSON)])
    with pytest.raises(ValueError, match="less than 3 GB free"):
        field.unpack(archive)


def test_unpack_extracts_a_plain_session(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    archive = _zip(tmp_path / "s.zip", [("s/session.json", SESSION_JSON), ("s/k/a.jpg", "x")])
    folder, digest = field.unpack(archive)
    assert folder.is_relative_to((tmp_path / "field").resolve())
    assert (folder / "k" / "a.jpg").read_text() == "x"
    assert field.unpack(archive) == (folder, digest)  # a second call reuses the folder


def test_unpack_refuses_an_oversized_archive_before_reading_it(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    monkeypatch.setattr(field, "MAX_ZIP_ARCHIVE_BYTES", 100)
    archive = _zip(tmp_path / "s.zip", [("s/session.json", SESSION_JSON), ("s/a", "0" * 500)])

    def no_read(*args, **kwargs):
        raise AssertionError("an oversized archive was hashed or opened")

    monkeypatch.setattr(field, "file_sha256", no_read)
    monkeypatch.setattr(field.zipfile, "ZipFile", no_read)
    with pytest.raises(ValueError, match="more than 100"):
        field.unpack(archive)


def test_unpack_counts_every_zip_entry_including_skipped_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    monkeypatch.setattr(field, "MAX_ZIP_ENTRIES", 3)
    entries = [("s/session.json", SESSION_JSON)] + [(f"__MACOSX/{i}", "") for i in range(3)]
    archive = _zip(tmp_path / "s.zip", entries)
    with pytest.raises(ValueError, match="4 zip entries, more than 3"):
        field.unpack(archive)


def test_file_sha256_streams_and_matches_hashlib(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"x" * (3 << 20) + b"tail")  # spans several 1 MB reads
    assert field.file_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_zip_directory_limits_apply_before_zipfile_reads_the_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    entries = [("s/session.json", SESSION_JSON)] + [(f"s/{i:03d}", "") for i in range(20)]
    archive = _zip(tmp_path / "s.zip", entries)

    def no_zipfile(*args, **kwargs):
        raise AssertionError("ZipFile read the directory before the limits were checked")

    monkeypatch.setattr(field.zipfile, "ZipFile", no_zipfile)
    monkeypatch.setattr(field, "MAX_ZIP_ENTRIES", 10)
    with pytest.raises(ValueError, match="21 zip entries, more than 10"):
        field.unpack(archive)
    monkeypatch.setattr(field, "MAX_ZIP_ENTRIES", 100)
    monkeypatch.setattr(field, "MAX_ZIP_DIRECTORY_BYTES", 200)
    with pytest.raises(ValueError, match="zip directory, more than 200"):
        field.unpack(archive)


def test_zip_directory_limits_read_zip64_end_records(tmp_path, monkeypatch):
    archive = tmp_path / "z64.zip"
    # Writing more entries than this limit makes zipfile add a ZIP64 end record, as a very large
    # session would, without writing 65,536 files.
    monkeypatch.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
    _zip(archive, [("s/session.json", SESSION_JSON), ("s/a", "x"), ("s/b", "y")])
    with archive.open("rb") as f:
        assert zipfile._EndRecData(f)[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64
    field.check_zip_directory(archive)  # a well-formed ZIP64 archive passes
    monkeypatch.setattr(field, "MAX_ZIP_ENTRIES", 2)
    with pytest.raises(ValueError, match="3 zip entries, more than 2"):
        field.check_zip_directory(archive)


def test_a_file_that_is_not_a_zip_is_refused_loudly(tmp_path):
    path = tmp_path / "s.zip"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(ValueError, match="not a zip file"):
        field.check_zip_directory(path)


def test_a_reported_multi_gigabyte_archive_is_refused_from_its_size_alone(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "field")
    archive = _zip(tmp_path / "s.zip", [("s/session.json", SESSION_JSON)])
    real_stat = Path.stat

    def huge(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        if self == archive:
            fields = list(st)
            fields[6] = 5 * 1024**3  # st_size: a 5 GiB file, without writing one
            return os.stat_result(fields)
        return st

    def no_read(*args, **kwargs):
        raise AssertionError("the archive was read or opened")

    monkeypatch.setattr(Path, "stat", huge)
    monkeypatch.setattr(field, "check_zip_directory", no_read)
    monkeypatch.setattr(field, "file_sha256", no_read)
    monkeypatch.setattr(field.zipfile, "ZipFile", no_read)
    with pytest.raises(ValueError, match="more than 2147483648"):
        field.unpack(archive)


def _jpeg_claiming(width: int, height: int) -> bytes:
    """A few dozen bytes: SOI, an APP0 segment to skip, then a baseline frame header declaring
    `width` x `height`, with no pixel data behind it."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + bytes(9)
    sof0 = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03"
        + bytes(9)
    )
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def test_image_size_reads_real_and_crafted_headers(tmp_path):
    real = tmp_path / "real.jpg"
    cv2.imwrite(str(real), np.zeros((48, 64, 3), np.uint8))
    assert field.image_size(real) == (64, 48)
    png = tmp_path / "real.png"
    cv2.imwrite(str(png), np.zeros((5, 7), np.uint8))
    assert field.image_size(png) == (7, 5)
    crafted = tmp_path / "bomb.jpg"
    crafted.write_bytes(_jpeg_claiming(65535, 65535))
    assert field.image_size(crafted) == (65535, 65535)


def test_an_image_whose_header_claims_too_many_pixels_is_never_decoded(tmp_path, monkeypatch):
    bomb = tmp_path / "bomb.jpg"
    bomb.write_bytes(_jpeg_claiming(65535, 65535))  # 4.3 gigapixels claimed in 60 bytes

    def no_decode(*args, **kwargs):
        raise AssertionError("an oversized image was decoded")

    monkeypatch.setattr(field.cv2, "imread", no_decode)
    with pytest.raises(ValueError, match="more than 50000000"):
        field.read_session_image(bomb)
    other = tmp_path / "x.gif"
    other.write_bytes(b"GIF89a" + bytes(20))
    with pytest.raises(ValueError, match="not a JPEG or PNG"):
        field.read_session_image(other)


def test_a_session_image_within_the_bound_decodes(tmp_path):
    path = tmp_path / "k.jpg"
    cv2.imwrite(str(path), np.full((30, 40, 3), 200, np.uint8))
    assert field.read_session_image(path).shape == (30, 40, 3)
    assert field.read_session_image(path, cv2.IMREAD_GRAYSCALE).shape == (30, 40)


def test_session_memory_follows_one_group_not_the_session():
    loads, live_peak = [], 0
    frames = field.GroupFrames(lambda k: loads.append(k) or (k,), lambda k: 1_000_000)
    groups = [[f"k{i}", f"k{i + 1}", f"k{i + 2}"] for i in range(50)]  # a 52-keyframe walk
    for group in groups:
        held = frames.hold(group)
        assert set(held) == set(group)
        live_peak = max(live_peak, len(frames.held))
    assert live_peak == 3  # never more than one group, however long the session
    assert len(loads) == 52  # overlapping frames are kept, not decoded again


def test_a_group_over_the_pixel_budget_is_refused_before_decoding(monkeypatch):
    def no_decode(k):
        raise AssertionError("a frame was decoded")

    # Eight 48 MP photos, sized from their headers only: 390 MP against a 200 MP budget.
    frames = field.GroupFrames(no_decode, lambda k: 8064 * 6048)
    with pytest.raises(ValueError, match="more than 200000000"):
        frames.hold([f"k{i}" for i in range(8)])
    small = field.GroupFrames(lambda k: (k,), lambda k: 1920 * 1440)  # eight ARKit keyframes
    assert len(small.hold([f"k{i}" for i in range(8)])) == 8


def test_stamp_refuses_a_survey_already_bound_to_another_capture(case):
    archive, paths, capture, out = case
    survey = json.loads(paths["truth"].read_text())
    survey["captures"] = ["0" * 64]  # the survey from another walk
    survey["measurements"][1]["value_ft"] = "32 9 3/4"
    paths["truth"].write_text(json.dumps(survey))
    before = paths["truth"].read_text()
    with pytest.raises(ValueError, match=f"already the survey for capture {'0' * 64}"):
        field.stamp(archive, paths["truth"], paths["map"], out)
    assert paths["truth"].read_text() == before  # nothing converted or appended
    assert not (out / "inputs" / "map.json").exists()
    survey["captures"] = [capture]  # its own capture, stamped before: accepted again
    paths["truth"].write_text(json.dumps(survey))
    field.stamp(archive, paths["truth"], paths["map"], out)


def test_cached_extraction_skips_zip_decompression(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "cache")
    archive = tmp_path / "session.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("s/session.json", SESSION_JSON)
        z.writestr("s/keyframe.jpg", b"photo")
    folder, digest = field.unpack(archive)

    def no_decompression(*args, **kwargs):
        pytest.fail("cached field stages must not open ZIP members")

    monkeypatch.setattr(zipfile.ZipFile, "open", no_decompression)
    for _ in range(3):
        assert field.unpack(archive) == (folder, digest)

    manifest = json.loads((field.FIELD_DIR / f"{digest[:16]}.members.json").read_text())
    assert manifest == {
        "archive_sha256": digest,
        "files": {
            "s/session.json": hashlib.sha256(SESSION_JSON.encode()).hexdigest(),
            "s/keyframe.jpg": hashlib.sha256(b"photo").hexdigest(),
        },
    }


@pytest.mark.parametrize("invalid", [None, "{", "[]", "wrong-archive", "missing-member"])
def test_cached_extraction_rebuilds_without_valid_manifest(tmp_path, monkeypatch, invalid):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "cache")
    archive = tmp_path / "session.zip"
    _zip(archive, [("session.json", SESSION_JSON), ("keyframe.jpg", b"photo")])
    folder, digest = field.unpack(archive)
    reference = field.FIELD_DIR / f"{digest[:16]}.members.json"
    expected = json.loads(reference.read_text())
    if invalid is None:
        reference.unlink()
    elif invalid in ("{", "[]"):
        reference.write_text(invalid)
    else:
        broken = json.loads(reference.read_text())
        if invalid == "wrong-archive":
            broken["archive_sha256"] = "0" * 64
        else:
            del broken["files"]["keyframe.jpg"]
        reference.write_text(json.dumps(broken))
    opened = []
    original_open = zipfile.ZipFile.open

    def record_open(self, member, *args, **kwargs):
        opened.append(member.filename)
        return original_open(self, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", record_open)
    assert field.unpack(archive) == (folder, digest)
    assert opened == ["session.json", "keyframe.jpg"]
    assert json.loads(reference.read_text()) == expected


def test_cached_extraction_recreates_modified_files(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "cache")
    archive = tmp_path / "session.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("session.json", "original")
        z.writestr("keyframe.jpg", b"photo")
    folder, _ = field.unpack(archive)
    (folder / "session.json").write_text("modified")
    (folder / "keyframe.jpg").write_bytes(b"other")
    again, _ = field.unpack(archive)
    assert (again / "session.json").read_text() == "original"
    assert (again / "keyframe.jpg").read_bytes() == b"photo"


def test_score_refuses_a_survey_with_another_capture(case):
    archive, paths, _capture, out = case
    truth = json.loads(paths["truth"].read_text())
    truth["captures"].append("another capture")
    paths["truth"].write_text(json.dumps(truth))
    with pytest.raises(ValueError, match="survey captures must contain only"):
        field.score(archive, paths["truth"], paths["map"], paths["rules"], out)


def test_image_decoder_keeps_sensor_orientation(tmp_path):
    import struct

    path = tmp_path / "oriented.jpg"
    assert cv2.imwrite(str(path), np.zeros((8, 12, 3), dtype=np.uint8))
    # A little-endian TIFF directory with one SHORT orientation tag, rotate 90 degrees.
    exif = b"Exif\0\0" + struct.pack("<2sHIH", b"II", 42, 8, 1)
    exif += struct.pack("<HHIHHI", 274, 3, 1, 6, 0, 0)
    jpeg = path.read_bytes()
    path.write_bytes(jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:])
    assert cv2.imread(str(path)).shape[:2] == (12, 8)
    assert field.read_session_image(path).shape[:2] == (8, 12)


def test_jpeg_header_rejects_a_frame_beyond_the_scan_budget(tmp_path):
    path = tmp_path / "invalid.jpg"
    # Valid APP segments lead to a valid frame beyond the budget; an unbounded scan accepts it.
    segment = b"\xff\xe0\xff\xff" + b"a" * 65533
    path.write_bytes(b"\xff\xd8" + segment * 17 + b"\xff\xc0\x00\x07\x08\x00\x08\x00\x0c")
    with pytest.raises(ValueError, match="frame header"):
        field.image_size(path)


def test_jpeg_frame_header_cannot_cross_scan_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "MAX_JPEG_HEADER_BYTES", 32)
    path = tmp_path / "crosses-budget.jpg"
    path.write_bytes(
        b"\xff\xd8\xff\xe0\x00\x1a" + b"a" * 24 + b"\xff\xc0\x00\x07\x08\x00\x08\x00\x0c"
    )
    with pytest.raises(ValueError, match="frame header"):
        field.image_size(path)


@pytest.mark.parametrize("input_name", ["session", "truth", "map", "rules"])
@pytest.mark.parametrize("symlink", [False, True])
def test_score_preserves_inputs_inside_cleanup_directory(case, input_name, symlink):
    archive, paths, _capture, out = case
    scored = out / "scored"
    scored.mkdir(parents=True)
    inputs = {"session": archive, **paths}
    original = inputs[input_name].read_bytes()
    inside = scored / inputs[input_name].name
    inside.write_bytes(original)
    if symlink:
        alias = out.parent / f"{input_name}-alias"
        alias.symlink_to(inside)
        inputs[input_name] = alias
    else:
        inputs[input_name] = inside
    report = out / "field_report.md"
    report.write_text("previous report")
    with pytest.raises(ValueError, match="overlaps scoring output cleanup"):
        field.score(inputs["session"], inputs["truth"], inputs["map"], inputs["rules"], out)
    assert inside.read_bytes() == original
    assert report.read_text() == "previous report"


def test_score_clears_old_reports_before_a_failure(case):
    archive, paths, _capture, out = case
    out.mkdir()
    (out / "field_report.md").write_text("stale report")
    (out / "scored").mkdir()
    (out / "scored" / "scores.json").write_text("stale scores")
    truth = json.loads(paths["truth"].read_text())
    truth["captures"] = ["wrong capture"]
    paths["truth"].write_text(json.dumps(truth))
    with pytest.raises(ValueError):
        field.score(archive, paths["truth"], paths["map"], paths["rules"], out)
    assert not (out / "field_report.md").exists()
    assert (out / "scored").is_dir()
    assert not list((out / "scored").iterdir())


@pytest.mark.parametrize("layout", ["inside", "equal", "ancestor", "alias", "cleanup-alias"])
def test_score_preserves_directory_sessions_overlapping_outputs(case, layout):
    archive, paths, _capture, _out = case
    folder, _ = field.unpack(archive)
    out = folder / "results"
    if layout == "equal":
        out = folder
    elif layout == "ancestor":
        out = folder.parent
    elif layout in ("alias", "cleanup-alias"):
        out = folder.parent / "output-alias"
        if layout == "alias":
            (folder / "results").mkdir()
            out.symlink_to(folder / "results", target_is_directory=True)
    out.mkdir(exist_ok=True)
    scored = out / "scored"
    if layout == "cleanup-alias":
        scored.symlink_to(folder, target_is_directory=True)
    else:
        scored.mkdir()
        doc = json.loads((folder / "session.json").read_text())
        (scored / "session-input.jpg").write_bytes(
            field.session_file(folder, doc["keyframes"][0]["img"]).read_bytes()
        )
        if scored.resolve().is_relative_to(folder):
            doc["keyframes"][0]["img"] = str(
                (scored / "session-input.jpg").resolve().relative_to(folder)
            )
            (folder / "session.json").write_text(json.dumps(doc))
    report = out / "field_report.md"
    report.write_text("previous report")
    before = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match=r"directory input .* overlaps scoring output cleanup"):
        field.score(folder, paths["truth"], paths["map"], paths["rules"], out)
    assert {
        str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    } == before
    assert report.read_text() == "previous report"


def test_prepare_without_output_directory_skips_result_lookup(tmp_path, monkeypatch, capsys):
    import sys

    archive = tmp_path / "session.zip"
    folder, prepared = tmp_path / "unpacked", tmp_path / "prepared"
    calls = []

    def unpack(session):
        assert session == archive
        calls.append("unpack")
        return folder, "capture-hash"

    def prepare(session_folder, digest):
        assert (session_folder, digest) == (folder, "capture-hash")
        calls.append("prepare")
        return prepared

    def unused_lookup(*args):
        pytest.fail("prepare must not look up a results directory")

    monkeypatch.setattr(sys, "argv", ["field", "prepare", str(archive)])
    monkeypatch.setattr(field, "unpack", unpack)
    monkeypatch.setattr(field, "prepare", prepare)
    monkeypatch.setattr(field, "result_dir", unused_lookup)
    field.main()
    assert calls == ["unpack", "prepare"]
    assert capsys.readouterr().out == f"{prepared}\n"


def test_equal_archive_basenames_get_distinct_result_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(field, "FIELD_DIR", tmp_path / "cache")
    archives = []
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        archive = folder / "session.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("session.json", name)
        archives.append(archive)
    assert field.result_dir(archives[0]) != field.result_dir(archives[1])


@pytest.mark.parametrize("dangling", [False, True])
def test_score_replaces_scored_symlink_without_touching_its_target(case, dangling):
    archive, paths, _capture, out = case
    unrelated = out.parent / "unrelated-scores"
    if not dangling:
        unrelated.mkdir()
        (unrelated / "keep.json").write_bytes(b"caller-owned scores")
    out.mkdir()
    scored = out / "scored"
    scored.symlink_to(unrelated, target_is_directory=True)
    field.score(archive, paths["truth"], paths["map"], paths["rules"], out)
    assert scored.is_dir()
    assert not scored.is_symlink()
    (scored / "new.json").write_bytes(b"new scores")
    if dangling:
        assert not unrelated.exists()
    else:
        assert (unrelated / "keep.json").read_bytes() == b"caller-owned scores"
        assert {path.name for path in unrelated.iterdir()} == {"keep.json"}
