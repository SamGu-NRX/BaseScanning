"""Build the iOS app from a git ref, run it in a dedicated Simulator, screenshot every STATE.

    make sim-app    # t3/ios-mvf on the ADVIO replay, uploading to a server started from t3/server
    uv run python -m hsverify.simrun --ref <ref> --replay <session> --autopilot --server-ref <ref>

The app is launched with the contract C4 arguments (`-replay <path>`, `-autopilot`,
`-serverURL <url>`) and the runner follows its `STATE=<name>` log markers. Each state gets
a screenshot once it has been on screen for `--settle` seconds; a state replaced sooner is
captured immediately and flagged `transient`, because its image may already show the next
state. The run ends at `--until`, after `--idle` seconds without a new state, or at
`--timeout`. States named with `--require` must appear in the order given. If the app
logged a scan bundle, it is copied into the report as scan.zip (required with `--require-export`)
and named in report.json's
`app_export`, so the report folder can be replayed as an app export. Reports go outside git
(default ~/house-scanning-data/reports/sim/) because a replay can put dataset frames on screen.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import plistlib
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TextIO

from hsverify import gitref, report
from hsverify.e2e import server_from_ref
from hsverify.memory import peak_rss_mb
from hsverify.statelog import (
    LOG_PREDICATE,
    RedactedStateError,
    parse_bundle_line,
    parse_ndjson_line,
)

DEVICE_NAME = "HouseScan Verify"
DEVICE_TYPE = "com.apple.CoreSimulator.SimDeviceType.iPhone-17"
DEFAULT_REPORTS = Path.home() / "house-scanning-data" / "reports" / "sim"
DERIVED_DATA = Path("/tmp/hs-verify-derived-data")
BUILD_LOCK = Path("/tmp/hs-verify-xcodebuild.lock")
DEVICE_LOCK = Path("/tmp/hs-verify-simulator.lock")
BUILT_APP_DIR = "built-app"  # in the report folder, until the Simulator has installed it


@dataclass
class ShotRecord:
    index: int
    state: str
    seconds_after_launch: float
    screenshot: str
    transient: bool
    log_message: str


@dataclass
class RunReport:
    ref: str
    sha: str
    started_at: str
    command: list[str]
    device: dict
    build: dict
    launch_arguments: list[str]
    static_c4: dict
    states: list[ShotRecord] = field(default_factory=list)
    end_reason: str = ""
    problems: list[str] = field(default_factory=list)
    crash_reports: list[str] = field(default_factory=list)
    final_screenshot: str | None = None
    server: dict | None = None
    peak_memory: dict | None = None
    app_export: str | None = None  # the app's scan bundle, copied into the report folder


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def simctl(*args: str) -> str:
    return run(["xcrun", "simctl", *args]).stdout.strip()


# --- Simulator -------------------------------------------------------------------------------


def latest_ios_runtime() -> str:
    runtimes = json.loads(simctl("list", "runtimes", "-j"))["runtimes"]
    ios = [r for r in runtimes if r.get("platform") == "iOS" and r.get("isAvailable")]
    if not ios:
        raise SystemExit("No available iOS Simulator runtime. Install one in Xcode > Settings.")
    ios.sort(key=lambda r: tuple(int(p) for p in r["version"].split(".")))
    return ios[-1]["identifier"]


def ensure_device(udid: str | None = None) -> dict:
    """The runner's own Simulator, so it never disturbs devices other workers are using, or
    the existing device `udid` when one has been handed over (creating a device costs disk)."""
    if udid is not None:
        for runtime, devices in json.loads(simctl("list", "devices", "-j"))["devices"].items():
            for device in devices:
                if device["udid"] == udid:
                    simctl("bootstatus", udid, "-b")
                    return {"name": device["name"], "udid": udid, "runtime": runtime}
        raise SystemExit(f"No Simulator with UDID {udid}; see `xcrun simctl list devices`.")
    runtime = latest_ios_runtime()
    devices = json.loads(simctl("list", "devices", "-j"))["devices"].get(runtime, [])
    for device in devices:
        if device["name"] == DEVICE_NAME and device.get("isAvailable", True):
            udid = device["udid"]
            break
    else:
        udid = simctl("create", DEVICE_NAME, DEVICE_TYPE, runtime)
    simctl("bootstatus", udid, "-b")  # boots the device if it isn't already booted
    return {"name": DEVICE_NAME, "udid": udid, "runtime": runtime, "type": DEVICE_TYPE}


def prepare_display(udid: str, appearance: str, content_size: str | None) -> None:
    # A fixed status bar keeps screenshots comparable between runs.
    simctl(
        "status_bar",
        udid,
        "override",
        "--time",
        "9:41",
        "--batteryState",
        "charged",
        "--batteryLevel",
        "100",
        "--cellularBars",
        "4",
        "--wifiBars",
        "3",
    )
    simctl("ui", udid, "appearance", appearance)
    simctl("ui", udid, "content_size", content_size or "large")


# --- Build -----------------------------------------------------------------------------------


def compiling(args: str) -> bool:
    """Whether an xcodebuild command line compiles. Running prebuilt tests does not."""
    return "xcodebuild" in args and "test-without-building" not in args


def other_builds_running() -> bool:
    listing = subprocess.run(["ps", "-Ao", "args="], capture_output=True, text=True).stdout
    return any(
        compiling(line)
        for line in listing.splitlines()
        if line.split(" ")[0].endswith("xcodebuild")
    )


def wait_for_other_builds(max_wait_s: float) -> float:
    """The Mac is shared: wait while another xcodebuild compiles, up to `max_wait_s`."""
    start = time.monotonic()
    announced = False
    while True:
        if not other_builds_running():
            return time.monotonic() - start
        if time.monotonic() - start > max_wait_s:
            raise SystemExit(f"Another xcodebuild has run for over {max_wait_s:.0f} s; try later.")
        if not announced:
            print("Waiting for another xcodebuild to finish (shared Mac)...", flush=True)
            announced = True
        time.sleep(5)


def build_app(
    tree: Path, out: Path, max_wait_s: float, project_path: str, scheme: str
) -> tuple[dict, Path | None]:
    project = tree / project_path
    if not project.exists():
        return {"ok": False, "error": f"{project.relative_to(tree)} not found"}, None
    log_path = out / "build.log"
    cmd = [
        "xcodebuild",
        "-project",
        str(project),
        "-scheme",
        scheme,
        "-configuration",
        "Debug",
        "-sdk",
        "iphonesimulator",
        "-destination",
        "generic/platform=iOS Simulator",
        "-derivedDataPath",
        str(DERIVED_DATA),
        "ARCHS=arm64",
        "ONLY_ACTIVE_ARCH=YES",
        "CODE_SIGNING_ALLOWED=NO",
        "build",
    ]
    BUILD_LOCK.touch()
    with BUILD_LOCK.open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        waited = wait_for_other_builds(max_wait_s)
        start = time.monotonic()
        with log_path.open("w") as log:
            code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
        seconds = time.monotonic() - start
        # DerivedData is shared: once the lock is released another run may rebuild over this
        # app before it is installed, so this run installs its own copy.
        built = DERIVED_DATA / "Build" / "Products" / "Debug-iphonesimulator" / f"{scheme}.app"
        app = None
        if code == 0 and built.exists():
            app = Path(tempfile.mkdtemp(prefix=f"{BUILT_APP_DIR}-", dir=out)) / built.name
            try:
                shutil.copytree(built, app, symlinks=True)
            except BaseException:
                discard_app(app)
                raise
    text = log_path.read_text(errors="replace")
    warnings = sorted(set(re.findall(r"^(?:.*: )?warning: .*$", text, re.M)))
    errors = sorted(set(re.findall(r"^.*: error: .*$", text, re.M)))
    result = {
        "ok": code == 0,
        "seconds": round(seconds, 1),
        "waited_for_other_builds_s": round(waited, 1),
        "command": shlex.join(cmd),
        "warnings": [shorten(w, tree) for w in warnings],
        "errors": [shorten(e, tree) for e in errors],
        "log": log_path.name,
    }
    return result, app


def discard_app(app: Path) -> None:
    """Remove this run's copy of the app once the Simulator has installed its own."""
    if app.parent.name.startswith(f"{BUILT_APP_DIR}-"):
        shutil.rmtree(app.parent, ignore_errors=True)


