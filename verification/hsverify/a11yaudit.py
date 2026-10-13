"""Run Apple's accessibility audit on every screen an app shows, from a git ref.

    uv run python -m hsverify.a11yaudit --ref origin/t3/ios-mvf \
        --replay ~/house-scanning-data/replays/<session> --autopilot

The app is built and installed exactly as `hsverify.simrun` does. Then the UI test bundle in
`fixtures/a11y-audit` launches it by bundle id with the same launch arguments, and runs
`performAccessibilityAudit(for: .all)` on each distinct screen: contrast, Dynamic Type, hit
areas, clipped text, element descriptions and traits. A screen is new when any element in its
accessibility tree is added, removed or relabelled; values and positions don't count. Each
screen's `labels` in the report are still only its texts and buttons.

Apple's audit accepts a button whose label SwiftUI took from an SF Symbol name, which
VoiceOver then reads aloud ("gearshape"). Those labels are flagged here as well.

Reports go to ~/house-scanning-data/reports/a11y/<run>/ with a screenshot per audited screen.
The probe's middle screen carries a deliberately undersized, unlabelled button, so
`--ref HEAD --project verification/fixtures/state-probe/StateProbe.xcodeproj --scheme
StateProbe --min-screens 1` must report at least one issue; a clean result there means the
audit is blind.

A run proves nothing when the harness did not run, the audit threw on a screen, or fewer than
`--min-screens` screens were audited. The report then says `ok: false` with the reasons in
`problems`, and the exit code is 1 whatever the issue count.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from hsverify import gitref, simrun

HARNESS = Path(__file__).resolve().parents[1] / "fixtures" / "a11y-audit" / "A11yAudit.xcodeproj"
DEFAULT_REPORTS = Path.home() / "house-scanning-data" / "reports" / "a11y"
HARNESS_DERIVED = Path("/tmp/hs-verify-a11y-derived-data")
# SF Symbol names are lowercase words joined by dots: "gearshape", "arrow.left.circle.fill".
SYMBOL_NAME = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)*$")
ARG_SEPARATOR = "\x1f"  # the harness splits AUDIT_ARGS on U+001F


def parse_output(text: str) -> tuple[list[dict], list[dict]]:
    """Screens and issues from the harness's `A11Y_SCREEN=` and `A11Y_ISSUE=` lines."""
    screens, issues = [], []
    for line in text.splitlines():
        for tag, sink in (("A11Y_SCREEN=", screens), ("A11Y_ISSUE=", issues)):
            if tag in line:
                try:
                    sink.append(json.loads(line.split(tag, 1)[1]))
                except json.JSONDecodeError:
                    continue
    return screens, issues


# One-word symbol names that are not ordinary English words. Dotted names ("arrow.left") are
# always symbol names; a plain word such as "camera" could be a deliberate label.
SYMBOL_WORDS = {"gearshape", "xmark", "checkmark", "ellipsis", "chevron", "info", "plus"}


def symbol_name_labels(screens: list[dict]) -> list[dict]:
    """Buttons whose accessible label looks like an SF Symbol name."""
    found = []
    for screen in screens:
        for label in screen.get("labels", []):
            kind, _, text = label.partition(":")
            looks_like_symbol = SYMBOL_NAME.match(text) and ("." in text or text in SYMBOL_WORDS)
            if kind == "button" and looks_like_symbol:
                found.append({"screen": screen["index"], "label": text})
    return found


def audited_screens(screens: list[dict]) -> set:
    """Indexes of screens the audit completed on.

    A screen entry carrying "error" means the audit threw on that screen or the app stopped.
    After a throw the harness still prints a normal entry for the same index, so a screen
    counts only if no entry for its index carries an error.
    """
    errored = {s.get("index") for s in screens if "error" in s}
    return {s.get("index") for s in screens if "error" not in s} - errored


