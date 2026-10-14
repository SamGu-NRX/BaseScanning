import contextlib
import datetime as dt
import json
import os
import subprocess
import textwrap
import time

import pytest

from hsverify import scoreboard as sb
from hsverify.scoreboard import (
    Context,
    MetricsError,
    Outcome,
    build_board,
    evaluate,
    load_metrics,
    parse_metric,
    render_markdown,
    summarise_rollup,
)

SHA = "a" * 40
OLD = "b" * 40
WS = {"S1": {"title": "Evals"}}
FIXED_NOW = dt.datetime(2026, 9, 26, 7, 30, tzinfo=dt.UTC)


class FakeTree:
    """Files per SHA; show returns None for a missing file, like gitref.show."""

    def __init__(self, files: dict[str, dict[str, str]]):
        self._files = files

    def files(self, sha):
        return list(self._files.get(sha, {}))

    def show(self, sha, path):
        return self._files.get(sha, {}).get(path)


def ctx(files=None, prs=None, **kw) -> Context:
    return Context(tree=FakeTree(files or {}), prs=prs, now=lambda: FIXED_NOW, **kw)


def metric(kind: str, ref: str = "origin/t3/evals", **probe):
    return parse_metric(
        {"id": "S1-1", "workstream": "S1", "ref": ref, "text": "t", kind: probe}, WS
    )


# --- CI rollup -------------------------------------------------------------------------------


def run(name, conclusion, status="COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def context_status(name, state):
    return {"__typename": "StatusContext", "context": name, "state": state}


def test_rollup_counts_each_bucket_and_names_failures():
    s = summarise_rollup(
        [
            run("iOS build", "SUCCESS"),
            run("Lint", "FAILURE"),
            run("Server", None, status="IN_PROGRESS"),
            run("Optional", "SKIPPED"),
            context_status("CodeRabbit", "SUCCESS"),
            context_status("Vercel", "ERROR"),
            context_status("Queue", "PENDING"),
        ]
    )
    assert s.counts == {"success": 2, "failure": 2, "pending": 2, "skipped": 1}
    assert s.failing == ["Lint", "Vercel"]
    assert s.text() == "2 success, 2 failure, 2 pending, 1 skipped; failing: Lint, Vercel"


def test_rollup_skips_entries_without_name_or_outcome():
    s = summarise_rollup(
        [
            {"__typename": "CheckRun", "name": None, "status": "COMPLETED", "conclusion": None},
            run("Done without conclusion", None),
            context_status(None, "SUCCESS"),
            context_status("No state", None),
            run("ok", "SUCCESS"),
        ]
    )
    assert s.counts == {"success": 1, "failure": 0, "pending": 0, "skipped": 0}
    assert s.text() == "1 success"


def test_rollup_unknown_conclusion_counts_as_failure():
    s = summarise_rollup([run("Weird", "SOMETHING_NEW")])
    assert s.failing == ["Weird"]


def test_rollup_empty():
    assert summarise_rollup(None).text() == "no checks"


# --- metrics file ----------------------------------------------------------------------------


def test_real_metrics_file_loads():
    titles, metrics = load_metrics()
    assert set(titles) == {"S1", "S2", "S3", "S4", "M"}
    assert {m.workstream for m in metrics} == set(titles)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"id": "X", "workstream": "S1", "ref": "r"}, "missing text"),
        ({"id": "X", "workstream": "S9", "ref": "r", "text": "t", "manual": {}}, "unknown work"),
        ({"id": "X", "workstream": "S1", "ref": "r", "text": "t"}, "exactly one probe"),
        (
            {"id": "X", "workstream": "S1", "ref": "r", "text": "t", "manual": {}, "pr_body": {}},
            "exactly one probe",
        ),
        (
            {"id": "X", "workstream": "S1", "ref": "r", "text": "t", "manual": {"status": "ok"}},
            "manual needs evidence",
        ),
        (
            {
                "id": "X",
                "workstream": "S1",
                "ref": "r",
                "text": "t",
                "manual": {"status": "done", "evidence": "e"},
            },
            "manual status must be",
        ),
        (
            {"id": "X", "workstream": "S1", "ref": "r", "text": "t", "report": {"glob": "g"}},
            "report needs key",
        ),
        (
            {
                "id": "X",
                "workstream": "S1",
                "ref": "r",
                "text": "t",
                "report": {"glob": "g", "key": "k", "equals": 1, "less_than": 2},
            },
            "exactly one of equals",
        ),
        (
            {"id": "X", "workstream": "S1", "ref": "r", "text": "t", "file_at_ref": {}},
            "exactly one of path, paths",
        ),
        (
            {
                "id": "X",
                "workstream": "S1",
                "ref": "r",
                "text": "t",
                "grep_at_ref": {"paths": ["a"], "pattern": "x", "each": ["1"]},
            },
            "must contain {each}",
        ),
        (
            {
                "id": "X",
                "workstream": "S1",
                "ref": "r",
                "text": "t",
                "grep_at_ref": {"paths": ["a"], "pattern": "("},
            },
            "bad pattern",
        ),
        (
            {"id": "X", "workstream": "S1", "ref": "r", "text": "t", "pr_check": {"nme": "x"}},
            "does not take nme",
        ),
        (
            {
                "id": "X",
                "workstream": "S1",
                "ref": "r",
                "text": "t",
                "pr_check": {"name": "x", "max_status": "partial"},
            },
            "needs a cap_reason",
        ),
    ],
)
def test_bad_metrics_fail_loudly(raw, message):
    with pytest.raises(MetricsError, match=message):
        parse_metric(raw, WS)