def bundle_id_of(app: Path) -> str:
    with (app / "Info.plist").open("rb") as f:
        return plistlib.load(f)["CFBundleIdentifier"]


def shorten(line: str, tree: Path) -> str:
    return line.replace(str(tree) + "/", "")


# --- Static contract check -------------------------------------------------------------------


def static_c4_check(tree: Path) -> dict:
    """Which C4 pieces appear in the app's Swift source. Presence is not proof they work."""
    sources = "\n".join(
        p.read_text(errors="replace") for p in (tree / "ios").rglob("*.swift") if p.is_file()
    )
    return {
        "replay_argument": "-replay" in sources or '"replay"' in sources,
        "autopilot_argument": "-autopilot" in sources or '"autopilot"' in sources,
        "server_url_argument": "-serverURL" in sources or '"serverURL"' in sources,
        "state_subsystem": "dev.housescanning.housescan" in sources,
        "state_marker": "STATE=" in sources,
        "public_privacy": "privacy: .public" in sources,
    }


# --- Run -------------------------------------------------------------------------------------


def log_stream_command(udid: str) -> list[str]:
    return [
        "xcrun",
        "simctl",
        "spawn",
        udid,
        "log",
        "stream",
        "--style",
        "ndjson",
        "--level",
        "debug",
        "--predicate",
        LOG_PREDICATE,
    ]