def audit_problems(screens: list[dict], harness_ran: bool, min_screens: int) -> list[str]:
    """Why this run is not evidence of an audit; empty when it is."""
    problems = []
    if not harness_ran:
        problems.append("the audit harness did not run (no TEST SUCCEEDED or TEST FAILED line)")
    problems += [f"screen {s.get('index')}: {s['error']}" for s in screens if "error" in s]
    audited = audited_screens(screens)
    if not audited:
        problems.append("no screen was audited")
    elif len(audited) < min_screens:
        problems.append(f"{len(audited)} screens audited, fewer than --min-screens {min_screens}")
    return problems


def run_harness(
    udid: str, bundle_id: str, args: argparse.Namespace, launch: list[str], out: Path
) -> str:
    env = os.environ | {
        "TEST_RUNNER_AUDIT_BUNDLE": bundle_id,
        "TEST_RUNNER_AUDIT_ARGS": ARG_SEPARATOR.join(launch),
        "TEST_RUNNER_AUDIT_SECONDS": str(args.seconds),
        "TEST_RUNNER_AUDIT_IDLE": str(args.idle),
        "TEST_RUNNER_AUDIT_OUT": str(out),
    }
    cmd = [
        "xcodebuild",
        "test",
        "-project",
        str(HARNESS),
        "-scheme",
        "AccessibilityAudit",
        "-destination",
        f"id={udid}",
        "-derivedDataPath",
        str(HARNESS_DERIVED),
        "CODE_SIGNING_ALLOWED=NO",
    ]
    simrun.wait_for_other_builds(args.build_wait)
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    (out / "xcodebuild-test.log").write_text(result.stdout + result.stderr)
    return result.stdout


