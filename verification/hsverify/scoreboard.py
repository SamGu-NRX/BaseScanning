"""Scoreboard for the morning meeting: every open PR and every overnight-plan metric.

    uv run python -m hsverify.scoreboard            # fast probes; slow ones show their cache
    uv run python -m hsverify.scoreboard --slow     # also run slow command probes
    uv run python -m hsverify.scoreboard --slow S2-1  # only this slow probe

Writes verification/SCOREBOARD.md and verification/scoreboard/scoreboard.json and prints the
Markdown. PRs come from `gh pr list`; CI is summarised from each PR's status-check rollup.

Metrics live in verification/scoreboard/metrics.yaml. Each names a git ref and exactly one
probe, which is evaluated against the ref's current SHA so the evidence is tied to a commit:

- file_at_ref {path | paths}: met when every file exists at the ref.
- grep_at_ref {paths, pattern, min_matches, each}: `paths` are fnmatch globs over the files
  at the ref (`*` also matches `/`). Without `each`, met when the pattern matches at least
  `min_matches` times. With `each` (a list, or a mapping of label to regex fragment), the
  fragment replaces `{each}` in the pattern and every one must match: met when all do,
  partial when some do, gap when none do.
- command {run, cwd, timeout_s, slow}: runs in a detached worktree of the SHA under /tmp;
  met on exit 0. Slow probes run only with --slow; otherwise the last cached result for the
  same SHA is shown with status "not run".
- report {glob, key, equals | less_than | length_equals | length_at_least,
  require_current_sha, sha_key, valid_key}: judges the newest matching report.json (by
  mtime). `sha_key` (default "sha") names the report field holding the SHA the report was
  measured at; an e2e report carries the server's as `sha` and the app's as `app_sha`. With
  require_current_sha only a report whose SHA equals the ref's counts; an older one makes the
  metric "stale". With `valid_key`, only a report whose value there is true counts, for
  reports that record whether the run itself was evidence (a11yaudit's `ok`). The evidence
  names the SHA the judged report was measured at.
- pr_check {name}: the named CI check on the ref's open PR.
- pr_body {pattern, min_matches}: the ref's open PR description.
- manual {status, evidence}: a judgment the lead records by editing the YAML.

Any probe may set `max_status: partial` with a `cap_reason`, for probes that can only show
that something exists, not that it works or is complete.

A ref that does not exist (branch not pushed) gives "ref missing" for every probe that needs
it; nothing about a missing ref stops the rest of the board.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fnmatch
import glob as globlib
import json
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from hsverify import gitref

GITHUB_REPO = "SamGu-NRX/house-scanning"
BLOB_URL = f"https://github.com/{GITHUB_REPO}/blob"
TREE_URL = f"https://github.com/{GITHUB_REPO}/tree"
PR_URL = f"https://github.com/{GITHUB_REPO}/pull"
VERIFICATION = Path(__file__).resolve().parents[1]
SCOREBOARD_DIR = VERIFICATION / "scoreboard"
METRICS_PATH = SCOREBOARD_DIR / "metrics.yaml"
CACHE_PATH = SCOREBOARD_DIR / ".cache.json"
JSON_PATH = SCOREBOARD_DIR / "scoreboard.json"
MARKDOWN_PATH = VERIFICATION / "SCOREBOARD.md"
COMMAND = "uv run python -m hsverify.scoreboard"
PR_FIELDS = "number,title,headRefName,headRefOid,isDraft,updatedAt,statusCheckRollup,body"

STATUSES = ("met", "partial", "gap", "stale", "not run", "no evidence yet", "ref missing")
MANUAL_STATUSES = ("met", "partial", "gap")
COMPARATORS = ("equals", "less_than", "length_equals", "length_at_least")

# Keys each probe accepts, with defaults for the optional ones (None marks a required key).
PROBE_KEYS: dict[str, dict[str, Any]] = {
    "file_at_ref": {"path": "", "paths": []},
    "grep_at_ref": {"paths": None, "pattern": None, "min_matches": 1, "each": None},
    "command": {"run": None, "cwd": ".", "timeout_s": 600, "slow": True},
    "report": {
        "glob": None,
        "key": None,
        **dict.fromkeys(COMPARATORS),
        "require_current_sha": False,
        "sha_key": "sha",
        "valid_key": None,
    },
    "pr_check": {"name": None},
    "pr_body": {"pattern": None, "min_matches": 1},
    "manual": {"status": None, "evidence": None},
}
COMMON_PROBE_KEYS = {"max_status": None, "cap_reason": None}
METRIC_KEYS = {"id", "workstream", "ref", "text"}

# CI rollup buckets. CheckRun entries carry status + conclusion, StatusContext entries state.
CHECK_CONCLUSIONS = {
    "SUCCESS": "success",
    "NEUTRAL": "success",
    "SKIPPED": "skipped",
    "STALE": "skipped",
    "FAILURE": "failure",
    "CANCELLED": "failure",
    "TIMED_OUT": "failure",
    "ACTION_REQUIRED": "failure",
    "STARTUP_FAILURE": "failure",
}
CONTEXT_STATES = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "ERROR": "failure",
    "PENDING": "pending",
    "EXPECTED": "pending",
}
CI_BUCKETS = ("success", "failure", "pending", "skipped")


class MetricsError(ValueError):
    """metrics.yaml does not match the probe vocabulary."""


# --- metrics file ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Metric:
    id: str
    workstream: str
    ref: str
    text: str
    kind: str
    probe: dict[str, Any]


def load_metrics(path: Path = METRICS_PATH) -> tuple[dict[str, str], list[Metric]]:
    """(workstream id -> title, metrics). Raises MetricsError naming the offending metric."""
    data = yaml.safe_load(path.read_text())
    workstreams = data.get("workstreams") or {}
    metrics = [parse_metric(raw, workstreams) for raw in data.get("metrics") or []]
    ids = [m.id for m in metrics]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise MetricsError(f"duplicate metric ids: {', '.join(dupes)}")
    return {k: v["title"] for k, v in workstreams.items()}, metrics


def parse_metric(raw: dict, workstreams: dict) -> Metric:
    mid = raw.get("id", "<no id>")
    missing = METRIC_KEYS - raw.keys()
    if missing:
        raise MetricsError(f"{mid}: missing {', '.join(sorted(missing))}")
    if raw["workstream"] not in workstreams:
        raise MetricsError(f"{mid}: unknown workstream {raw['workstream']!r}")
    kinds = [k for k in raw if k not in METRIC_KEYS]
    if len(kinds) != 1 or kinds[0] not in PROBE_KEYS:
        raise MetricsError(
            f"{mid}: needs exactly one probe out of {', '.join(PROBE_KEYS)}; got {kinds}"
        )
    kind = kinds[0]
    given = raw[kind] or {}
    allowed = PROBE_KEYS[kind] | COMMON_PROBE_KEYS
    unknown = set(given) - allowed.keys()
    if unknown:
        raise MetricsError(f"{mid}: {kind} does not take {', '.join(sorted(unknown))}")
    required = {k for k, v in PROBE_KEYS[kind].items() if v is None and k not in COMPARATORS}
    required -= {"each", "valid_key"}
    absent = required - given.keys()
    if absent:
        raise MetricsError(f"{mid}: {kind} needs {', '.join(sorted(absent))}")
    probe = {k: v for k, v in allowed.items() if v is not None or k in given} | given
    _check_probe(mid, kind, probe)
    return Metric(mid, raw["workstream"], raw["ref"], raw["text"], kind, probe)


def _check_probe(mid: str, kind: str, probe: dict) -> None:
    if kind == "file_at_ref" and bool(probe.get("path")) == bool(probe.get("paths")):
        raise MetricsError(f"{mid}: file_at_ref needs exactly one of path, paths")
    if kind == "report":
        chosen = [c for c in COMPARATORS if probe.get(c) is not None]
        if len(chosen) != 1:
            raise MetricsError(f"{mid}: report needs exactly one of {', '.join(COMPARATORS)}")
    if kind == "manual" and probe["status"] not in MANUAL_STATUSES:
        raise MetricsError(f"{mid}: manual status must be one of {', '.join(MANUAL_STATUSES)}")
    if kind == "grep_at_ref" and probe.get("each") and "{each}" not in probe["pattern"]:
        raise MetricsError(f"{mid}: pattern must contain {{each}} when each is set")
    if kind in ("grep_at_ref", "pr_body"):
        for _, fragment in _each_items(probe) or [("", "")]:
            try:
                re.compile(probe["pattern"].replace("{each}", fragment))
            except re.error as e:
                raise MetricsError(f"{mid}: bad pattern: {e}") from e
    if probe.get("max_status") not in (None, "partial"):
        raise MetricsError(f"{mid}: max_status can only be partial")
    if probe.get("max_status") and not probe.get("cap_reason"):
        raise MetricsError(f"{mid}: max_status needs a cap_reason")


def _each_items(probe: dict) -> list[tuple[str, str]]:
    each = probe.get("each")
    if not each:
        return []
    if isinstance(each, dict):
        return [(str(k), str(v)) for k, v in each.items()]
    return [(str(v), re.escape(str(v))) for v in each]


# --- CI rollup -------------------------------------------------------------------------------


@dataclass
class CISummary:
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(CI_BUCKETS, 0))
    failing: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [f"{n} {b}" for b, n in self.counts.items() if n]
        if not parts:
            return "no checks"
        text = ", ".join(parts)
        return f"{text}; failing: {', '.join(self.failing)}" if self.failing else text


def classify_check(entry: dict) -> tuple[str, str] | None:
    """(name, bucket) for one rollup entry, or None for entries with no name or outcome.

    A CheckRun that is not COMPLETED is pending; a completed one with no conclusion is
    skipped entirely, as is any entry without a name. An unknown conclusion counts as a
    failure so it shows up rather than vanishing.
    """
    name = entry.get("name") or entry.get("context")
    if not name:
        return None
    if entry.get("__typename") == "StatusContext" or "state" in entry:
        state = entry.get("state")
        if not state:
            return None
        return name, CONTEXT_STATES.get(state, "failure")
    status = entry.get("status")
    if status and status != "COMPLETED":
        return name, "pending"
    conclusion = entry.get("conclusion")
    if not conclusion:
        return None
    return name, CHECK_CONCLUSIONS.get(conclusion, "failure")


def summarise_rollup(entries: list[dict] | None) -> CISummary:
    summary = CISummary()
    for entry in entries or []:
        classified = classify_check(entry)
        if classified is None:
            continue
        name, bucket = classified
        summary.counts[bucket] += 1
        if bucket == "failure":
            summary.failing.append(name)
    return summary


# --- probes ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    status: str
    evidence: str
    links: tuple[tuple[str, str], ...] = ()


class Tree(Protocol):
    def files(self, sha: str) -> list[str]: ...
    def show(self, sha: str, path: str) -> str | None: ...


class GitTree:
    """Files at a commit, read through gitref without touching any working tree."""

    def __init__(self) -> None:
        self._files: dict[str, list[str]] = {}

    def files(self, sha: str) -> list[str]:
        if sha not in self._files:
            self._files[sha] = gitref.git("ls-tree", "-r", "--name-only", sha).splitlines()
        return self._files[sha]

    def show(self, sha: str, path: str) -> str | None:
        return gitref.show(sha, path)


@dataclass
class Context:
    tree: Tree
    prs: dict[str, dict] | None  # branch -> PR; None when the PR list could not be read
    slow: bool = False
    slow_only: frozenset[str] = frozenset()  # with slow: run just these ids; empty runs all
    cache: dict[str, dict] = field(default_factory=dict)
    worktree: Callable[[str], contextlib.AbstractContextManager[Path]] = gitref.detached_worktree
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now().astimezone()


def blob_link(sha: str, path: str) -> tuple[str, str]:
    return path, f"{BLOB_URL}/{sha}/{path}"


def short_home(path: str | Path) -> str:
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home) :] if text.startswith(home + os.sep) else text


def probe_file(probe: dict, sha: str, ctx: Context) -> Outcome:
    paths = probe["paths"] or [probe["path"]]
    present = set(ctx.tree.files(sha))
    found = [p for p in paths if p in present]
    missing = [p for p in paths if p not in present]
    links = tuple(blob_link(sha, p) for p in found)
    if missing:
        status = "partial" if found else "gap"
        return Outcome(status, f"missing at {sha[:8]}: {', '.join(missing)}", links)
    return Outcome("met", f"present at {sha[:8]}", links)


def _matching_files(sha: str, globs: list[str], ctx: Context) -> list[str]:
    return [f for f in ctx.tree.files(sha) if any(fnmatch.fnmatchcase(f, g) for g in globs)]


def probe_grep(probe: dict, sha: str, ctx: Context) -> Outcome:
    files = _matching_files(sha, probe["paths"], ctx)
    texts = {f: ctx.tree.show(sha, f) or "" for f in files}
    need = probe["min_matches"]

    def search(pattern: str) -> tuple[int, list[str]]:
        rx = re.compile(pattern, re.MULTILINE)
        hits = {f: len(rx.findall(t)) for f, t in texts.items()}
        return sum(hits.values()), [f for f, n in hits.items() if n]

    items = _each_items(probe)
    if not items:
        total, where = search(probe["pattern"])
        links = tuple(blob_link(sha, f) for f in where[:3])
        if total >= need:
            return Outcome("met", f"{total} matches in {len(where)} files", links)
        scope = f"{len(files)} files" if files else "no files match " + ", ".join(probe["paths"])
        return Outcome("gap", f"{total} of {need} matches needed, searched {scope}", links)

    found: list[str] = []
    missing: list[str] = []
    where_all: list[str] = []
    for label, fragment in items:
        total, where = search(probe["pattern"].replace("{each}", fragment))
        (found if total >= need else missing).append(label)
        where_all += [f for f in where if f not in where_all]
    links = tuple(blob_link(sha, f) for f in where_all[:3])
    if not missing:
        return Outcome("met", f"all {len(found)} found", links)
    if not files:
        return Outcome("gap", "no files match " + ", ".join(probe["paths"]))
    evidence = f"missing {', '.join(missing)}"
    if found:
        return Outcome("partial", f"found {', '.join(found)}; {evidence}", links)
    return Outcome("gap", f"none found in {len(files)} files; {evidence}", links)


def _tail(output: str, n: int = 3) -> str:
    lines = [" ".join(line.split()) for line in output.splitlines() if line.strip()]
    return " / ".join(lines[-n:]) or "(no output)"


def run_shell(run: str, cwd: Path, timeout_s: float) -> tuple[int | None, str]:
    """(exit code, combined output); exit code None on timeout. Kills the whole process group,
    since a shell's children (uv, pytest) would otherwise outlive it and hold the pipe open."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    proc = subprocess.Popen(
        run,
        shell=True,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        return None, out
    return proc.returncode, out