def test_duplicate_ids_rejected(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text(
        textwrap.dedent(
            """
            workstreams: {S1: {title: E}}
            metrics:
              - {id: A, workstream: S1, ref: r, text: t, manual: {status: gap, evidence: e}}
              - {id: A, workstream: S1, ref: r, text: t, manual: {status: gap, evidence: e}}
            """
        )
    )
    with pytest.raises(MetricsError, match="duplicate metric ids: A"):
        load_metrics(path)


# --- ref missing -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "probe"),
    [
        ("file_at_ref", {"path": "a"}),
        ("grep_at_ref", {"paths": ["a"], "pattern": "x"}),
        ("command", {"run": "true"}),
        ("pr_check", {"name": "x"}),
        ("pr_body", {"pattern": "x"}),
        (
            "report",
            {"glob": "/nonexistent/*.json", "key": "k", "equals": 1, "require_current_sha": True},
        ),
    ],
)
def test_missing_ref_is_reported_not_raised(kind, probe):
    out = evaluate(metric(kind, **probe), None, ctx(slow=True))
    assert out == Outcome("ref missing", "origin/t3/evals does not exist")


def test_manual_and_sha_free_report_ignore_missing_ref(tmp_path):
    out = evaluate(metric("manual", status="partial", evidence="half"), None, ctx())
    assert out == Outcome("partial", "half")
    probe = {"glob": str(tmp_path / "*.json"), "key": "k", "equals": 1}
    assert evaluate(metric("report", **probe), None, ctx()).status == "no evidence yet"


def test_a_report_removed_while_the_board_lists_them_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "kept.json").write_text(json.dumps({"k": 1}))
    listed = [str(tmp_path / "removed.json"), str(tmp_path / "kept.json")]
    monkeypatch.setattr(sb.globlib, "glob", lambda pattern: listed)
    probe = {"glob": str(tmp_path / "*.json"), "key": "k", "equals": 1}
    assert evaluate(metric("report", **probe), None, ctx()).status == "met"


# --- file_at_ref and grep_at_ref -------------------------------------------------------------