def write_report(out: Path, data: dict) -> None:
    (out / "report.json").write_text(json.dumps(data, indent=2) + "\n")
    rows = []
    for screen in data["screens"]:
        if "error" in screen:
            rows.append(
                f"<section><div><h2>Screen {screen['index']}: audit error</h2>"
                f"<p>{html.escape(str(screen['error']))}</p></div></section>"
            )
            continue
        issues = [i for i in data["issues"] if i.get("screen") == screen["index"]]
        symbols = [s for s in data["symbol_name_labels"] if s["screen"] == screen["index"]]
        items = "".join(
            f"<li><b>{html.escape(i['description'])}</b> {html.escape(i.get('element', ''))}"
            f"<br><small>{html.escape(i.get('detail', ''))}</small></li>"
            for i in issues
        ) + "".join(
            f"<li><b>Button label is a symbol name</b> {html.escape(s['label'])}</li>"
            for s in symbols
        )
        texts = [lab.split(":", 1)[1] for lab in screen.get("labels", []) if lab.split(":", 1)[1]]
        rows.append(
            f"<section><img src='{html.escape(screen.get('screenshot', ''))}' width=240>"
            f"<div><h2>Screen {screen['index']} <small>+{screen.get('seconds', 0):.1f} s</small>"
            f"</h2><p>{html.escape(' · '.join(texts[:8]))}</p>"
            f"<ul>{items or '<li>No issues</li>'}</ul></div></section>"
        )
    verdict = "".join(f"<p><b>Not valid:</b> {html.escape(p)}</p>" for p in data["problems"])
    page = (
        "<!doctype html><meta charset=utf-8><title>Accessibility audit</title><style>"
        "body{font:15px/1.45 -apple-system,sans-serif;max-width:1000px;margin:32px auto;"
        "padding:0 20px}section{display:flex;gap:20px;margin:20px 0;align-items:flex-start}"
        "img{border-radius:16px;border:1px solid #ccc}h2{font-size:17px;margin:0}"
        "small{color:#6e6e73}</style>"
        f"<h1>Accessibility audit: <code>{html.escape(data['ref'])}</code> at "
        f"<code>{data['sha'][:12]}</code></h1>{verdict}<p>{data['screens_audited']} screens, "
        f"{data['issue_count']} issues. Text size {html.escape(str(data['content_size']))}, "
        f"{html.escape(data['appearance'])}.</p>" + "".join(rows)
    )
    (out / "index.html").write_text(page)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ref", default="origin/t3/ios-mvf")
    parser.add_argument("--project", default="ios/HouseScan.xcodeproj")
    parser.add_argument("--scheme", default="HouseScan")
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--autopilot", action="store_true")
    parser.add_argument("--server-url")
    parser.add_argument("--extra-arg", action="append", default=[])
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--idle", type=float, default=20)
    parser.add_argument("--appearance", choices=["light", "dark"], default="light")
    parser.add_argument("--content-size")
    parser.add_argument("--label", default="")
    parser.add_argument("--build-wait", type=float, default=1200)
    # Nine: the app's ten states less `unsupported`, which needs a device without LiDAR (the
    # same count as metric S3-7). The state-probe fixture has fewer screens; pass a lower value.
    parser.add_argument("--min-screens", type=int, default=9)
    args = parser.parse_args(argv)

    gitref.fetch()
    sha = gitref.resolve(args.ref)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = "-".join(
        filter(
            None,
            [
                stamp,
                simrun.slug(args.ref.removeprefix("origin/")),
                sha[:8],
                simrun.slug(args.label),
            ],
        )
    )
    out = DEFAULT_REPORTS / name
    out.mkdir(parents=True, exist_ok=True)

    launch: list[str] = []
    if args.replay:
        launch += ["-replay", str(args.replay.expanduser().resolve())]
    if args.autopilot:
        launch.append("-autopilot")
    if args.server_url:
        launch += ["-serverURL", args.server_url]
    launch += args.extra_arg

    with gitref.detached_worktree(sha) as tree:
        build, app = simrun.build_app(tree, out, args.build_wait, args.project, args.scheme)
    if app is None:
        print(f"Build failed; see {out / 'build.log'}")
        return 1
    device = simrun.ensure_device()
    udid = device["udid"]
    try:
        bundle_id = simrun.bundle_id_of(app)
        simrun.prepare_display(udid, args.appearance, args.content_size)
        subprocess.run(["xcrun", "simctl", "uninstall", udid, bundle_id], capture_output=True)
        simrun.simctl("install", udid, str(app))
        simrun.discard_app(app)
        subprocess.run(
            ["xcrun", "simctl", "privacy", udid, "grant", "camera", bundle_id], capture_output=True
        )
        print(f"Auditing {bundle_id} at {sha[:12]}...", flush=True)
        output = run_harness(udid, bundle_id, args, launch, out)
    finally:
        subprocess.run(["xcrun", "simctl", "shutdown", udid], capture_output=True)

    screens, issues = parse_output(output)
    symbols = symbol_name_labels(screens)
    harness_ran = "** TEST SUCCEEDED **" in output or "** TEST FAILED **" in output
    problems = audit_problems(screens, harness_ran, args.min_screens)
    data = {
        # ok: the run is evidence of an audit. Issues found are counted separately below.
        "ok": not problems,
        "problems": problems,
        "ref": args.ref,
        "build": build,
        "sha": sha,
        "launch_arguments": launch,
        "appearance": args.appearance,
        "content_size": args.content_size or "large",
        "screens": screens,
        "issues": issues,
        "symbol_name_labels": symbols,
        "issue_count": len(issues) + len(symbols),
        "screens_audited": len(audited_screens(screens)),
        "min_screens": args.min_screens,
        "harness_ran": harness_ran,
    }
    write_report(out, data)
    print(
        f"{data['screens_audited']} screens, {data['issue_count']} issues. "
        f"Open {out / 'index.html'}"
    )
    for issue in issues[:20]:
        print(
            f"  screen {issue.get('screen')}: {issue.get('description')} {issue.get('element', '')}"
        )
    for s in symbols:
        print(f"  screen {s['screen']}: button label is a symbol name: {s['label']}")
    for problem in problems:
        print(f"PROBLEM: {problem}; see {out / 'xcodebuild-test.log'}")
    return 0 if not problems and data["issue_count"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
