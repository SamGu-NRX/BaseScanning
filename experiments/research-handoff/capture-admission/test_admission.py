"""Fault controls for the protocol, not perception or installation tests."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from admission import Conflict, Ledger, ReturnedStill


def still(**changes):
    base = ReturnedStill(
        "actual-frame-107",
        10.7,
        "epoch-a",
        "normal",
        True,
        (("detail-a", 1), ("wall-a", 1)),
    )
    return replace(base, **changes)


class AdmissionControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "survey.sqlite"
        self.db = Ledger(self.path)
        self.db.set_epoch("epoch-a")
        self.db.require("detail-a", 1, "detail")
        self.db.require("detail-b", 1, "detail")
        self.db.require("wall-a", 1, "spatial")
        self.db.request("capture-1", "detail-a", "preview-frame-100")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def reopen(self):
        self.db.close()
        self.db = Ledger(self.path)

    def test_good_preview_bad_returned_still(self):
        # The request itself represents an eligible preview, never an admission.
        self.assertFalse(self.db.progress()["detail-a"]["captured"])
        self.assertEqual(
            self.db.receive("capture-1", b"blurry source", still(usable=False)),
            "rejected",
        )
        self.assertFalse(any(v["captured"] for v in self.db.progress().values()))
        self.assertEqual(len(self.db.snapshot()["assets"]), 1)

    def test_actual_still_metadata_and_local_progress_before_upload(self):
        self.db.receive("capture-1", b"actual source", still())
        record = self.db.snapshot()["requests"][0]
        self.assertEqual(record[2], "preview-frame-100")
        self.assertEqual(json.loads(record[3])["frame_id"], "actual-frame-107")
        self.assertEqual(json.loads(record[3])["timestamp"], 10.7)
        self.assertTrue(self.db.progress()["detail-a"]["captured"])
        self.assertEqual(self.db.snapshot()["assets"][0][2], 0)

    def test_save_exception_rolls_back_and_retry_admits_once(self):
        def fail():
            raise OSError("injected storage failure before commit")

        with self.assertRaises(OSError):
            self.db.receive("capture-1", b"actual source", still(), fail)
        self.reopen()
        self.assertEqual(self.db.snapshot()["assets"], [])
        self.assertEqual(self.db.snapshot()["requests"][0][1], "pending")
        self.assertFalse(self.db.progress()["detail-a"]["captured"])
        self.assertEqual(
            self.db.receive("capture-1", b"actual source", still()), "admitted"
        )
        self.assertEqual(self.db.progress()["detail-a"]["unique_assets"], 1)

    def test_owned_process_killed_before_commit(self):
        self.db.close()
        marker = Path(self.tmp.name) / "transaction-entered"
        proc = subprocess.Popen(
            [sys.executable, __file__, "--crash-worker", str(self.path), str(marker)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 10
            while (
                not marker.exists()
                and proc.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertTrue(marker.exists(), "Owned child never reached precommit seam")
            proc.kill()
            proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, -signal.SIGKILL)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=3)
            self.db = Ledger(self.path)
        self.assertEqual(self.db.snapshot()["assets"], [])
        self.assertEqual(self.db.snapshot()["requests"][0][1], "pending")
        self.assertFalse(any(v["captured"] for v in self.db.progress().values()))

    def test_callback_idempotency_and_conflict_after_restart(self):
        self.db.receive("capture-1", b"actual source", still())
        self.reopen()
        before = self.db.snapshot()
        self.assertEqual(
            self.db.receive("capture-1", b"actual source", still()),
            "duplicate_callback",
        )
        for payload, frame in [
            (b"different source", still()),
            (b"actual source", still(timestamp=10.8)),
        ]:
            with self.assertRaises(Conflict):
                self.db.receive("capture-1", payload, frame)
        self.assertEqual(self.db.snapshot(), before)

    def test_other_current_requirement_does_not_inherit_requested_target(self):
        # Actual assessment supports B even though preview/request was for A.
        self.db.receive(
            "capture-1", b"actual B detail", still(supports=(("detail-b", 1),))
        )
        self.assertFalse(self.db.progress()["detail-a"]["captured"])
        self.assertTrue(self.db.progress()["detail-b"]["captured"])

    def test_reset_preserves_details_and_invalidates_old_spatial_progress(self):
        self.db.receive("capture-1", b"actual source", still())
        self.assertTrue(self.db.progress()["wall-a"]["captured"])
        original_assets = self.db.snapshot()["assets"]
        self.db.set_epoch("epoch-b")
        self.assertTrue(self.db.progress()["detail-a"]["captured"])
        self.assertFalse(self.db.progress()["wall-a"]["captured"])
        self.assertEqual(self.db.snapshot()["assets"], original_assets)

    def test_old_epoch_callback_and_limited_tracking_are_not_spatial_evidence(self):
        self.db.set_epoch("epoch-b")
        self.db.receive("capture-1", b"old epoch still", still())
        self.assertTrue(self.db.progress()["detail-a"]["captured"])
        self.assertFalse(self.db.progress()["wall-a"]["captured"])
        self.db.request("capture-2", "wall-a", "preview-b")
        self.db.receive(
            "capture-2", b"limited still", still(epoch="epoch-b", tracking="limited")
        )
        self.assertFalse(self.db.progress()["wall-a"]["captured"])

    def test_requirement_revision_and_unknown_target_do_not_reuse_support(self):
        self.db.receive(
            "capture-1",
            b"actual source",
            still(supports=(("detail-a", 1), ("future-detail", 1))),
        )
        self.db.require("detail-a", 2, "detail")
        self.db.require("future-detail", 1, "detail")
        self.assertFalse(self.db.progress()["detail-a"]["captured"])
        self.assertFalse(self.db.progress()["future-detail"]["captured"])
        with self.assertRaises(Conflict):
            self.db.require("detail-a", 1, "detail")

    def test_empty_assessment_does_not_complete_request(self):
        self.db.receive("capture-1", b"unhelpful but sharp", still(supports=()))
        self.assertFalse(any(v["captured"] for v in self.db.progress().values()))

    def test_server_acceptance_matches_exact_committed_hash_and_length(self):
        data = b"actual source"
        self.db.receive("capture-1", data, still())
        digest = hashlib.sha256(data).hexdigest()
        for key, size in [
            ("0" * 64, len(data)),
            (digest, len(data) + 1),
            (digest, True),
        ]:
            with self.assertRaises(Conflict):
                self.db.acknowledge(key, size)
            self.assertEqual(self.db.snapshot()["assets"][0][2], 0)
        self.db.acknowledge(digest, len(data))
        self.db.acknowledge(digest, len(data))
        self.assertEqual(self.db.snapshot()["assets"][0][2], 1)

    def test_identical_asset_under_two_requests_has_one_unique_asset(self):
        self.db.receive("capture-1", b"same bytes", still())
        self.db.request("capture-2", "detail-a", "preview-frame-200")
        self.db.receive(
            "capture-2",
            b"same bytes",
            still(frame_id="actual-frame-207", timestamp=20.7),
        )
        self.assertEqual(len(self.db.snapshot()["assets"]), 1)
        self.assertEqual(self.db.progress()["detail-a"]["unique_assets"], 1)
        self.assertEqual(len(self.db.snapshot()["requests"]), 2)

    def test_single_inflight_capture_and_explicit_orphan_recovery(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.request("capture-2", "detail-b", "preview-frame-101")
        self.assertEqual(self.db.recover_orphaned_requests(), 1)
        self.db.request("capture-2", "detail-b", "preview-frame-200")
        with self.assertRaises(Conflict):
            self.db.receive("capture-1", b"late callback", still())
        self.assertEqual(self.db.snapshot()["assets"], [])

    def test_prior_epoch_cannot_resurrect_spatial_support(self):
        self.db.receive("capture-1", b"actual source", still())
        self.db.set_epoch("epoch-b")
        self.reopen()
        with self.assertRaises(Conflict):
            self.db.set_epoch("epoch-a")
        self.assertFalse(self.db.progress()["wall-a"]["captured"])
        self.db.set_epoch("epoch-b")  # Current-epoch assignment is idempotent.

    def test_unknown_future_epoch_does_not_gain_spatial_support_later(self):
        self.db.receive(
            "capture-1",
            b"unregistered future-epoch source",
            still(epoch="future-epoch"),
        )
        self.assertFalse(self.db.progress()["wall-a"]["captured"])
        self.db.set_epoch("future-epoch")
        self.assertFalse(self.db.progress()["wall-a"]["captured"])
        self.assertTrue(self.db.progress()["detail-a"]["captured"])

    def test_malformed_metadata_cannot_use_sqlite_affinity_to_match_task(self):
        self.db.require("1", 1, "detail")
        bad = [
            still(frame_id=["frame"]),
            still(timestamp=True),
            still(timestamp=float("nan")),
            still(epoch=1),
            still(supports=((True, 1),)),
            still(supports=(("1", True),)),
        ]
        before = self.db.snapshot()
        for frame in bad:
            with self.assertRaises(ValueError):
                self.db.receive("capture-1", b"actual source", frame)
            self.assertEqual(self.db.snapshot(), before)
        for invalid in (True, 1, ["task"], " "):
            with self.assertRaises(ValueError):
                self.db.require(invalid, 1, "detail")
        self.assertFalse(self.db.progress()["1"]["captured"])

    def test_two_writers_cannot_regress_requirement_revision(self):
        self.db.require("detail-a", 2, "detail")
        self.db.receive(
            "capture-1", b"revision2 evidence", still(supports=(("detail-a", 2),))
        )
        ready, go, done = threading.Event(), threading.Event(), threading.Event()
        failures = []

        def writer_b():
            other = None
            try:
                other = Ledger(self.path)
                ready.set()
                if not go.wait(3):
                    raise RuntimeError("Writer B was never released")
                other.require("detail-a", 3, "detail")
            except BaseException as error:
                failures.append(repr(error))
            finally:
                if other:
                    other.close()
                done.set()

        worker = threading.Thread(target=writer_b)
        worker.start()
        self.assertTrue(ready.wait(3))

        def before_insert(sql):
            if sql.startswith("INSERT OR REPLACE INTO requirements"):
                # A has already read rev2. Old separate autocommits allowed B to
                # install rev3 here, then A overwrote it with rev2. BEGIN IMMEDIATE
                # now holds the write reservation through the validated write.
                go.set()
                done.wait(0.3)

        self.db.db.set_trace_callback(before_insert)
        try:
            self.db.require("detail-a", 2, "detail")
        finally:
            self.db.db.set_trace_callback(None)
            go.set()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.db.progress()["detail-a"]["revision"], 3)
        self.assertFalse(self.db.progress()["detail-a"]["captured"])

    def test_progress_cannot_publish_uncommitted_admission(self):
        def before_commit():
            with self.assertRaises(Conflict):
                self.db.progress()
            with self.assertRaises(Conflict):
                self.db.snapshot()

        self.db.receive("capture-1", b"actual source", still(), before_commit)
        self.assertTrue(self.db.progress()["detail-a"]["captured"])

    def test_snapshot_is_one_read_view_during_concurrent_epoch_and_revision_changes(
        self,
    ):
        self.db.receive("capture-1", b"actual source", still())
        ready, go, done = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def change_state():
            other = None
            try:
                other = Ledger(self.path)
                ready.set()
                if not go.wait(3):
                    raise RuntimeError("Writer was not released")
                other.set_epoch("epoch-b")
                other.require("detail-a", 2, "detail")
            except BaseException as e:
                errors.append(repr(e))
            finally:
                if other:
                    other.close()
                done.set()

        worker = threading.Thread(target=change_state)
        worker.start()
        self.assertTrue(ready.wait(3))

        def change_after_epoch_read(sql):
            if sql.startswith("SELECT id,revision,kind FROM requirements"):
                go.set()
                done.wait(2)

        self.db.db.set_trace_callback(change_after_epoch_read)
        try:
            old_view = self.db.snapshot()
        finally:
            self.db.db.set_trace_callback(None)
            go.set()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(old_view["epoch"], "epoch-a")
        self.assertEqual(old_view["progress"]["detail-a"]["revision"], 1)
        self.assertTrue(old_view["progress"]["wall-a"]["captured"])
        current = self.db.snapshot()
        self.assertEqual(current["epoch"], "epoch-b")
        self.assertEqual(current["progress"]["detail-a"]["revision"], 2)
        self.assertFalse(current["progress"]["wall-a"]["captured"])


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--crash-worker":
        ledger = Ledger(Path(sys.argv[2]))

        def freeze_before_commit():
            Path(sys.argv[3]).write_text("inside uncommitted admission transaction")
            signal.pause()

        ledger.receive("capture-1", b"actual source", still(), freeze_before_commit)
        raise SystemExit("The owned worker should be killed before commit")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AdmissionControls)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt = {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "passed": result.wasSuccessful(),
        "scope": "synthetic admission protocol and SQLite process-crash rollback, no camera/perception/physical accuracy",
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
    }
    Path(__file__).with_name("test-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