def test_file_at_ref():
    c = ctx({SHA: {"server/a.json": "{}"}})
    met = evaluate(metric("file_at_ref", path="server/a.json"), SHA, c)
    assert met.status == "met"
    assert met.links == (("server/a.json", f"{sb.BLOB_URL}/{SHA}/server/a.json"),)
    part = evaluate(metric("file_at_ref", paths=["server/a.json", "server/b.json"]), SHA, c)
    assert part == Outcome("partial", "missing at aaaaaaaa: server/b.json", met.links)
    assert evaluate(metric("file_at_ref", path="nope"), SHA, c).status == "gap"


GREP_FILES = {
    SHA: {
        "server/tests/test_golden.py": "def test_golden_01_x(): ...\ndef test_golden_02_y(): ...\n",
        "server/tests/test_other.py": "def test_golden_11(): ...\n",
        "server/solver.py": "golden_03 is not a test\n",
    }
}


def test_grep_without_each_counts_matches():
    c = ctx(GREP_FILES)
    m = metric("grep_at_ref", paths=["server/tests/*.py"], pattern=r"def test_", min_matches=3)
    out = evaluate(m, SHA, c)
    assert out.status == "met"
    assert out.evidence == "3 matches in 2 files"
    m = metric("grep_at_ref", paths=["server/tests/*.py"], pattern=r"def test_", min_matches=4)
    assert evaluate(m, SHA, c).evidence == "3 of 4 matches needed, searched 2 files"


def test_grep_each_is_partial_and_names_what_is_missing():
    m = metric(
        "grep_at_ref",
        paths=["server/tests/*.py"],
        pattern=r"golden[^0-9\n]{0,12}{each}(?!\d)",
        each=["01", "02", "03", "1"],
    )
    out = evaluate(m, SHA, ctx(GREP_FILES))
    # 03 exists only outside the globbed paths; "1" must not match inside "11" or "01".
    assert out.status == "partial"
    assert out.evidence == "found 01, 02; missing 03, 1"
    assert [label for label, _ in out.links] == ["server/tests/test_golden.py"]


def test_grep_each_mapping_uses_labels_and_all_found_is_met():
    m = metric(
        "grep_at_ref",
        paths=["server/tests/*.py"],
        pattern="{each}",
        each={"first": r"golden_0[12]", "eleven": r"golden_11"},
    )
    assert evaluate(m, SHA, ctx(GREP_FILES)).evidence == "all 2 found"


def test_grep_with_no_matching_files_is_a_gap():
    m = metric("grep_at_ref", paths=["ios/*.swift"], pattern="{each}", each=["a"])
    assert evaluate(m, SHA, ctx(GREP_FILES)) == Outcome("gap", "no files match ios/*.swift")


def test_max_status_caps_met_at_partial():
    m = metric(
        "grep_at_ref",
        paths=["server/tests/*.py"],
        pattern="def",
        max_status="partial",
        cap_reason="exists only",
    )
    out = evaluate(m, SHA, ctx(GREP_FILES))
    assert out.status == "partial"
    assert out.evidence.endswith("(exists only)")


# --- command ---------------------------------------------------------------------------------


def fake_worktree(root):
    @contextlib.contextmanager
    def wt(sha):
        yield root

    return wt


def test_command_runs_in_worktree_keeps_last_lines_and_caches(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "marker").write_text("x")
    cache: dict = {}
    c = ctx(slow=True, cache=cache, worktree=fake_worktree(tmp_path))
    run = "ls; echo one; echo two; echo three; echo four"
    out = evaluate(metric("command", run=run, cwd="server"), SHA, c)
    assert out == Outcome("met", "exit 0: two / three / four")
    assert cache["S1-1"]["sha"] == SHA
    assert cache["S1-1"]["status"] == "met"

    out = evaluate(metric("command", run="echo bad; exit 3", cwd="server"), SHA, c)
    assert out == Outcome("gap", "exit 3: bad")


