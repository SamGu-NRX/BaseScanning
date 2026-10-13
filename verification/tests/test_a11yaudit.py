import argparse
import subprocess

from hsverify import a11yaudit
from hsverify.a11yaudit import audit_problems, audited_screens, parse_output, symbol_name_labels


def test_parse_output_reads_screens_and_issues_and_skips_noise():
    text = "\n".join(
        [
            "Test Case started.",
            'A11Y_ISSUE={"screen": 2, "description": "Hit area is too small"}',
            'A11Y_SCREEN={"index": 2, "labels": ["text:hi"], "issues": 1}',
            "A11Y_SCREEN={broken",
        ]
    )
    screens, issues = parse_output(text)
    assert screens == [{"index": 2, "labels": ["text:hi"], "issues": 1}]
    assert issues == [{"screen": 2, "description": "Hit area is too small"}]


def test_symbol_name_labels():
    screens = [
        {
            "index": 1,
            "labels": [
                "button:gearshape",
                "button:arrow.left.circle.fill",
                "button:camera",
                "button:I'm here",
                "text:xmark",
            ],
        }
    ]
    assert symbol_name_labels(screens) == [
        {"screen": 1, "label": "gearshape"},
        {"screen": 1, "label": "arrow.left.circle.fill"},
    ]


def screen(index, **extra):
    return {"index": index, "labels": ["text:x"], "issues": 0} | extra


NINE = [screen(i) for i in range(1, 10)]


def test_nine_clean_screens_are_valid():
    assert audit_problems(NINE, harness_ran=True, min_screens=9) == []


def test_zero_screens_is_not_evidence():
    assert audit_problems([], harness_ran=True, min_screens=9) == ["no screen was audited"]


def test_harness_that_did_not_run_is_not_evidence():
    problems = audit_problems(NINE, harness_ran=False, min_screens=9)
    assert problems == ["the audit harness did not finish successfully"]


def test_too_few_distinct_screens():
    repeated = [screen(1), screen(1), screen(2)]
    assert audit_problems(repeated, harness_ran=True, min_screens=9) == [
        "2 screens audited, fewer than --min-screens 9"
    ]


def test_screen_error_fails_and_that_screen_is_not_counted():
    # After a throw the harness prints an error entry and then a normal one for the same index.
    screens = [*NINE, screen(9, error="audit threw"), {"index": 10, "error": "app not running"}]
    assert audit_problems(screens, harness_ran=True, min_screens=9) == [
        "screen 9: audit threw",
        "screen 10: app not running",
        "8 screens audited, fewer than --min-screens 9",
    ]
    assert audited_screens(screens) == set(range(1, 9))


def test_error_lines_from_the_harness_are_parsed_as_screens():
    text = 'A11Y_SCREEN={"error":"app not running","index":1}'
    screens, _ = parse_output(text)
    assert audit_problems(screens, harness_ran=True, min_screens=1) == [
        "screen 1: app not running",
        "no screen was audited",
    ]


def test_failed_xctest_cannot_be_clean_evidence(tmp_path, monkeypatch):
    output = "\n".join(f'A11Y_SCREEN={{"index":{i},"issues":0}}' for i in range(1, 10))
    output += "\n** TEST SUCCEEDED **"
    monkeypatch.setattr(a11yaudit.simrun, "wait_for_other_builds", lambda _: None)
    monkeypatch.setattr(
        a11yaudit.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 65, output, "failed"),
    )
    args = argparse.Namespace(build_wait=0, seconds=1, idle=1)
    text, code = a11yaudit.run_harness("mock", "mock", args, [], tmp_path)
    screens, _ = parse_output(text)
    assert audit_problems(screens, harness_ran=code == 0, min_screens=9)