def probe_command(metric: Metric, sha: str, ctx: Context) -> Outcome:
    probe = metric.probe
    if probe["slow"] and not (ctx.slow and (not ctx.slow_only or metric.id in ctx.slow_only)):
        cached = ctx.cache.get(metric.id)
        if cached and cached.get("sha") == sha:
            return Outcome(
                "not run",
                f"slow; last run {cached['ran_at']}: {cached['status']}, {cached['evidence']}",
            )
        return Outcome("not run", "slow; run with --slow")
    with ctx.worktree(sha) as root:
        cwd = root / probe["cwd"]
        if not cwd.is_dir():
            return Outcome("gap", f"no {probe['cwd']}/ at {sha[:8]}")
        code, out = run_shell(probe["run"], cwd, probe["timeout_s"])
    if code is None:
        outcome = Outcome("gap", f"timed out after {probe['timeout_s']} s: {_tail(out)}")
    else:
        outcome = Outcome("met" if code == 0 else "gap", f"exit {code}: {_tail(out)}")
    ctx.cache[metric.id] = {
        "sha": sha,
        "ran_at": ctx.now().strftime("%Y-%m-%d %H:%M %Z"),
        "status": outcome.status,
        "evidence": outcome.evidence,
    }
    return outcome


def lookup(data: Any, dotted: str) -> Any:
    """Value at a dotted path; list elements by integer index. Raises KeyError when absent."""
    for part in dotted.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and re.fullmatch(r"-?\d+", part):
            try:
                data = data[int(part)]
            except IndexError as e:
                raise KeyError(dotted) from e
        else:
            raise KeyError(dotted)
    return data


