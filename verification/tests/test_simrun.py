import contextlib
import datetime as dt
import io
import json
import queue
import subprocess
import sys
from pathlib import Path

import pytest

from hsverify import gitref, simrun
from hsverify.simrun import (
    ShotRecord,
    app_export,
    compiling,
    copy_app_export,
    missing_required,
    pump_log,
    seconds_since,
    slug,
    wait_for_log_stream,
)
from hsverify.statelog import SUBSYSTEM

BANNER = 'Filtering the log data using "subsystem == "dev.housescanning.housescan""\n'


def log_line(message: str, category: str = "state") -> str:
    return (
        json.dumps(
            {
                "timestamp": "2026-09-26 02:50:00.123456-0500",
                "subsystem": SUBSYSTEM,
                "category": category,
                "eventMessage": message,
            }
        )
        + "\n"
    )


def test_state_time_uses_the_log_clock():
    stamp = "2026-09-26 02:47:25.977214-0500"
    logged = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f%z").timestamp()
    assert seconds_since(logged - 2.5, stamp, fallback=9.0) == 2.5


def test_unparseable_timestamp_falls_back_to_arrival_time():
    assert seconds_since(0.0, "", fallback=1.234) == 1.23


def test_slug_is_filename_safe():
    assert slug("origin/t3/ios-mvf") == "origin-t3-ios-mvf"
    assert slug("wall walk: gap!") == "wall-walk-gap"


def test_only_compiling_xcodebuilds_block_a_build():
    assert compiling("/Applications/Xcode.app/.../xcodebuild -project a.xcodeproj build")
    assert compiling("xcodebuild test -project a.xcodeproj -scheme A")
    assert not compiling("xcodebuild -project a.xcodeproj test-without-building")


# --- log stream readiness --------------------------------------------------------------------


def drain(events: queue.Queue) -> list:
    items = []
    while not events.empty():
        items.append(events.get_nowait())
    return items


def test_ready_is_signalled_by_the_first_line_then_states_follow():
    events: queue.Queue = queue.Queue()
    sink = io.StringIO()
    pump_log(iter([BANNER, log_line("STATE=onboarding")]), events, sink)
    kinds = [(kind, getattr(payload, "name", payload)) for kind, payload in drain(events)]
    assert kinds == [("ready", BANNER.strip()), ("state", "onboarding")]
    assert sink.getvalue() == BANNER + log_line("STATE=onboarding")


def test_a_silent_log_stream_never_signals_ready_and_times_out():
    events: queue.Queue = queue.Queue()
    pump_log(iter([]), events, io.StringIO())
    assert events.empty()
    assert wait_for_log_stream(events, 0.01) == (
        "log stream printed nothing within 0 s; the app was not launched"
    )


def test_wait_for_log_stream_passes_once_ready():
    events: queue.Queue = queue.Queue()
    pump_log(iter([BANNER]), events, io.StringIO())
    assert wait_for_log_stream(events, 1.0) is None


# --- required states -------------------------------------------------------------------------


def shot(state: str) -> ShotRecord:
    return ShotRecord(1, state, 0.0, "01.png", False, f"STATE={state}")


def test_missing_required_state_is_a_problem():
    states = [shot("onboarding"), shot("uploading")]
    assert missing_required(["result"], states) == ["required state result never appeared"]
    assert missing_required(["result"], [*states, shot("result")]) == []
    assert missing_required([], []) == []


def test_required_states_reject_reversed_complete_journey():
    required = ["onboarding", "capture", "uploading", "result"]
    assert missing_required(required, [shot(name) for name in reversed(required)]) == [
        "required states did not appear in order: onboarding -> capture -> uploading -> result"
    ]


def test_required_states_allow_other_states_and_a_repeated_journey():
    required = ["capture", "uploading", "result"]
    states = ["result", "capture", "capture", "review", "uploading", "result"]
    assert missing_required(required, [shot(name) for name in states]) == []