def test_command_missing_cwd_and_timeout(tmp_path):
    c = ctx(slow=True, worktree=fake_worktree(tmp_path))
    assert evaluate(metric("command", run="true", cwd="server"), SHA, c) == Outcome(
        "gap", "no server/ at aaaaaaaa"
    )
    out = evaluate(metric("command", run="echo started; sleep 5", timeout_s=0.5), SHA, c)
    assert out == Outcome("gap", "timed out after 0.5 s: started")


def test_slow_command_not_run_shows_cache_only_for_same_sha(tmp_path):
    cache = {
        "S1-1": {"sha": SHA, "ran_at": "2026-09-26 01:00 PDT", "status": "met", "evidence": "ok"}
    }

    def no_worktree(sha):
        raise AssertionError("slow probe must not run")

    c = ctx(cache=cache, worktree=no_worktree)
    m = metric("command", run="true")
    assert evaluate(m, SHA, c) == Outcome("not run", "slow; last run 2026-09-26 01:00 PDT: met, ok")
    assert evaluate(m, OLD, c) == Outcome("not run", "slow; run with --slow")


# --- report ----------------------------------------------------------------------------------


def write_report(path, data, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    os.utime(path, (mtime, mtime))
    return path


def report_metric(tmp_path, **probe):
    return metric("report", glob=str(tmp_path / "*" / "report.json"), **probe)


def test_report_uses_newest_and_compares(tmp_path):
    write_report(tmp_path / "old" / "report.json", {"latency_ms": 2000}, 100)
    new = write_report(tmp_path / "new" / "report.json", {"latency_ms": 420}, 200)
    out = evaluate(report_metric(tmp_path, key="latency_ms", less_than=1000), SHA, ctx())
    assert out == Outcome("met", f"latency_ms = 420 (want < 1000) at no sha: {new}")


@pytest.mark.parametrize(
    ("data", "probe", "status"),
    [
        ({"build": {"warnings": []}}, {"key": "build.warnings", "length_equals": 0}, "met"),
        ({"build": {"warnings": ["w"]}}, {"key": "build.warnings", "length_equals": 0}, "gap"),
        ({"states": [1, 2, 3]}, {"key": "states", "length_at_least": 3}, "met"),
        ({"states": [1, 2]}, {"key": "states", "length_at_least": 3}, "gap"),
        ({"ok": True}, {"key": "ok", "equals": True}, "met"),
        ({"ok": 1}, {"key": "ok", "equals": "yes"}, "gap"),
        ({"latency_ms": True}, {"key": "latency_ms", "less_than": 1000}, "gap"),
        ({"states": [{"state": "result"}]}, {"key": "states.0.state", "equals": "result"}, "met"),
        ({"n": 3}, {"key": "n", "length_equals": 0}, "gap"),
    ],
)
def test_report_comparators(tmp_path, data, probe, status):
    write_report(tmp_path / "r" / "report.json", data, 100)
    assert evaluate(report_metric(tmp_path, **probe), SHA, ctx()).status == status


def test_report_missing_key_and_no_reports(tmp_path):
    m = report_metric(tmp_path, key="build.ok", equals=True)
    assert evaluate(m, SHA, ctx()).status == "no evidence yet"
    path = write_report(tmp_path / "r" / "report.json", {"build": {}}, 100)
    assert evaluate(m, SHA, ctx()) == Outcome("no evidence yet", f"no build.ok in {path} (no sha)")


def test_report_skips_newer_runs_that_did_not_set_the_key(tmp_path):
    m = report_metric(tmp_path, key="latency_ms", less_than=1000)
    write_report(tmp_path / "old" / "report.json", {"latency_ms": 420.0}, 100)
    write_report(tmp_path / "new" / "report.json", {"latency_ms": None}, 200)
    outcome = evaluate(m, SHA, ctx())
    assert outcome.status == "met"
    assert "old" in outcome.evidence


def test_report_requiring_current_sha_marks_stale_or_picks_matching(tmp_path):
    older = write_report(tmp_path / "a" / "report.json", {"sha": SHA, "states": [1]}, 100)
    newest = write_report(tmp_path / "b" / "report.json", {"sha": OLD, "states": []}, 200)
    m = report_metric(tmp_path, key="states", length_at_least=1, require_current_sha=True)
    out = evaluate(m, SHA, ctx())
    assert out == Outcome("met", f"states = length 1 (want at least 1) at sha aaaaaaaa: {older}")

    out = evaluate(m, "c" * 40, ctx())
    assert out == Outcome("stale", f"report for sha bbbbbbbb, ref at cccccccc: {newest}")


def test_report_sha_key_compares_the_named_field_not_sha(tmp_path):
    # An e2e report: `sha` is the server's, `app_sha` the app's the export came from.
    path = write_report(
        tmp_path / "r" / "report.json",
        {"sha": OLD, "app_sha": SHA, "app_export_passed": True},
        100,
    )
    m = report_metric(
        tmp_path, key="app_export_passed", equals=True, require_current_sha=True, sha_key="app_sha"
    )
    assert evaluate(m, SHA, ctx()) == Outcome(
        "met", f"app_export_passed = true (want True) at app_sha aaaaaaaa: {path}"
    )
    # The server SHA matching the ref is not enough.
    assert evaluate(m, OLD, ctx()) == Outcome(
        "stale", f"report for app_sha aaaaaaaa, ref at bbbbbbbb: {path}"
    )


def test_report_sha_key_missing_from_the_report_is_stale(tmp_path):
    path = write_report(tmp_path / "r" / "report.json", {"sha": SHA, "passed": True}, 100)
    m = report_metric(
        tmp_path, key="passed", equals=True, require_current_sha=True, sha_key="app_sha"
    )
    assert evaluate(m, SHA, ctx()) == Outcome(
        "stale", f"report for no app_sha, ref at aaaaaaaa: {path}"
    )


def test_stale_cites_the_newest_report_that_records_the_sha(tmp_path):
    measured = write_report(tmp_path / "a" / "report.json", {"app_sha": OLD, "passed": True}, 100)
    write_report(tmp_path / "b" / "report.json", {"sha": SHA, "passed": True}, 200)
    m = report_metric(
        tmp_path, key="passed", equals=True, require_current_sha=True, sha_key="app_sha"
    )
    assert evaluate(m, SHA, ctx()) == Outcome(
        "stale", f"report for app_sha bbbbbbbb, ref at aaaaaaaa: {measured}"
    )


def test_report_sha_key_defaults_to_sha():
    assert metric("report", glob="x", key="k", equals=1).probe["sha_key"] == "sha"


def test_report_valid_key_ignores_runs_that_were_not_evidence(tmp_path):
    m = report_metric(
        tmp_path, key="issue_count", equals=0, require_current_sha=True, valid_key="ok"
    )
    good = write_report(
        tmp_path / "a" / "report.json", {"sha": SHA, "ok": True, "issue_count": 0}, 100
    )
    # Newer, but zero screens audited: issue_count 0 must not count.
    blind = write_report(
        tmp_path / "b" / "report.json", {"sha": SHA, "ok": False, "issue_count": 0}, 200
    )
    assert evaluate(m, SHA, ctx()) == Outcome(
        "met", f"issue_count = 0 (want 0) at sha aaaaaaaa: {good}"
    )
    good.unlink()
    assert evaluate(m, SHA, ctx()) == Outcome(
        "gap", f"ok is not true in the newest report (sha aaaaaaaa): {blind}"
    )
    # A report from before `ok` existed is not evidence either.
    write_report(tmp_path / "b" / "report.json", {"sha": SHA, "issue_count": 0}, 300)
    assert evaluate(m, SHA, ctx()).status == "gap"


def test_real_app_metrics_compare_the_app_sha():
    for mid in ("S3-8", "S4-1"):
        m = real_metric(mid)
        assert m.ref == "origin/t3/ios-mvf"
        assert m.probe["require_current_sha"] is True
        assert m.probe["sha_key"] == "app_sha"
    assert real_metric("S3-6b").probe["valid_key"] == "ok"


def test_report_path_shortens_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sb.short_home(tmp_path / "x" / "report.json") == "~/x/report.json"
    assert sb.short_home("/elsewhere/report.json") == "/elsewhere/report.json"


# --- PR probes -------------------------------------------------------------------------------


def pr(branch="t3/evals", head=SHA, checks=(), body=""):
    return {
        "number": 12,
        "title": "Evals",
        "headRefName": branch,
        "headRefOid": head,
        "isDraft": True,
        "updatedAt": "2026-09-26T07:47:51Z",
        "statusCheckRollup": list(checks),
        "body": body,
    }


def test_pr_check_states():
    m = metric("pr_check", name="iOS build")
    ok = {"t3/evals": pr(checks=[run("iOS build", "SUCCESS")])}
    assert evaluate(m, SHA, ctx(prs=ok)).status == "met"
    assert evaluate(m, OLD, ctx(prs=ok)) == Outcome(
        "stale",
        "iOS build passed for aaaaaaaa, ref at bbbbbbbb",
        (("#12", f"{sb.PR_URL}/12"),),
    )
    rerun = {"t3/evals": pr(checks=[run("iOS build", "SUCCESS"), run("iOS build", "FAILURE")])}
    assert evaluate(m, SHA, ctx(prs=rerun)).status == "gap"
    pending = {"t3/evals": pr(checks=[run("iOS build", None, status="QUEUED")])}
    assert evaluate(m, SHA, ctx(prs=pending)).status == "not run"
    other = {"t3/evals": pr(checks=[run("Lint", "SUCCESS")])}
    assert evaluate(m, SHA, ctx(prs=other)).evidence == "no check named 'iOS build'"
    assert evaluate(m, SHA, ctx(prs={})) == Outcome("no evidence yet", "no open PR for t3/evals")
    assert evaluate(m, SHA, ctx(prs=None)) == Outcome("no evidence yet", "PR list unavailable")


def test_pr_body_counts_images():
    m = metric("pr_body", pattern=r"!\[[^\]]*\]\([^)]+\)|<img\s", min_matches=2)
    body = "![a](x.png)\ntext\n<img src=y.png>"
    assert evaluate(m, SHA, ctx(prs={"t3/evals": pr(body=body)})).status == "met"
    assert evaluate(m, SHA, ctx(prs={"t3/evals": pr(body="![a](x)")})).evidence == (
        "1 of 2 matches in the PR body"
    )


# --- board and Markdown ----------------------------------------------------------------------


@pytest.fixture
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_small_board_renders(utc):
    metrics = [
        metric("file_at_ref", path="a.md"),
        parse_metric(
            {
                "id": "S1-2",
                "workstream": "S1",
                "ref": "origin/t3/gone",
                "text": "Pushed | tagged",
                "file_at_ref": {"path": "x"},
            },
            WS,
        ),
    ]
    board = build_board(
        {"S1": "Evals"},
        metrics,
        {"origin/t3/evals": SHA, "origin/t3/gone": None},
        [pr(checks=[run("Lint", "FAILURE"), run("Size", "SUCCESS")])],
        ctx({SHA: {"a.md": ""}}, prs={}),
    )
    md = render_markdown(board)
    assert md == textwrap.dedent(
        f"""\
        # Scoreboard

        Generated 2026-09-26 07:30 UTC by `make scoreboard` (`{sb.COMMAND}`) in `verification/`; slow probes not run (add ARGS=--slow). Probes are defined in `verification/scoreboard/metrics.yaml`.

        ## Open pull requests

        | # | Branch | Head | Draft | CI | Updated |
        | --- | --- | --- | --- | --- | --- |
        | [#12]({sb.PR_URL}/12) | `t3/evals` | `aaaaaaaa` | yes | 1 success, 1 failure; failing: Lint | 2026-09-26 07:47 UTC |

        ## S1 Evals

        `origin/t3/evals` at [`aaaaaaaa`]({sb.TREE_URL}/{SHA}), `origin/t3/gone` (missing). 1 met, 1 ref missing.

        | Metric | Text | Status | Evidence |
        | --- | --- | --- | --- |
        | S1-1 | t | met | present at aaaaaaaa [a.md]({sb.BLOB_URL}/{SHA}/a.md) |
        | S1-2 | Pushed \\| tagged | ref missing | origin/t3/gone does not exist |
        """  # noqa: E501
    )
    data = json.loads(json.dumps(sb.asdict(board)))
    assert data["prs"][0]["ci"]["failing"] == ["Lint"]
    assert data["workstreams"][0]["metrics"][1]["status"] == "ref missing"


def test_board_without_pr_list():
    board = build_board({"S1": "Evals"}, [], {}, None, ctx(), pr_error="gh: not logged in")
    assert "Could not list PRs: gh: not logged in" in render_markdown(board)


# --- the real S2 patterns --------------------------------------------------------------------

S2_TESTS = """
def test_07_unobserved_headroom_is_manual_review() -> None: ...
def test_10_margins_cover_equality(e, offset, expected) -> None: ...
def test_11a_unseen_wall_past_a_tapped_corner() -> None: ...
def test_golden_12_last_start() -> None: ...

@given(st.lists(st.floats(0, 1), min_size=1), st.booleans())
@settings(max_examples=50)
def test_missing_coverage_never_passes(xs, flag) -> None: ...

@given(st.floats())
def test_mirror_gives_the_same_decision(x) -> None: ...
"""


def real_metric(mid):
    return next(m for m in load_metrics()[1] if m.id == mid)


def test_golden_pattern_accepts_numbered_test_names_only():
    out = evaluate(real_metric("S2-2"), SHA, ctx({SHA: {"server/tests/test_g.py": S2_TESTS}}))
    assert out.status == "partial"
    assert out.evidence.startswith("found 07, 10, 11, 12; missing 01, 02, 03, 04, 05, 06, 08")


def test_property_pattern_needs_a_given_decorator():
    # test_07 and test_10 name coverage and equality but are not Hypothesis tests.
    out = evaluate(real_metric("S2-3"), SHA, ctx({SHA: {"server/tests/test_p.py": S2_TESTS}}))
    assert out.status == "partial"
    assert out.evidence.startswith(
        "found missing coverage, mirror; missing equality unsure, larger clearance"
    )


def test_slow_only_runs_the_named_probe(tmp_path):
    c = ctx(slow=True, slow_only=frozenset({"S2-1"}), worktree=fake_worktree(tmp_path))
    assert evaluate(metric("command", run="true"), SHA, c).status == "not run"
    c.slow_only = frozenset({"S1-1"})
    assert evaluate(metric("command", run="true"), SHA, c).status == "met"


def test_tail_keeps_last_three_lines_with_spaces_collapsed():
    assert (
        sb._tail("a\n\n...   [ 82%]\nb\n  87 passed  in 1s \n")
        == "... [ 82%] / b / 87 passed in 1s"
    )


def test_pr_inventory_exceeds_default_page(monkeypatch):
    prs = [{"number": n} for n in range(1, 42)]

    def fake_run(cmd, **kwargs):
        assert int(cmd[cmd.index("--limit") + 1]) > 30
        assert kwargs["timeout"] == sb.PR_LIST_TIMEOUT_S
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(prs))

    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    assert sb.list_prs() == prs


def test_s2_contract_credit_requires_successful_full_run(tmp_path):
    _, metrics = sb.load_metrics()
    metric = next(m for m in metrics if m.id == "S2-8")
    metric.probe["glob"] = str(tmp_path / "*" / "report.json")
    write_report(
        tmp_path / "r" / "report.json",
        {"sha": SHA, "contract_ok": True, "all_passed": False},
        100,
    )
    assert sb.probe_report(metric.probe, SHA).status == "gap"
