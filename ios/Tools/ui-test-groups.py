#!/usr/bin/env python3
"""The three hosted UI test groups, and a check that together they run every UI test once.

CI splits HouseScanUITests across three jobs so none passes the 90-minute cap: `screenStates`,
ScreenStatesUITests alone; `photoProcessing`, PhotoProcessingUITests alone; and `journey`, every
other class. With ScreenStatesUITests in `journey`, run 37221532164 (#216 at 6fa43b9b) hit the
cap: 12 min of package tests and build before the UI step, which ran 75.3 min and stopped with
107 of 109 tests run. That run's per-class test times put ScreenStatesUITests at 1892 s (its
every-state audit 905 s) and the remaining journey classes at about 2475 s. The workflow takes
each group's xcodebuild selectors from here, so the check below reads the same selectors CI
passes.

  ui-test-groups.py args <group> [--full-ui true|false]   print the group's selectors, one per line
  ui-test-groups.py check                                 list the tests from source and check them

`check` finds every `func test...()` in an XCTestCase class under ios/HouseScanUITests, works out
which tests each group's selectors select as xcodebuild does (an -only-testing prefix includes, a
-skip-testing prefix excludes), and fails unless, with the full audit on, each test is in exactly
one group; with it off, the only test left out must be the every-state audit, the existing
pull-request policy. `screenStates` and `photoProcessing` must each hold their one class and
nothing else. It also fails on a selector that names no class or test. It reads source
only, so a test hidden behind `#if` would be listed although the build leaves it out; there is none
today.
"""
import pathlib
import re
import sys

TARGET = "HouseScanUITests"
PHOTO_CLASS = "PhotoProcessingUITests"
SCREEN_CLASS = "ScreenStatesUITests"
# Pull requests skip this without the full-ui label (CONTRIBUTING.md, "Checks").
FULL_UI_ONLY = f"{TARGET}/{SCREEN_CLASS}/testEveryStatePassesTheAccessibilityAudit"
GROUPS = ("screenStates", "journey", "photoProcessing")
# The groups that hold exactly one class, and that class.
SINGLE_CLASS = {"screenStates": SCREEN_CLASS, "photoProcessing": PHOTO_CLASS}


def selectors(group, full_ui):
    if group == "journey":
        return [f"-only-testing:{TARGET}", f"-skip-testing:{TARGET}/{PHOTO_CLASS}", f"-skip-testing:{TARGET}/{SCREEN_CLASS}"]
    if group == "screenStates":
        args = [f"-only-testing:{TARGET}/{SCREEN_CLASS}"]
        if not full_ui:
            args.append(f"-skip-testing:{FULL_UI_ONLY}")
        return args
    if group == "photoProcessing":
        # Its default and largest-text audits always run: they are this group's own evidence.
        return [f"-only-testing:{TARGET}/{PHOTO_CLASS}"]
    sys.exit(f"unknown group {group!r}; the groups are {', '.join(GROUPS)}")


def tests_in_source(folder):
    """Every `Class/testMethod` XCTest would run, read from the Swift sources."""
    found = []
    for path in sorted(folder.glob("*.swift")):
        current = None
        for line in path.read_text(encoding="utf-8").splitlines():
            declared = re.match(r"\s*(?:final\s+)?class\s+(\w+)\s*:\s*([\w ,]+)", line)
            if declared:
                current = declared.group(1) if "XCTestCase" in declared.group(2) else None
                continue
            if re.match(r"\s*(?:final\s+)?class\s+\w+", line):
                current = None
                continue
            extended = re.match(r"\s*extension\s+(\w+)", line)
            if extended:
                current = extended.group(1) if extended.group(1).endswith("Tests") else None
                continue
            method = re.match(r"\s*(?:@MainActor\s+)?func\s+(test\w*)\s*\(\s*\)", line)
            if method and current:
                found.append(f"{TARGET}/{current}/{method.group(1)}")
    return found


def selected(tests, args):
    only = [a.split(":", 1)[1] for a in args if a.startswith("-only-testing:")]
    skip = [a.split(":", 1)[1] for a in args if a.startswith("-skip-testing:")]
    covers = lambda prefix, test: test == prefix or test.startswith(prefix + "/")
    return {t for t in tests if any(covers(p, t) for p in only) and not any(covers(p, t) for p in skip)}


def check():
    folder = pathlib.Path(__file__).resolve().parent.parent / TARGET
    tests = tests_in_source(folder)
    problems = []
    if len(tests) != len(set(tests)):
        problems.append("a test name appears twice in source")
    names = set(tests)
    for full_ui in (True, False):
        for group in GROUPS:
            for arg in selectors(group, full_ui):
                prefix = arg.split(":", 1)[1]
                if prefix != TARGET and not any(t == prefix or t.startswith(prefix + "/") for t in names):
                    problems.append(f"{group}: {arg} names no test")
    full = {g: selected(names, selectors(g, True)) for g in GROUPS}
    pr = {g: selected(names, selectors(g, False)) for g in GROUPS}
    overlap = {t for t in names if sum(t in full[g] for g in GROUPS) > 1}
    missing = names - set().union(*full.values())
    left_out = names - set().union(*pr.values())
    print(f"tests in source: {len(names)}")
    for group in GROUPS:
        print(f"{group}: {len(full[group])} (pull request without full-ui: {len(pr[group])})")
    print(f"overlap: {len(overlap)}")
    print(f"missing: {len(missing)}")
    print(f"left out without full-ui: {sorted(left_out)}")
    problems += [f"in more than one group: {t}" for t in sorted(overlap)]
    problems += [f"in no group: {t}" for t in sorted(missing)]
    if left_out != {FULL_UI_ONLY}:
        problems.append(f"without full-ui exactly {FULL_UI_ONLY} should be left out, not {sorted(left_out)}")
    for group, cls in SINGLE_CLASS.items():
        if not full[group] or any(not t.startswith(f"{TARGET}/{cls}/") for t in full[group]):
            problems.append(f"{group} must hold {cls} and nothing else")
    for group in GROUPS:
        if not pr[group]:
            problems.append(f"{group} runs no test on a pull request without full-ui")
    for problem in problems:
        print(f"error: {problem}")
    return 1 if problems else 0


def main(argv):
    if argv[1:2] == ["check"] and len(argv) == 2:
        return check()
    if argv[1:2] == ["args"] and len(argv) in (3, 5):
        full_ui = True
        if len(argv) == 5:
            if argv[3] != "--full-ui" or argv[4] not in ("true", "false"):
                sys.exit(__doc__)
            full_ui = argv[4] == "true"
        print("\n".join(selectors(argv[2], full_ui)))
        return 0
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