def pump_log(lines: Iterable[str], events: queue.Queue, sink: TextIO) -> None:
    """Copy `log stream` output to `sink` and queue what the runner acts on.

    The first line is the stream's "Filtering the log data" banner, printed once it is attached;
    it is queued as ("ready", line) so the app is launched only after that, and no early STATE
    is lost. Starting the process is not enough: it prints nothing until attached.
    """
    ready = False
    for line in lines:
        sink.write(line)
        sink.flush()
        if not ready:
            events.put(("ready", line.strip()))
            ready = True
        try:
            event = parse_ndjson_line(line)
        except RedactedStateError as exc:
            events.put(("redacted", str(exc)))
            continue
        if event is not None:
            events.put(("state", event))


def wait_for_log_stream(events: queue.Queue, timeout_s: float) -> str | None:
    """None once the stream's first line arrived; otherwise the problem to report."""
    try:
        kind, _ = events.get(timeout=timeout_s)
    except queue.Empty:
        return f"log stream printed nothing within {timeout_s:.0f} s; the app was not launched"
    if kind != "ready":
        return f"log stream sent {kind!r} before its first line"
    return None


def missing_required(required: list[str], states: list[ShotRecord]) -> list[str]:
    seen = {s.state for s in states}
    missing = [f"required state {name} never appeared" for name in required if name not in seen]
    if missing:
        return missing
    remaining = iter(required)
    next_required = next(remaining, None)
    for state in states:
        if state.state == next_required:
            next_required = next(remaining, None)
    if next_required is not None:
        return [f"required states did not appear in order: {' -> '.join(required)}"]
    return []


def logged_message(line: str) -> str:
    """The message of one ndjson log line, or "" for the banner and anything unreadable."""
    try:
        return json.loads(line).get("eventMessage") or ""
    except (json.JSONDecodeError, AttributeError):
        return ""