def test_repeated_required_state_needs_a_separate_occurrence():
    assert missing_required(["capture", "capture"], [shot("capture")]) == [
        "required states did not appear in order: capture -> capture"
    ]


# --- app export ------------------------------------------------------------------------------


def write_log(out: Path, *lines: str) -> None:
    (out / "state.ndjson").write_text(BANNER + "".join(lines))


def test_last_logged_bundle_is_copied_as_scan_zip(tmp_path):
    first, last = tmp_path / "first.zip", tmp_path / "last.zip"
    first.write_bytes(b"old")
    last.write_bytes(b"PK new")
    out = tmp_path / "report"
    out.mkdir()
    write_log(
        out,
        log_line(f"bundle {first} with 3 keyframes", category="engine"),
        log_line("STATE=result"),
        log_line(f"bundle {last} with 77 keyframes", category="engine"),
    )
    assert copy_app_export(out) == ("scan.zip", [])
    assert (out / "scan.zip").read_bytes() == b"PK new"


def test_no_bundle_logged_means_no_export_and_no_problem(tmp_path):
    write_log(tmp_path, log_line("STATE=onboarding"))
    assert copy_app_export(tmp_path) == (None, [])
    assert not (tmp_path / "scan.zip").exists()


def test_logged_bundle_that_is_gone_is_a_problem(tmp_path):
    gone = tmp_path / "gone.zip"
    write_log(tmp_path, log_line(f"bundle {gone} with 5 keyframes", category="engine"))
    export, problems = copy_app_export(tmp_path)
    assert export is None
    assert len(problems) == 1
    assert problems[0].startswith(f"the app logged bundle {gone} with 5 keyframes; copy failed")


def test_a_bundle_line_the_runner_cannot_read_is_a_problem(tmp_path):
    write_log(tmp_path, log_line("bundle written to a new place, 5 keyframes", category="engine"))
    assert copy_app_export(tmp_path) == (
        None,
        ["the app logged a bundle line this runner cannot read; see state.ndjson"],
    )


def test_a_run_that_must_keep_the_scan_fails_without_one(tmp_path):
    write_log(tmp_path, log_line("STATE=result"))
    assert app_export(tmp_path, required=False) == (None, [])
    name, problems = app_export(tmp_path, required=True)
    assert name is None and "--require-export" in problems[0]


def test_a_run_installs_its_own_copy_of_the_app_it_built(tmp_path, monkeypatch):
    derived = tmp_path / "derived"
    built = derived / "Build" / "Products" / "Debug-iphonesimulator" / "HouseScan.app"
    monkeypatch.setattr(simrun, "DERIVED_DATA", derived)
    monkeypatch.setattr(simrun, "BUILD_LOCK", tmp_path / "lock")
    monkeypatch.setattr(simrun, "wait_for_other_builds", lambda max_wait_s: 0.0)

    def fake_build(cmd, **kwargs):
        built.mkdir(parents=True, exist_ok=True)
        (built / "Info.plist").write_text("run A")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(simrun.subprocess, "run", fake_build)
    tree, out = tmp_path / "tree", tmp_path / "report"
    (tree / "ios" / "HouseScan.xcodeproj").mkdir(parents=True)
    out.mkdir()
    result, app = simrun.build_app(tree, out, 0, "ios/HouseScan.xcodeproj", "HouseScan")
    assert result["ok"] and app is not None
    (built / "Info.plist").write_text("run B")  # another run rebuilds after the lock is released
    assert (app / "Info.plist").read_text() == "run A"
    simrun.discard_app(app)
    assert not app.parent.exists() and out.exists()


