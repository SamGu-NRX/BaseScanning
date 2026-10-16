"""CLI overrides the brief names for the meter anchor."""

from server.coverage.run import _clicks, parse_meter_frames


class _Cam:
    def __init__(self, cam_id: str):
        self.id = cam_id


def test_meter_frames_keep_pixel_commas():
    rows = parse_meter_frames("k00012:1010,655,k00013:990,640")
    assert rows == [("k00012", 1010.0, 655.0), ("k00013", 990.0, 640.0)]


def test_anchor_from_tap_uses_that_label_when_no_meter_tap():
    packet = {"taps": [{"label": "reference_point", "keyframe": "k0", "pixel": [10, 20]}]}
    cameras = [_Cam("k0")]
    assert _clicks(packet, cameras, None) == []
    assert _clicks(packet, cameras, None, "reference_point") == [(0, 10.0, 20.0)]