def copy_app_export(out: Path) -> tuple[str | None, list[str]]:
    """Copy the scan bundle the app last logged into `out` as scan.zip.

    Returns (file name or None, problems). A run whose app never logged a bundle has no export
    and no problem; a logged bundle that cannot be copied is a problem.
    """
    log = out / "state.ndjson"
    lines = log.read_text().splitlines() if log.exists() else []
    bundles = [b for line in lines if (b := parse_bundle_line(line))]
    if not bundles:
        if any(logged_message(line).startswith("bundle ") for line in lines):
            return None, ["the app logged a bundle line this runner cannot read; see state.ndjson"]
        return None, []
    path, keyframes = bundles[-1]
    try:
        shutil.copyfile(path, out / "scan.zip")
    except OSError as exc:
        return None, [
            f"the app logged bundle {path} with {keyframes} keyframes; copy failed: {exc}"
        ]
    return "scan.zip", []


def app_export(out: Path, required: bool) -> tuple[str | None, list[str]]:
    """copy_app_export, where a run that must keep the app's scan fails without one."""
    name, problems = copy_app_export(out)
    if required and name is None and not problems:
        problems = [
            "The run was required to keep the app's scan (--require-export), but the app logged "
            "no bundle."
        ]
    return name, problems


def app_running(udid: str, bundle_id: str) -> bool:
    out = subprocess.run(
        ["xcrun", "simctl", "spawn", udid, "launchctl", "list"], capture_output=True, text=True
    ).stdout
    return f"UIKitApplication:{bundle_id}" in out


def screenshot(udid: str, path: Path) -> None:
    simctl("io", udid, "screenshot", "--type=png", str(path))


def seconds_since(launch_wall: float, log_timestamp: str, fallback: float) -> float:
    """Seconds from launch to the log entry, by the log's own clock.

    Arrival time at the runner lags behind while a screenshot is being taken, so the log
    timestamp is preferred; the arrival time is used only if the timestamp does not parse.
    """
    try:
        logged = dt.datetime.strptime(log_timestamp, "%Y-%m-%d %H:%M:%S.%f%z").timestamp()
    except ValueError:
        return round(fallback, 2)
    return round(logged - launch_wall, 2)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")[:60]


