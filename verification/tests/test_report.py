"""Tests for hsverify.report: the two static pages every sim run folder gets."""

import json
from dataclasses import asdict
from html.parser import HTMLParser

from hsverify.report import write_sim_report
from hsverify.simrun import RunReport
from hsverify.statelog import SUBSYSTEM, parse_ndjson_line

SCRIPT = "<script>alert(1)</script>"
ATTR = '" onmouseover="x'
SHA = "ab12cd34" + "e" * 32
DEVICE = {
    "name": "HouseScan Verify",
    "udid": "1A2B3C4D-0000-0000-0000-000000000000",
    "runtime": "iOS 26.0 (23A345)",
    "type": "com.apple.CoreSimulator.SimDeviceType.iPhone-17",
    "appearance": "light",
    "content_size": None,
}


def state(
    index: int,
    name: str,
    transient: bool = False,
    seconds: float = 1.0,
    screenshot: str | None = None,
) -> dict:
    """One states entry as `asdict(RunReport)` hands it to write_sim_report."""
    return {
        "index": index,
        "state": name,
        "seconds_after_launch": seconds,
        "screenshot": screenshot or f"{index:02d}-{name}.png",
        "transient": transient,
        "log_message": f"STATE={name}",
    }


def sim_data(**overrides: object) -> dict:
    """The fully-populated dict simrun.main hands over: asdict of a finished RunReport."""
    data = asdict(
        RunReport(
            ref="origin/t3/ios-mvf",
            sha=SHA,
            started_at="2026-10-09T08:00:00",
            command=["python3", "-m", "hsverify.simrun", "--ref", "origin/t3/ios-mvf"],
            device=DEVICE,
            build={"ok": True, "seconds": 84.2, "warnings": ["note"], "errors": []},
            launch_arguments=["-replay", "/Users/sam/replays/advio", "-autopilot"],
            static_c4={"anchor is meter-relative": True, "unseen stays unsure": False},
            states=[
                state(1, "onboarding", seconds=2.5),
                state(2, "wall_walk", seconds=6.0),
                state(3, "result", seconds=9.4),
            ],
            end_reason="reached result",
            final_screenshot="final.png",
        )
    )
    data.update(overrides)
    return data


_VOID = {"meta", "br", "img", "link", "hr", "input"}


