"""Tests for the CI tools in this folder: the UI test groups' check and the result-bundle snapshot.

  python3 -m unittest discover -s ios/Tools -p 'test_*.py'
"""
import contextlib
import importlib.util
import io
import pathlib
import shutil
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent


def load(filename, name):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


groups = load("ui-test-groups.py", "ui_test_groups")
snap = load("snapshot-xcresult.py", "snapshot_xcresult")


def quiet(function, *args):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = function(*args)
    return result, out.getvalue()


class UITestGroupsTests(unittest.TestCase):
    def test_the_committed_groups_pass(self):
        result, out = quiet(groups.check)
        self.assertEqual(result, 0, out)
        self.assertIn("overlap: 0", out)
        self.assertIn("missing: 0", out)

    def test_a_class_in_two_groups_fails(self):
        original = groups.selectors
        def overlapping(group, full_ui):
            args = original(group, full_ui)
            return [a for a in args if a != f"-skip-testing:{groups.TARGET}/{groups.SCREEN_CLASS}"] if group == "journey" else args
        groups.selectors = overlapping
        try:
            result, out = quiet(groups.check)
        finally:
            groups.selectors = original
        self.assertEqual(result, 1)
        self.assertIn("in more than one group", out)

    def test_a_class_in_no_group_fails(self):
        original = groups.selectors
        def dropping(group, full_ui):
            return [f"-only-testing:{groups.TARGET}/{groups.PHOTO_CLASS}"] if group == "screenStates" else original(group, full_ui)
        groups.selectors = dropping
        try:
            result, out = quiet(groups.check)
        finally:
            groups.selectors = original
        self.assertEqual(result, 1)
        self.assertIn("in no group", out)
        self.assertIn("screenStates must hold ScreenStatesUITests and nothing else", out)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.bundle = self.root / "HouseScanUITests.xcresult"
        for path in ["Info.plist", "Data/data.0", "Staging/1_Test/Attachments/shot.png", "Staging/1_Test/Diagnostics/log.txt", "Staging/note.txt"]:
            file = self.bundle / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(path)
        self.out = self.root / "out"

    def test_a_finished_bundle_is_copied_whole(self):
        copied, vanished = snap.snapshot(self.bundle, self.out / self.bundle.name)
        self.assertEqual((copied, vanished), (5, []))
        self.assertEqual((self.out / self.bundle.name / "Staging/1_Test/Attachments/shot.png").read_text(), "Staging/1_Test/Attachments/shot.png")

    def test_a_staging_file_that_vanishes_is_listed_and_the_rest_kept(self):
        def copy(source, target, follow_symlinks):
            if source.endswith("log.txt"):
                raise FileNotFoundError(source)
            shutil.copy2(source, target, follow_symlinks=follow_symlinks)
        copied, vanished = snap.snapshot(self.bundle, self.out, copy=copy)
        self.assertEqual(copied, 4)
        self.assertEqual(vanished, ["Staging/1_Test/Diagnostics/log.txt"])
        self.assertTrue((self.out / "Staging/1_Test/Attachments/shot.png").exists())

    def test_a_staging_folder_that_vanishes_is_listed(self):
        def copy(source, target, follow_symlinks):
            shutil.copy2(source, target, follow_symlinks=follow_symlinks)
            if source.endswith("note.txt"):
                shutil.rmtree(self.bundle / "Staging/1_Test")
        copied, vanished = snap.snapshot(self.bundle, self.out, copy=copy)
        self.assertEqual(vanished, ["Staging/1_Test/"])
        self.assertEqual(copied, 3)

    def test_a_file_outside_staging_that_vanishes_stops_the_copy(self):
        def copy(source, target, follow_symlinks):
            if source.endswith("data.0"):
                raise FileNotFoundError(source)
            shutil.copy2(source, target, follow_symlinks=follow_symlinks)
        with self.assertRaises(FileNotFoundError):
            snap.snapshot(self.bundle, self.out, copy=copy)

    def test_main_reports_incomplete_and_exits_zero(self):
        original = snap.snapshot
        snap.snapshot = lambda bundle, out: (4, ["Staging/1_Test/Diagnostics/log.txt"])
        try:
            result, out = quiet(snap.main, ["snapshot", str(self.bundle), str(self.out)])
        finally:
            snap.snapshot = original
        self.assertEqual(result, 0)
        self.assertIn("::warning::", out)
        report = (self.out / "SNAPSHOT.txt").read_text()
        self.assertTrue(report.startswith("incomplete: copied 4 files; 1 entries under Staging/ vanished"))
        self.assertIn("Staging/1_Test/Diagnostics/log.txt", report)

    def test_main_reports_complete(self):
        result, out = quiet(snap.main, ["snapshot", str(self.bundle), str(self.out)])
        self.assertEqual(result, 0)
        self.assertEqual((self.out / "SNAPSHOT.txt").read_text(), "complete: copied 5 files\n")

    def test_main_fails_loudly_outside_staging(self):
        original = snap.snapshot
        def broken(bundle, out):
            raise PermissionError(13, "denied", str(self.bundle / "Data/data.0"))
        snap.snapshot = broken
        try:
            result, out = quiet(snap.main, ["snapshot", str(self.bundle), str(self.out)])
        finally:
            snap.snapshot = original
        self.assertEqual(result, 1)
        self.assertIn("::error::", out)
        self.assertFalse((self.out / "SNAPSHOT.txt").exists())

    def test_no_bundle_keeps_nothing(self):
        result, out = quiet(snap.main, ["snapshot", str(self.root / "missing.xcresult"), str(self.out)])
        self.assertEqual(result, 0)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