def follow(args, udid: str, bundle_id: str, out: Path, rep: RunReport) -> None:
    events: queue.Queue = queue.Queue()
    proc = subprocess.Popen(
        log_stream_command(udid), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    assert proc.stdout is not None
    with (out / "state.ndjson").open("w") as sink:
        reader = threading.Thread(target=pump_log, args=(proc.stdout, events, sink), daemon=True)
        reader.start()
        try:
            problem = wait_for_log_stream(events, args.log_ready_timeout)
            if problem:
                rep.end_reason = "log stream did not start"
                rep.problems.append(problem)
                return
            follow_launched(args, udid, bundle_id, out, rep, events)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            reader.join(timeout=10)


def follow_launched(
    args, udid: str, bundle_id: str, out: Path, rep: RunReport, events: queue.Queue
) -> None:
    launch = [
        "xcrun",
        "simctl",
        "launch",
        "--terminate-running-process",
        f"--stdout={out / 'app-stdout.log'}",
        f"--stderr={out / 'app-stderr.log'}",
        udid,
        bundle_id,
        *rep.launch_arguments,
    ]
    run(launch)
    launched = time.monotonic()
    launched_wall = time.time()
    last_event = launched
    pending = None  # (StateEvent, arrival time) waiting for its settled screenshot
    redaction_noted = False
    last_alive_check = launched

    def shoot(event, arrived: float, transient: bool) -> None:
        index = len(rep.states) + 1
        name = f"{index:02d}-{slug(event.name)}.png"
        screenshot(udid, out / name)
        rep.states.append(
            ShotRecord(
                index,
                event.name,
                seconds_since(launched_wall, event.timestamp, arrived - launched),
                name,
                transient,
                event.message,
            )
        )

    while True:
        now = time.monotonic()
        if now - launched > args.timeout:
            rep.end_reason = f"timeout after {args.timeout:.0f} s"
            break
        wait = 0.25
        if pending is not None:
            wait = max(0.0, min(wait, pending[1] + args.settle - now))
        try:
            kind, payload = events.get(timeout=wait)
        except queue.Empty:
            kind, payload = None, None

        if kind == "redacted" and not redaction_noted:
            rep.problems.append(
                "STATE markers are logged as <private>; log the name with privacy: .public "
                f"(saw: {payload!r})"
            )
            redaction_noted = True
        if kind == "state":
            if pending is not None:
                shoot(pending[0], pending[1], transient=True)
            pending = (payload, time.monotonic())
            last_event = pending[1]
            print(f"  STATE={payload.name}", flush=True)
            continue

        now = time.monotonic()
        if pending is not None and now - pending[1] >= args.settle:
            shoot(pending[0], pending[1], transient=False)
            finished = args.until and pending[0].name in args.until
            pending = None
            if finished:
                rep.end_reason = f"reached {rep.states[-1].state}"
                break
        if pending is None and now - last_event > args.idle:
            rep.end_reason = f"no new state for {args.idle:.0f} s"
            break
        if pending is None and now - last_alive_check > 5:
            last_alive_check = now
            if not app_running(udid, bundle_id):
                rep.end_reason = "app is no longer running"
                rep.problems.append("The app exited or crashed during the run.")
                break

    if pending is not None:
        shoot(pending[0], pending[1], transient=False)
    final = out / "final.png"
    screenshot(udid, final)
    rep.final_screenshot = final.name
    if not rep.states:
        rep.problems.append(
            "No STATE markers arrived. Either C4 logging is not implemented on this ref or "
            "the app never reached its first screen; final.png shows what was on screen."
        )


def collect_crashes(since: float, out: Path) -> list[str]:
    found = []
    reports = Path.home() / "Library" / "Logs" / "DiagnosticReports"
    for path in reports.glob("HouseScan*"):
        if path.stat().st_mtime >= since:
            target = out / path.name
            target.write_bytes(path.read_bytes())
            found.append(path.name)
    return found


@contextlib.contextmanager
def device_lock():
    """Hold the shared Simulator until install, capture and shutdown have finished."""
    DEVICE_LOCK.touch()
    with DEVICE_LOCK.open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@device_lock()
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ref", default="origin/main", help="git ref to build")
    parser.add_argument("--project", default="ios/HouseScan.xcodeproj", help="path in the ref")
    parser.add_argument("--scheme", default="HouseScan", help="also the .app name")
    parser.add_argument("--replay", type=Path, help="replay session folder (contract C3)")
    parser.add_argument("--autopilot", action="store_true")
    server = parser.add_mutually_exclusive_group()
    server.add_argument("--server-url", help="a running placement server")
    server.add_argument(
        "--server-ref", help="start the placement server from this ref for the run (S2 API)"
    )
    parser.add_argument("--extra-arg", action="append", default=[], help="more launch args")
    parser.add_argument("--until", action="append", default=[], help="state that ends the run")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="STATE",
        help="a state the run must reach; repeat in the required journey order (exit 1 on failure)",
    )
    parser.add_argument(
        "--require-export",
        action="store_true",
        help="fail unless the app logs a scan bundle, which is copied into the report as scan.zip",
    )
    parser.add_argument(
        "--log-ready-timeout",
        type=float,
        default=30.0,
        help="seconds to wait for `log stream` to print its first line before launching",
    )
    parser.add_argument("--settle", type=float, default=1.2, help="seconds before a screenshot")
    parser.add_argument("--idle", type=float, default=25.0, help="stop after this long quiet")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--appearance", choices=["light", "dark"], default="light")
    parser.add_argument(
        "--content-size", help="Dynamic Type size, e.g. accessibility-extra-extra-extra-large"
    )
    parser.add_argument("--out", type=Path, help="report folder (default under reports/sim)")
    parser.add_argument("--label", default="", help="short name added to the report folder")
    parser.add_argument("--keep-worktree", action="store_true")
    parser.add_argument("--udid", help="run on this existing Simulator instead of the runner's own")
    parser.add_argument("--keep-booted", action="store_true", help="leave the Simulator on")
    parser.add_argument("--build-wait", type=float, default=1200.0)
    args = parser.parse_args(argv)

    gitref.fetch()
    sha = gitref.resolve(args.ref)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = "-".join(
        filter(None, [stamp, slug(args.ref.removeprefix("origin/")), sha[:8], slug(args.label)])
    )
    out = (args.out or DEFAULT_REPORTS / name).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    launch_args: list[str] = []
    if args.replay:
        replay = args.replay.expanduser().resolve()
        if not replay.exists():
            raise SystemExit(f"Replay not found: {replay}")
        launch_args += ["-replay", str(replay)]
    if args.autopilot:
        launch_args.append("-autopilot")
    if args.server_url:
        launch_args += ["-serverURL", args.server_url]
    launch_args += args.extra_arg

    print(f"Report: {out}\nRef {args.ref} at {sha[:12]}", flush=True)
    with gitref.detached_worktree(sha, keep=args.keep_worktree) as tree:
        static = static_c4_check(tree)
        print("Building for the Simulator...", flush=True)
        build, app = build_app(tree, out, args.build_wait, args.project, args.scheme)
    print(
        f"  build ok={build['ok']} in {build.get('seconds')} s, "
        f"{len(build.get('warnings', []))} warnings",
        flush=True,
    )

    try:
        device = ensure_device(args.udid)
    except BaseException:
        if app is not None:
            discard_app(app)
        raise
    rep = RunReport(
        ref=args.ref,
        sha=sha,
        started_at=dt.datetime.now().isoformat(timespec="seconds"),
        command=[sys.executable, "-m", "hsverify.simrun", *(argv or sys.argv[1:])],
        device=device | {"appearance": args.appearance, "content_size": args.content_size},
        build=build,
        launch_arguments=launch_args,
        static_c4=static,
    )
    started = time.time()
    services = contextlib.ExitStack()
    try:
        if app is None:
            rep.end_reason = "build failed"
            rep.problems.append("The app did not build; see build.log.")
        else:
            if args.server_ref:
                server_sha = gitref.resolve(args.server_ref)
                url, _ = services.enter_context(server_from_ref(server_sha, out / "server.log"))
                launch_args += ["-serverURL", url]
                rep.server = {"ref": args.server_ref, "sha": server_sha, "url": url}
                print(f"Server {args.server_ref} at {server_sha[:12]} on {url}", flush=True)
            bundle_id = bundle_id_of(app)
            rep.device["bundle_id"] = bundle_id
            udid = device["udid"]
            prepare_display(udid, args.appearance, args.content_size)
            subprocess.run(["xcrun", "simctl", "uninstall", udid, bundle_id], capture_output=True)
            simctl("install", udid, str(app))
            discard_app(app)
            # The Simulator has no camera, but granting access keeps the permission prompt from
            # hiding the replay flow. The prompt itself is reviewed on a device.
            subprocess.run(
                ["xcrun", "simctl", "privacy", udid, "grant", "camera", bundle_id],
                capture_output=True,
            )
            print("Running...", flush=True)
            follow(args, udid, bundle_id, out, rep)
            rep.problems += missing_required(args.require, rep.states)
            rep.app_export, export_problems = app_export(out, args.require_export)
            rep.problems += export_problems
        rep.crash_reports = collect_crashes(started, out)
        if rep.crash_reports:
            rep.problems.append(f"Crash reports: {', '.join(rep.crash_reports)}")
    finally:
        if app is not None:
            discard_app(app)
        services.close()
        rep.peak_memory = peak_rss_mb()
        if not args.keep_booted:
            subprocess.run(["xcrun", "simctl", "shutdown", device["udid"]], capture_output=True)
        data = asdict(rep)
        (out / "report.json").write_text(json.dumps(data, indent=2) + "\n")
        report.write_sim_report(out, data)
    print(f"{len(rep.states)} states; end: {rep.end_reason}")
    for problem in rep.problems:
        print(f"PROBLEM: {problem}")
    print(f"Open {out / 'index.html'}")
    return 0 if build["ok"] and not rep.problems else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
