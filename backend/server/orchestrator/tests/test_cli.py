"""CLI: local tar run, dump, and the Modal app wiring."""

from __future__ import annotations

import json
import tarfile

from server.orchestrator.cli import main
from server.orchestrator.modal_functions import modal_available


def test_modal_app_is_wired_and_does_not_spawn() -> None:
    """A missing tar is refused before any Modal call is spawned."""
    assert modal_available() is True
    assert main(["run-modal", "--env", "dev", "--tar", "missing.tar", "--stubs"]) == 2
    assert main(["stop-apps"]) == 2
    assert main(["spend", "--env", "dev"]) == 0


def test_run_local_variant_packet_name(tmp_path) -> None:
    packet = {
        "captureId": "cap_variant",
        "flow": "guided",
        "keyframes": [{"id": "k0"}],
        "stills": [],
        "taps": [{"id": "meter", "element": "meter"}],
    }
    root = tmp_path / "src"
    root.mkdir()
    (root / "packet.arkit-nonlidar.json").write_text(json.dumps(packet))
    tar_path = tmp_path / "variant.tar"
    with tarfile.open(tar_path, "w") as archive:
        archive.add(root / "packet.arkit-nonlidar.json", arcname="site/packet.arkit-nonlidar.json")
    out = tmp_path / "out"
    code = main(["run-local", "--tar", str(tar_path), "--variant", "arkit-nonlidar", "--out", str(out)])
    assert code == 0
    assert json.loads((out / "run.json").read_text())["status"] == "done"


def test_run_local_missing_tar(tmp_path) -> None:
    assert main(["run-local", "--tar", str(tmp_path / "nope.tar"), "--out", str(tmp_path / "out")]) == 2


def test_run_local_synthetic_tar_and_dump(tmp_path, capsys) -> None:
    packet = {
        "captureId": "cap_cli",
        "flow": "guided",
        "keyframes": [{"id": "k0"}],
        "stills": [{"id": "s1", "purpose": "meter_close"}],
        "taps": [{"id": "meter", "element": "meter"}],
        "scaleReference": {"ext": {"doorOnMeterSide": False, "doorViews": []}},
    }
    root = tmp_path / "src"
    root.mkdir()
    (root / "packet.json").write_text(json.dumps(packet))
    tar_path = tmp_path / "mini.tar"
    with tarfile.open(tar_path, "w") as archive:
        archive.add(root / "packet.json", arcname="packet.json")
    out = tmp_path / "out"
    code = main(
        [
            "run-local",
            "--tar",
            str(tar_path),
            "--variant",
            "arkit-nonlidar",
            "--out",
            str(out),
            "--replay-interval",
            "0",
        ]
    )
    assert code == 0
    run = json.loads((out / "run.json").read_text())
    assert run["status"] == "done"
    assert run["outcome_kind"] == "manual_review"
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    finals = [event for event in events if event["type"] in {"verdict_ready", "retake_request", "failed"}]
    assert len(finals) == 1
    assert finals[0]["type"] == "verdict_ready"
    capsys.readouterr()
    assert main(["dump-run", "--out", str(out)]) == 0
    dumped = capsys.readouterr().out
    assert "| validate | done |" in dumped
    assert "merge_objects_s:" in dumped
    assert "| artifact |" not in dumped


def test_run_local_uploads_keyframe_and_still_bytes(tmp_path) -> None:
    jpeg = b"\xff\xd8\xff\xd9"
    still = b"still-bytes"
    packet = {
        "captureId": "cap_media",
        "flow": "guided",
        "keyframes": [{"id": "k0", "img": "keyframes/k0.jpg"}],
        "stills": [{"id": "s1", "img": "stills/s1.jpg", "purpose": "meter_close"}],
        "files": [
            {"path": "keyframes/k0.jpg", "bytes": len(jpeg), "sha256": "0" * 64, "role": "keyframe"},
            {"path": "stills/s1.jpg", "bytes": len(still), "sha256": "0" * 64, "role": "still"},
            {"path": "features/k0.bin", "bytes": 3, "sha256": "0" * 64, "role": "features"},
        ],
        "taps": [{"id": "meter", "element": "meter"}],
    }
    root = tmp_path / "src" / "site"
    (root / "keyframes").mkdir(parents=True)
    (root / "stills").mkdir()
    (root / "features").mkdir()
    (root / "keyframes" / "k0.jpg").write_bytes(jpeg)
    (root / "stills" / "s1.jpg").write_bytes(still)
    (root / "features" / "k0.bin").write_bytes(b"bin")
    (root / "packet.json").write_text(json.dumps(packet))
    tar_path = tmp_path / "media.tar"
    with tarfile.open(tar_path, "w") as archive:
        archive.add(root, arcname="site")
    out = tmp_path / "out"
    assert main(["run-local", "--tar", str(tar_path), "--variant", "arkit", "--out", str(out)]) == 0
    store = out / "store" / "captures" / "cap_media"
    assert (store / "keyframes" / "k0.jpg").read_bytes() == jpeg
    assert (store / "stills" / "s1.jpg").read_bytes() == still
    assert (store / "features" / "k0.bin").read_bytes() == b"bin"