def compare(value: Any, probe: dict) -> tuple[bool, str]:
    """(passed, description of the value) for the report's single comparator."""
    if probe.get("equals") is not None:
        return value == probe["equals"], f"{json.dumps(value)} (want {probe['equals']!r})"
    if probe.get("less_than") is not None:
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        return ok and value < probe["less_than"], f"{value} (want < {probe['less_than']})"
    if not isinstance(value, list | dict | str):
        return False, f"{json.dumps(value)} has no length"
    if probe.get("length_equals") is not None:
        return len(value) == probe["length_equals"], (
            f"length {len(value)} (want {probe['length_equals']})"
        )
    return len(value) >= probe["length_at_least"], (
        f"length {len(value)} (want at least {probe['length_at_least']})"
    )


def _read_report(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _value_or_none(data: dict, key: str) -> Any:
    try:
        return lookup(data, key)
    except KeyError:
        return None


def probe_report(probe: dict, sha: str | None) -> Outcome:
    stamped = []
    for p in map(Path, globlib.glob(os.path.expanduser(probe["glob"]))):
        try:  # other runs write and remove reports while the board is built
            stamped.append((p.stat().st_mtime, str(p), p))
        except OSError:
            continue
    paths = [p for *_, p in sorted(stamped, reverse=True)]
    if not paths:
        return Outcome("no evidence yet", f"no report matches {probe['glob']}")
    reports = [(p, _read_report(p)) for p in paths]
    sha_key = probe["sha_key"]

    def measured_at(data: dict | None) -> str:
        value = _value_or_none(data or {}, sha_key)
        return f"{sha_key} {str(value)[:8]}" if value else f"no {sha_key}"

    if probe["require_current_sha"]:
        if sha is None:
            raise ValueError("require_current_sha needs the ref's SHA")
        current = [
            (p, d) for p, d in reports if d is not None and _value_or_none(d, sha_key) == sha
        ]
        if not current:
            # Cite the newest run that measured this SHA at all: a later run that did not (an
            # e2e run without an app export) says nothing about which commit was checked.
            measured = [(p, d) for p, d in reports if d and _value_or_none(d, sha_key)]
            newest, data = (measured or reports)[0]
            return Outcome(
                "stale",
                f"report for {measured_at(data)}, ref at {sha[:8]}: {short_home(newest)}",
            )
        reports = current
    if probe.get("valid_key"):
        valid = [(p, d) for p, d in reports if d and _value_or_none(d, probe["valid_key"]) is True]
        if not valid:
            newest, data = reports[0]
            return Outcome(
                "gap",
                f"{probe['valid_key']} is not true in the newest report ({measured_at(data)}): "
                f"{short_home(newest)}",
            )
        reports = valid
    # A run that did not exercise this metric leaves its key null; use the newest that did.
    with_value = [
        (p, d) for p, d in reports if d is not None and _value_or_none(d, probe["key"]) is not None
    ]
    if not with_value:
        path, data = reports[0]
        if data is None:
            return Outcome("gap", f"unreadable report {short_home(path)}")
        return Outcome(
            "no evidence yet", f"no {probe['key']} in {short_home(path)} ({measured_at(data)})"
        )
    path, data = with_value[0]
    value = lookup(data, probe["key"])
    ok, described = compare(value, probe)
    return Outcome(
        "met" if ok else "gap",
        f"{probe['key']} = {described} at {measured_at(data)}: {short_home(path)}",
    )


def _pr_for(ref: str, ctx: Context) -> tuple[dict | None, str]:
    branch = ref.removeprefix("origin/")
    if ctx.prs is None:
        return None, "PR list unavailable"
    pr = ctx.prs.get(branch)
    return pr, "" if pr else f"no open PR for {branch}"


def probe_pr_check(probe: dict, ref: str, sha: str, ctx: Context) -> Outcome:
    pr, why = _pr_for(ref, ctx)
    if pr is None:
        return Outcome("no evidence yet", why)
    link = (f"#{pr['number']}", f"{PR_URL}/{pr['number']}")
    buckets = [
        c[1]
        for c in map(classify_check, pr.get("statusCheckRollup") or [])
        if c and c[0] == probe["name"]
    ]
    if not buckets:
        return Outcome("no evidence yet", f"no check named {probe['name']!r}", (link,))
    # A re-run leaves several entries under one name; any failure or pending one decides.
    bucket = next((b for b in ("failure", "pending") if b in buckets), buckets[0])
    head = pr["headRefOid"]
    if bucket == "success" and head != sha:
        return Outcome("stale", f"{probe['name']} passed for {head[:8]}, ref at {sha[:8]}", (link,))
    status = {"success": "met", "failure": "gap", "pending": "not run"}.get(bucket, "gap")
    return Outcome(status, f"{probe['name']}: {bucket} at {head[:8]}", (link,))


def probe_pr_body(probe: dict, ref: str, ctx: Context) -> Outcome:
    pr, why = _pr_for(ref, ctx)
    if pr is None:
        return Outcome("no evidence yet", why)
    link = (f"#{pr['number']}", f"{PR_URL}/{pr['number']}")
    n = len(re.findall(probe["pattern"], pr.get("body") or "", re.MULTILINE))
    need = probe["min_matches"]
    return Outcome("met" if n >= need else "gap", f"{n} of {need} matches in the PR body", (link,))


def needs_sha(metric: Metric) -> bool:
    if metric.kind == "manual":
        return False
    if metric.kind == "report":
        return bool(metric.probe["require_current_sha"])
    return True


def evaluate(metric: Metric, sha: str | None, ctx: Context) -> Outcome:
    if sha is None and needs_sha(metric):
        return Outcome("ref missing", f"{metric.ref} does not exist")
    p = metric.probe
    match metric.kind:
        case "manual":
            outcome = Outcome(p["status"], p["evidence"])
        case "file_at_ref":
            outcome = probe_file(p, sha, ctx)
        case "grep_at_ref":
            outcome = probe_grep(p, sha, ctx)
        case "command":
            outcome = probe_command(metric, sha, ctx)
        case "report":
            outcome = probe_report(p, sha)
        case "pr_check":
            outcome = probe_pr_check(p, metric.ref, sha, ctx)
        case "pr_body":
            outcome = probe_pr_body(p, metric.ref, ctx)
        case _:
            raise MetricsError(f"{metric.id}: unknown probe {metric.kind}")
    if p.get("max_status") == "partial" and outcome.status == "met":
        return Outcome("partial", f"{outcome.evidence} ({p['cap_reason']})", outcome.links)
    return outcome


# --- board -----------------------------------------------------------------------------------


@dataclass
class Row:
    id: str
    text: str
    ref: str
    sha: str | None
    status: str
    evidence: str
    links: list[list[str]]


@dataclass
class Board:
    generated_at: str
    command: str
    slow: bool | list[str]  # True, False, or the ids of the only slow probes run
    prs: list[dict] | None
    pr_error: str | None
    workstreams: list[dict]


def pr_rows(prs: list[dict]) -> list[dict]:
    rows = []
    for pr in sorted(prs, key=lambda p: -p["number"]):
        ci = summarise_rollup(pr.get("statusCheckRollup"))
        rows.append(
            {
                "number": pr["number"],
                "title": pr["title"],
                "branch": pr["headRefName"],
                "head": pr["headRefOid"],
                "draft": bool(pr["isDraft"]),
                "updated": pr["updatedAt"],
                "ci": {"counts": ci.counts, "failing": ci.failing, "text": ci.text()},
            }
        )
    return rows


def build_board(
    titles: dict[str, str],
    metrics: list[Metric],
    shas: dict[str, str | None],
    prs: list[dict] | None,
    ctx: Context,
    pr_error: str | None = None,
) -> Board:
    streams = []
    for ws, title in titles.items():
        rows = []
        for m in (m for m in metrics if m.workstream == ws):
            sha = shas[m.ref]
            outcome = evaluate(m, sha, ctx)
            links = [list(link) for link in outcome.links]
            rows.append(Row(m.id, m.text, m.ref, sha, outcome.status, outcome.evidence, links))
        refs = list(dict.fromkeys(r.ref for r in rows))
        streams.append(
            {
                "id": ws,
                "title": title,
                "refs": [{"ref": r, "sha": shas[r]} for r in refs],
                "metrics": [asdict(r) for r in rows],
            }
        )
    return Board(
        generated_at=ctx.now().isoformat(timespec="seconds"),
        command=COMMAND,
        slow=sorted(ctx.slow_only) if ctx.slow and ctx.slow_only else ctx.slow,
        prs=pr_rows(prs) if prs is not None else None,
        pr_error=pr_error,
        workstreams=streams,
    )


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _local(iso: str) -> str:
    stamp = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return stamp.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def render_markdown(board: Board) -> str:
    if isinstance(board.slow, list):
        slow = f"slow probes ran for {', '.join(board.slow)} only"
    else:
        slow = "slow probes ran" if board.slow else "slow probes not run (add ARGS=--slow)"
    lines = [
        "# Scoreboard",
        "",
        f"Generated {_local(board.generated_at)} by `make scoreboard` (`{board.command}`) in "
        f"`verification/`; {slow}. Probes are defined in `verification/scoreboard/metrics.yaml`.",
        "",
        "## Open pull requests",
        "",
    ]
    if board.prs is None:
        lines.append(f"Could not list PRs: {board.pr_error}")
    elif not board.prs:
        lines.append("No open pull requests.")
    else:
        lines += [
            "| # | Branch | Head | Draft | CI | Updated |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| [#{p['number']}]({PR_URL}/{p['number']}) | `{p['branch']}` | "
            f"`{p['head'][:8]}` | {'yes' if p['draft'] else 'no'} | {_cell(p['ci']['text'])} | "
            f"{_local(p['updated'])} |"
            for p in board.prs
        ]
    for ws in board.workstreams:
        refs = ", ".join(
            f"`{r['ref']}` at [`{r['sha'][:8]}`]({TREE_URL}/{r['sha']})"
            if r["sha"]
            else f"`{r['ref']}` (missing)"
            for r in ws["refs"]
        )
        counts = dict.fromkeys(STATUSES, 0)
        for m in ws["metrics"]:
            counts[m["status"]] += 1
        tally = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
        lines += ["", f"## {ws['id']} {ws['title']}", "", f"{refs}. {tally}.", ""]
        lines += ["| Metric | Text | Status | Evidence |", "| --- | --- | --- | --- |"]
        for m in ws["metrics"]:
            evidence = _cell(m["evidence"])
            if m["links"]:
                evidence += " " + ", ".join(f"[{_cell(label)}]({url})" for label, url in m["links"])
            lines.append(f"| {m['id']} | {_cell(m['text'])} | {m['status']} | {evidence} |")
    return "\n".join(lines) + "\n"


# --- I/O -------------------------------------------------------------------------------------


# gh can wait indefinitely for the network or a login; the board is written without PRs instead.
PR_LIST_TIMEOUT_S = 60


def list_prs() -> list[dict]:
    out = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            GITHUB_REPO,
            "--state",
            "open",
            "--limit",
            "10000",
            "--json",
            PR_FIELDS,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=PR_LIST_TIMEOUT_S,
    ).stdout
    return json.loads(out)