def test_shared_output_keeps_both_app_copies(tmp_path, monkeypatch):
    derived = tmp_path / "derived"
    built = derived / "Build" / "Products" / "Debug-iphonesimulator" / "HouseScan.app"
    monkeypatch.setattr(simrun, "DERIVED_DATA", derived)
    monkeypatch.setattr(simrun, "BUILD_LOCK", tmp_path / "lock")
    monkeypatch.setattr(simrun, "wait_for_other_builds", lambda _: 0.0)

    def fake_build(cmd, **kwargs):
        built.mkdir(parents=True, exist_ok=True)
        (built / "Info.plist").write_text("app")
        kwargs["stdout"].write("warning: Run script build phase\n/path/File: warning: warning\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(simrun.subprocess, "run", fake_build)
    tree, out = tmp_path / "tree", tmp_path / "report"
    (tree / "ios" / "HouseScan.xcodeproj").mkdir(parents=True)
    out.mkdir()
    result, first = simrun.build_app(tree, out, 0, "ios/HouseScan.xcodeproj", "HouseScan")
    _, second = simrun.build_app(tree, out, 0, "ios/HouseScan.xcodeproj", "HouseScan")
    assert first != second and first.exists() and second.exists()
    assert len(result["warnings"]) == 2
    simrun.discard_app(first)
    assert not first.exists() and second.exists()


@pytest.mark.parametrize("failure", ["device", "bundle", "install"])
def test_failed_preparation_discards_app_copy(tmp_path, monkeypatch, failure):
    out = tmp_path / "out"
    app = out / "built-app-unique" / "HouseScan.app"
    app.mkdir(parents=True)
    monkeypatch.setattr(simrun, "DEVICE_LOCK", tmp_path / "device.lock")
    monkeypatch.setattr(gitref, "fetch", lambda: None)
    monkeypatch.setattr(gitref, "resolve", lambda _: "a" * 40)
    monkeypatch.setattr(
        gitref, "detached_worktree", lambda *args, **kwargs: contextlib.nullcontext(tmp_path)
    )
    monkeypatch.setattr(simrun, "static_c4_check", lambda _: {})
    monkeypatch.setattr(simrun, "build_app", lambda *args: ({"ok": True}, app))

    def fail():
        raise RuntimeError("preparation failed")

    monkeypatch.setattr(
        simrun, "ensure_device", lambda _: fail() if failure == "device" else {"udid": "mock"}
    )
    monkeypatch.setattr(simrun, "bundle_id_of", lambda _: fail() if failure == "bundle" else "mock")
    monkeypatch.setattr(simrun, "prepare_display", lambda *args: None)
    monkeypatch.setattr(simrun, "simctl", lambda *args: fail() if args[0] == "install" else "")
    monkeypatch.setattr(
        simrun.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(simrun, "peak_rss_mb", lambda: {})
    monkeypatch.setattr(simrun.report, "write_sim_report", lambda *args: None)
    with pytest.raises(RuntimeError, match="preparation failed"):
        simrun.main(["--out", str(out)])
    assert not app.parent.exists()


def test_device_lock_excludes_another_runner_and_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(simrun, "DEVICE_LOCK", tmp_path / "device.lock")
    script = """import fcntl, sys
with open(sys.argv[1]) as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(43)
"""
    cmd = [sys.executable, "-c", script, str(simrun.DEVICE_LOCK)]
    with simrun.device_lock():
        assert subprocess.run(cmd, timeout=5, check=False).returncode == 43
    assert subprocess.run(cmd, timeout=5, check=False).returncode == 0


@pytest.mark.parametrize("prefix", [[], ["findMeter"], ["findMeter", "uploading"]])
def test_early_result_cannot_be_hidden_by_a_later_complete_flow(prefix):
    order = ["findMeter", "meterCloseUp", "wallWalk", "markFeatures", "uploading", "result"]
    states = [shot(name) for name in [*prefix, "result", *order]]
    assert simrun.out_of_order(order, states)
    assert simrun.out_of_order(order, [shot(name) for name in order]) == []


def test_order_still_allows_intermediate_states_and_completed_recapture():
    order = ["findMeter", "uploading", "result"]
    states = [
        shot(name)
        for name in [
            "onboarding",
            "findMeter",
            "wallWalk",
            "uploading",
            "gapRequest",
            "markFeatures",
            "uploading",
            "result",
        ]
    ]
    assert simrun.out_of_order(order, states) == []
