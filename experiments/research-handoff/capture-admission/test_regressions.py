"""Existing independent synthetic regression controls, packaged without review tooling."""

from dataclasses import replace
import hashlib
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from admission import Conflict, Ledger, ReturnedStill

TEMP = None
DETAILS = {}


def frame(**changes):
    return replace(
        ReturnedStill(
            "actual-1", 1.2, "epoch-a", "normal", True, (("detail", 1), ("wall", 2))
        ),
        **changes,
    )


class FixControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEMP)
        self.path = Path(self.tmp.name) / "ledger.sqlite"
        self.db = Ledger(self.path)
        self.db.set_epoch("epoch-a")
        self.db.require("detail", 1, "detail")
        self.db.require("wall", 2, "spatial")
        self.db.request("capture-1", "wall", "preview-1")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_epoch_aba_reopen_and_late_old_epoch(self):
        self.db.receive("capture-1", b"first", frame())
        self.assertTrue(self.db.progress()["wall"]["captured"])
        self.db.set_epoch("epoch-b")
        self.db.close()
        self.db = Ledger(self.path)
        before = self.db.snapshot()
        with self.assertRaises(Conflict):
            self.db.set_epoch("epoch-a")
        self.db.set_epoch("epoch-b")
        self.assertEqual(before, self.db.snapshot())
        self.db.request("capture-2", "wall", "preview-2")
        self.db.receive("capture-2", b"late old epoch", frame(frame_id="actual-2"))
        after = self.db.snapshot()
        self.assertTrue(after["progress"]["detail"]["captured"])
        self.assertFalse(after["progress"]["wall"]["captured"])
        self.assertEqual(after["progress"]["wall"]["spatial_epoch"], "epoch-b")
        self.assertEqual(
            self.db.db.execute(
                "SELECT requirement_id FROM support WHERE capture_id='capture-2'"
            ).fetchall(),
            [("detail",)],
        )
        DETAILS["epoch_aba"] = {
            "after_reopen_and_old_epoch_attempt": before,
            "late_old_epoch_callback": after,
        }

    def test_unknown_future_epoch_and_retry_stay_ineligible(self):
        future = frame(epoch="future-epoch")
        self.db.receive("capture-1", b"future", future)
        first = self.db.snapshot()
        self.db.set_epoch("future-epoch")
        self.db.close()
        self.db = Ledger(self.path)
        self.assertEqual(
            self.db.receive("capture-1", b"future", future), "duplicate_callback"
        )
        after = self.db.snapshot()
        self.assertTrue(after["progress"]["detail"]["captured"])
        self.assertFalse(after["progress"]["wall"]["captured"])
        self.assertEqual(
            self.db.db.execute("SELECT requirement_id FROM support").fetchall(),
            [("detail",)],
        )
        DETAILS["future_epoch"] = {
            "before_epoch_introduction": first,
            "after_introduction_reopen_identical_retry": after,
        }

    def test_actual_two_connection_revision_race(self):
        self.db.receive("capture-1", b"revision 2", frame())
        ready, go, attempted, done = [threading.Event() for _ in range(4)]
        failures, events, seam_checks = [], [], []

        def record(name):
            events.append({"event": name, "monotonic": time.monotonic()})

        def writer_b():
            db = None
            try:
                db = Ledger(self.path)

                def trace(sql):
                    if sql == "BEGIN IMMEDIATE":
                        record("B BEGIN IMMEDIATE attempted")
                        attempted.set()

                db.db.set_trace_callback(trace)
                ready.set()
                if not go.wait(3):
                    raise RuntimeError("B not released")
                db.require("wall", 3, "spatial")
                record("B revision 3 committed")
            except BaseException as e:
                failures.append(repr(e))
            finally:
                if db:
                    db.close()
                done.set()

        worker = threading.Thread(target=writer_b)
        worker.start()
        self.assertTrue(ready.wait(3))

        def at_a_write(sql):
            if sql.startswith("INSERT OR REPLACE INTO requirements"):
                record("A validated revision 2; write pending")
                go.set()
                saw_attempt = attempted.wait(2)
                committed_while_a_held_write = done.wait(0.15)
                seam_checks.append(
                    {
                        "b_attempted_begin": saw_attempt,
                        "b_completed_while_a_transaction_open": committed_while_a_held_write,
                        "a_in_transaction": self.db.db.in_transaction,
                    }
                )

        self.db.db.set_trace_callback(at_a_write)
        try:
            self.db.require("wall", 2, "spatial")
            record("A revision 2 committed")
        finally:
            self.db.db.set_trace_callback(None)
            go.set()
            worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(
            seam_checks,
            [
                {
                    "b_attempted_begin": True,
                    "b_completed_while_a_transaction_open": False,
                    "a_in_transaction": True,
                }
            ],
        )
        after = self.db.progress()
        self.assertEqual(after["wall"]["revision"], 3)
        self.assertFalse(after["wall"]["captured"])
        with self.assertRaises(Conflict):
            self.db.require("wall", 2, "spatial")
        DETAILS["two_connection_revision_race"] = {
            "events": events,
            "seam_checks": seam_checks,
            "after": after,
        }

    def test_runtime_types_reject_affinity_alias_and_malformed_shapes(self):
        self.db.require("1", 1, "detail")
        bad_changes = [
            {"frame_id": ["bad"]},
            {"frame_id": True},
            {"frame_id": " "},
            {"timestamp": True},
            {"timestamp": False},
            {"timestamp": "1.0"},
            {"timestamp": float("nan")},
            {"timestamp": float("inf")},
            {"timestamp": -1},
            {"epoch": 1},
            {"epoch": {}},
            {"epoch": "\t"},
            {"tracking": ["normal"]},
            {"tracking": True},
            {"tracking": "unknown"},
            {"usable": 1},
            {"usable": "true"},
            {"supports": ((True, 1),)},
            {"supports": (("1", True),)},
            {"supports": (("1", 1.0),)},
            {"supports": (("1", 0),)},
            {"supports": ((" ", 1),)},
            {"supports": ((["1"], 1),)},
            {"supports": [("1", 1)]},
            {"supports": (["1", 1],)},
            {"supports": (("1",),)},
            {"supports": (("1", 1, "extra"),)},
            {"supports": None},
        ]
        before = self.db.snapshot()
        cases = []
        for changes in bad_changes:
            with self.subTest(changes=repr(changes)):
                with self.assertRaises(ValueError):
                    self.db.receive("capture-1", b"still", frame(**changes))
                self.assertEqual(self.db.snapshot(), before)
                cases.append(repr(changes))
        bad_api = [
            lambda: self.db.require(True, 1, "detail"),
            lambda: self.db.require("1", True, "detail"),
            lambda: self.db.require("x", 1, ["detail"]),
            lambda: self.db.set_epoch(True),
            lambda: self.db.request(True, "wall", "preview"),
            lambda: self.db.request("new", True, "preview"),
            lambda: self.db.request("new", "wall", {}),
            lambda: self.db.receive(True, b"still", frame()),
            lambda: self.db.acknowledge(True, 5),
        ]
        for i, call in enumerate(bad_api):
            with self.subTest(api_case=i):
                with self.assertRaises(ValueError):
                    call()
                self.assertEqual(self.db.snapshot(), before)
        self.assertFalse(self.db.progress()["1"]["captured"])
        DETAILS["runtime_types"] = {
            "returned_still_rejections": cases,
            "public_api_rejections": len(bad_api),
            "no_snapshot_mutations": True,
        }

    def test_read_view_does_not_mix_concurrent_admission_and_ack(self):
        ready, go, done = [threading.Event() for _ in range(3)]
        errors, seam_checks = [], []
        payload = b"concurrent still"

        def writer():
            db = None
            try:
                db = Ledger(self.path)
                ready.set()
                if not go.wait(3):
                    raise RuntimeError("writer not released")
                db.receive("capture-1", payload, frame())
                db.acknowledge(hashlib.sha256(payload).hexdigest(), len(payload))
            except BaseException as e:
                errors.append(repr(e))
            finally:
                if db:
                    db.close()
                done.set()

        thread = threading.Thread(target=writer)
        thread.start()
        self.assertTrue(ready.wait(3))

        def after_first_epoch_read(sql):
            if sql.startswith("SELECT id,revision,kind FROM requirements"):
                go.set()
                seam_checks.append(
                    {
                        "writer_completed_during_reader_transaction": done.wait(3),
                        "reader_in_transaction": self.db.db.in_transaction,
                    }
                )

        self.db.db.set_trace_callback(after_first_epoch_read)
        try:
            old_view = self.db.snapshot()
        finally:
            self.db.db.set_trace_callback(None)
            go.set()
            thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            seam_checks,
            [
                {
                    "writer_completed_during_reader_transaction": True,
                    "reader_in_transaction": True,
                }
            ],
        )
        self.assertFalse(any(v["captured"] for v in old_view["progress"].values()))
        self.assertEqual(old_view["requests"][0][1], "pending")
        self.assertIsNone(old_view["requests"][0][3])
        self.assertEqual(old_view["assets"], [])
        new_view = self.db.snapshot()
        self.assertTrue(all(v["captured"] for v in new_view["progress"].values()))
        self.assertEqual(new_view["requests"][0][1], "admitted")
        self.assertEqual(new_view["assets"][0][2], 1)
        DETAILS["read_view_admission_and_ack"] = {
            "seam_checks": seam_checks,
            "old_view": old_view,
            "new_view": new_view,
        }

    def test_precommit_other_connection_view_and_rollback(self):
        other = Ledger(self.path)
        before = self.db.snapshot()
        observed = {}

        def before_commit():
            self.assertTrue(self.db.db.in_transaction)
            for method in (self.db.progress, self.db.snapshot):
                with self.assertRaises(Conflict):
                    method()
                self.assertTrue(self.db.db.in_transaction)
            observed["other_connection_during_write"] = other.snapshot()
            self.assertEqual(observed["other_connection_during_write"], before)
            raise OSError("independent rollback after failed publication attempts")

        try:
            with self.assertRaises(OSError):
                self.db.receive("capture-1", b"rollback", frame(), before_commit)
            self.assertFalse(self.db.db.in_transaction)
            self.assertEqual(self.db.snapshot(), before)
            self.assertEqual(other.snapshot(), before)
            self.assertEqual(
                self.db.receive("capture-1", b"rollback", frame()), "admitted"
            )
            observed["other_connection_after_commit"] = other.snapshot()
            self.assertTrue(
                observed["other_connection_after_commit"]["progress"]["wall"][
                    "captured"
                ]
            )
        finally:
            other.close()
        DETAILS["precommit_publication_and_rollback"] = observed

    def test_failed_read_rolls_back_read_transaction(self):
        self.db.receive("capture-1", b"saved", frame())
        before = self.db.snapshot()
        denials = []

        def authorizer(action, table, column, database, source):
            if action == sqlite3.SQLITE_READ and table == "assets":
                denials.append(
                    {
                        "table": table,
                        "column": column,
                        "inside_transaction": self.db.db.in_transaction,
                    }
                )
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.db.db.set_authorizer(authorizer)
        try:
            with self.assertRaises(sqlite3.DatabaseError) as caught:
                self.db.snapshot()
            error = str(caught.exception)
        finally:
            self.db.db.set_authorizer(None)
        self.assertTrue(denials)
        self.assertTrue(all(x["inside_transaction"] for x in denials))
        self.assertFalse(self.db.db.in_transaction)
        self.assertEqual(self.db.snapshot(), before)
        self.db.require("detail", 2, "detail")
        self.assertFalse(self.db.progress()["detail"]["captured"])
        DETAILS["failed_read_cleanup"] = {
            "denials": denials,
            "error": error,
            "transaction_cleared": True,
            "subsequent_read_write_succeeded": True,
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