def resolve_refs(refs: list[str]) -> dict[str, str | None]:
    return {r: gitref.resolve(r) if gitref.ref_exists(r) else None for r in refs}


@contextlib.contextmanager
def cached(path: Path) -> Iterator[dict]:
    data = json.loads(path.read_text()) if path.exists() else {}
    yield data
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--slow",
        nargs="*",
        metavar="ID",
        help="also run slow command probes; with ids, only those",
    )
    parser.add_argument("--no-fetch", action="store_true", help="skip `git fetch origin`")
    args = parser.parse_args(argv)

    titles, metrics = load_metrics()
    if not args.no_fetch:
        gitref.fetch()
    shas = resolve_refs(list(dict.fromkeys(m.ref for m in metrics)))
    prs: list[dict] | None
    pr_error = None
    try:
        prs = list_prs()
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as e:
        prs = None
        # A timeout's stderr is bytes even with text=True; its own message says what happened.
        stderr = getattr(e, "stderr", None)
        pr_error = (stderr if isinstance(stderr, str) and stderr.strip() else str(e)).strip()
        print(f"warning: could not list PRs: {pr_error}", file=sys.stderr)
    with cached(CACHE_PATH) as cache:
        ctx = Context(
            tree=GitTree(),
            prs={p["headRefName"]: p for p in prs} if prs is not None else None,
            slow=args.slow is not None,
            slow_only=frozenset(args.slow or ()),
            cache=cache,
        )
        board = build_board(titles, metrics, shas, prs, ctx, pr_error)
    markdown = render_markdown(board)
    MARKDOWN_PATH.write_text(markdown)
    JSON_PATH.write_text(json.dumps(asdict(board), indent=2) + "\n")
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