class _Balanced(HTMLParser):
    """Flags markup that opens or closes across another tag, i.e. an injection got through."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            self.problems.append(f"unbalanced </{tag}>")


def balanced(html_text: str) -> _Balanced:
    parser = _Balanced()
    parser.feed(html_text)
    parser.close()
    return parser


def data_rows(md: str) -> list[str]:
    return [
        ln
        for ln in md.splitlines()
        if ln.startswith("| ") and not ln.startswith("| #") and "---" not in ln
    ]


# --- files and structure ---------------------------------------------------------------------


def test_writes_exactly_the_two_pages(tmp_path):
    write_sim_report(tmp_path, sim_data())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["index.html", "report.md"]


def test_markdown_structure(tmp_path):
    write_sim_report(tmp_path, sim_data())
    md = (tmp_path / "report.md").read_text()
    assert md.splitlines()[0] == f"# Simulator run: `origin/t3/ios-mvf` at `{SHA[:12]}`"
    assert "- Build: passed, 84.2 s, 1 warnings" in md
    assert "- Launch arguments: `-replay /Users/sam/replays/advio -autopilot`" in md
    assert "- States: 3; ended: reached result" in md
    assert "| # | State | +s | Screenshot |" in md
    assert "| --- | --- | --- | --- |" in md


def test_states_table_rows_match_states_and_flag_transients(tmp_path):
    states = [
        state(1, "onboarding", seconds=2.5),
        state(2, "wall_walk", transient=True, seconds=6.0),
    ]
    write_sim_report(tmp_path, sim_data(states=states, final_screenshot=None))
    md = (tmp_path / "report.md").read_text()
    assert data_rows(md) == [
        "| 1 | onboarding | 2.5 | 01-onboarding.png |",
        "| 2 | wall_walk (transient) | 6.0 | 02-wall_walk.png |",
    ]
    assert md.count("(transient)") == 1


def test_summary_counts_match_the_table_and_figures(tmp_path):
    states = [state(i, f"stage_{i}") for i in range(1, 5)]
    write_sim_report(tmp_path, sim_data(states=states))
    html_text = (tmp_path / "index.html").read_text()
    md = (tmp_path / "report.md").read_text()
    assert "<p>4 states." in html_text
    assert html_text.count("<figure>") == 5  # the four states plus the final screen
    assert "- States: 4;" in md
    assert len(data_rows(md)) == 4


def test_no_final_screen_figure_when_absent(tmp_path):
    write_sim_report(tmp_path, sim_data(final_screenshot=None))
    html_text = (tmp_path / "index.html").read_text()
    assert html_text.count("<figure>") == 3
    assert "Final screen" not in html_text


def test_no_states_still_write_the_table_header(tmp_path):
    write_sim_report(
        tmp_path,
        sim_data(states=[], final_screenshot=None, end_reason="timeout after 300 s"),
    )
    html_text = (tmp_path / "index.html").read_text()
    md = (tmp_path / "report.md").read_text()
    assert "<p>0 states. Ended: timeout after 300 s." in html_text
    assert html_text.count("<figure>") == 0
    assert data_rows(md) == []
    assert "| # | State | +s | Screenshot |" in md


# --- HTML escaping ---------------------------------------------------------------------------


def test_index_html_is_well_formed_and_escapes_hostile_values(tmp_path):
    write_sim_report(
        tmp_path,
        sim_data(
            ref=f"{SCRIPT} {ATTR}",
            end_reason=SCRIPT,
            problems=[f"boom\n{ATTR}"],
            launch_arguments=[ATTR],
            states=[state(1, ATTR, screenshot=ATTR)],
            final_screenshot=ATTR,
            static_c4={ATTR: True},
            build={"ok": False, "seconds": 3.1, "warnings": [], "errors": [ATTR]},
        ),
    )
    html_text = (tmp_path / "index.html").read_text()
    assert "<script" not in html_text  # no element injected anywhere
    assert '" onmouseover' not in html_text  # no attribute broken out of
    assert "&lt;script&gt;" in html_text  # the hostile text is there, escaped
    parser = balanced(html_text)
    assert parser.problems == []
    assert parser.stack == []


def test_attribute_values_cannot_break_out_of_single_quotes(tmp_path):
    tricky = "x' onmouseover='y"
    write_sim_report(
        tmp_path,
        sim_data(states=[state(1, tricky, screenshot=tricky)], final_screenshot=tricky),
    )
    html_text = (tmp_path / "index.html").read_text()
    assert " onmouseover='" not in html_text  # html.escape's quote=True covers both styles
    assert "&#x27; onmouseover=&#x27;" in html_text
    parser = balanced(html_text)
    assert parser.problems == []
    assert parser.stack == []


# --- the markdown side -----------------------------------------------------------------------


def test_problem_lines_cannot_inject_markdown_structure(tmp_path):
    hostile = "The app did not build; see build.log.\n| faked | row |\n- Problem: faked list item"
    write_sim_report(tmp_path, sim_data(problems=[hostile]))
    md = (tmp_path / "report.md").read_text()
    problem_lines = [ln for ln in md.splitlines() if ln.startswith("- Problem:")]
    assert len(problem_lines) == 1
    assert "| faked | row |" not in md
    assert "The app did not build; see build.log." in md  # reported, only defused


def test_state_names_from_the_log_grammar_cannot_corrupt_the_table(tmp_path):
    """State cells stay raw because statelog's marker grammar and slug() bound them: detail
    after the marker, pipes and markup in the log line must never reach a cell."""
    logged = json.dumps(
        {
            "timestamp": "2026-09-26 02:50:00.123456-0500",
            "subsystem": SUBSYSTEM,
            "category": "state",
            "eventMessage": "STATE=wall_walk | <script> wall=1",
        }
    )
    event = parse_ndjson_line(logged)
    assert event is not None
    write_sim_report(tmp_path, sim_data(states=[state(1, event.name, seconds=3.2)]))
    md = (tmp_path / "report.md").read_text()
    row = next(ln for ln in md.splitlines() if ln.startswith("| 1 |"))
    assert row == "| 1 | wall_walk | 3.2 | 01-wall_walk.png |"
    assert row.count("|") == 5
    assert "<script" not in md  # the detail after the marker is not report content


# --- caller-guaranteed shapes ----------------------------------------------------------------


def test_build_without_details_renders_the_question_mark_shape(tmp_path):
    write_sim_report(
        tmp_path,
        sim_data(
            build={"ok": False, "error": "StateProbe.xcodeproj not found"},
            end_reason="build failed",
            states=[],
            final_screenshot=None,
            problems=["The app did not build; see build.log."],
        ),
    )
    html_text = (tmp_path / "index.html").read_text()
    md = (tmp_path / "report.md").read_text()
    assert "0 states. Ended: build failed. Build failed in ? s with 0 warnings." in html_text
    assert "Build diagnostics" not in html_text
    assert "- Build: failed, ? s, 0 warnings" in md
    assert "- Problem: The app did not build; see build.log." in md


def test_device_fields_and_launch_arguments_render(tmp_path):
    write_sim_report(tmp_path, sim_data())
    html_text = (tmp_path / "index.html").read_text()
    assert "2026-10-09T08:00:00 · iOS 26.0 (23A345) · light · text size default" in html_text
    assert "<code>-replay /Users/sam/replays/advio -autopilot</code>" in html_text


def test_content_size_when_set(tmp_path):
    data = sim_data()
    data["device"] = data["device"] | {"content_size": "accessibility-extra-extra-large"}
    write_sim_report(tmp_path, data)
    html_text = (tmp_path / "index.html").read_text()
    assert "text size accessibility-extra-extra-large" in html_text


def test_no_launch_arguments_say_none(tmp_path):
    write_sim_report(tmp_path, sim_data(launch_arguments=[]))
    html_text = (tmp_path / "index.html").read_text()
    md = (tmp_path / "report.md").read_text()
    assert "<code>(none)</code>" in html_text
    assert "- Launch arguments: `(none)`" in md
