"""Eleven synthetic v2 transition cases; no real media or model inputs."""

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "equipment_association_prepared", ROOT / "association.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Associations, Conflict = module.Associations, module.Conflict
F = json.loads((ROOT / "fixtures.json").read_text())


class TransitionCases(unittest.TestCase):
    def setUp(self):
        self.a = Associations(F["scope"])
        self.serial = 0
        for cid in ("A", "B"):
            self.send(
                "create_candidate",
                candidate_id=cid,
                description="Synthetic enclosure " + cid,
            )

    def send(self, kind, **data):
        self.serial += 1
        return self.a.apply({"id": f"event-{self.serial}", "type": kind, "data": data})

    def photo(self, name):
        return copy.deepcopy(F["photos"][name])

    def revision(self):
        return self.a.snapshot()["candidates"]["A"]["revision"]

    def label(self, oid="label-a", which="label_a"):
        return self.send(
            "record_label",
            observation_id=oid,
            text="Original label " + oid,
            producer="synthetic-extractor",
            raw_result_ref="synthetic/" + oid + ".json",
            raw_result_sha256="f" * 64,
            photo=self.photo(which),
        )

    def nominate(self, which="context_a", considered=()):
        return self.send(
            "nominate_context",
            candidate_id="A",
            expected_candidate_revision=self.revision(),
            actor="reviewer-1",
            reason="Inspect this contextual photo; no identity probability asserted",
            photo=self.photo(which),
            alternatives=["B"],
            considered_contradictions=list(considered),
        )

    def ticket(self, tid="ticket-a", label="label-a"):
        return self.send(
            "request_attachment",
            ticket_id=tid,
            candidate_id="A",
            label_id=label,
            expected_candidate_revision=self.revision(),
        )

    def review(self, ticket, decision="attach", which="context_a"):
        return self.send(
            "review_attachment",
            ticket_id=ticket["id"],
            expected_ticket_revision=ticket["revision"],
            expected_candidate_revision=ticket["candidate_revision"],
            decision=decision,
            actor="reviewer-1",
            reason="Explicit inspection of original label and contextual enclosure evidence",
            photo=self.photo(which),
        )

    def current(self):
        return [x for x in self.a.snapshot()["attachments"].values() if x["current"]]

    def test_same_uuid_wrong_target(self):
        self.nominate()
        self.label("label-b", "label_b")
        for i, which in enumerate(("context_a", "context_b")):
            self.send(
                "record_track",
                observation_id=f"track-{i}",
                tracker_uuid="unchanged-uuid",
                confidence=0.95,
                photo=self.photo(which),
            )
        ticket = self.ticket(label="label-b")
        self.assertEqual(ticket["status"], "needs_review")
        self.assertEqual(self.current(), [])
        with self.assertRaises(Conflict):
            self.send(
                "attach_from_tracker",
                ticket_id=ticket["id"],
                tracker_uuid="unchanged-uuid",
                confidence=0.95,
            )
        self.assertEqual(
            self.review(ticket, "unresolved", "context_b")["status"], "unresolved"
        )
        self.assertEqual(self.current(), [])
        self.assertIn("label-b", self.a.snapshot()["labels"])

    def test_explicit_review_and_immutable_source(self):
        event = {
            "id": "original-label",
            "type": "record_label",
            "data": {
                "observation_id": "label-a",
                "text": "SERIAL ORIGINAL",
                "producer": "synthetic",
                "raw_result_ref": "synthetic/original.json",
                "raw_result_sha256": "f" * 64,
                "photo": self.photo("label_a"),
            },
        }
        self.a.apply(event)
        event["data"]["text"] = "MUTATED"
        event["data"]["photo"]["xywh"][0] = 999
        self.nominate()
        self.assertEqual(self.review(self.ticket())["status"], "attached")
        view = self.a.snapshot()
        self.assertEqual(view["labels"]["label-a"]["text"], "SERIAL ORIGINAL")
        self.assertEqual(len(self.current()), 1)
        view["labels"]["label-a"]["text"] = "VIEW MUTATION"
        self.assertEqual(
            self.a.snapshot()["labels"]["label-a"]["text"], "SERIAL ORIGINAL"
        )
        sidecar = self.a.sidecar("0" * 64, self.a.snapshot()["revision"])
        self.assertEqual(sidecar["schema"], "equipment-association-sidecar/1")
        self.assertIn("physical equipment identity", sidecar["not_established"])
        self.assertNotIn("supports", sidecar)

    def test_reset_and_late_label(self):
        self.label()
        self.nominate()
        old = self.ticket()
        self.send(
            "reset_scope",
            scope=copy.deepcopy(F["reset_scope"]),
            reason="Tracking reset",
        )
        self.label("late-label", "label_b")
        self.assertEqual(self.review(old)["status"], "stale_review")
        self.assertIn("late-label", self.a.snapshot()["labels"])
        self.assertEqual(self.current(), [])
        self.nominate("new_context_a")
        new = self.ticket("new-ticket", "late-label")
        self.assertEqual(self.review(new, which="new_context_a")["status"], "attached")
        self.assertEqual(len(self.current()), 1)

    def test_contradiction_preserves_history(self):
        self.label()
        self.label("label-b", "label_b")
        self.nominate()
        self.review(self.ticket())
        self.ticket("pending-b", "label-b")
        original = copy.deepcopy(self.a.snapshot()["labels"])
        self.send(
            "contradict",
            candidate_id="A",
            expected_candidate_revision=self.revision(),
            actor="reviewer-2",
            reason="Context suggests a different enclosure",
            photo=self.photo("context_b"),
        )
        snapshot = self.a.snapshot()
        cid = snapshot["candidates"]["A"]["contradictions"][0]
        self.assertEqual(self.current(), [])
        self.assertEqual(snapshot["labels"], original)
        self.assertEqual(
            next(iter(snapshot["attachments"].values()))["status"], "historical_only"
        )
        self.assertEqual(snapshot["tickets"]["pending-b"]["status"], "stale")
        with self.assertRaises(Conflict):
            self.nominate()
        self.nominate(considered=[cid])
        self.assertEqual(
            self.current(),
            [],
            "New nomination does not automatically restore historical links",
        )
        self.review(self.ticket("reattach-a"))
        self.send(
            "nominate_context",
            candidate_id="B",
            expected_candidate_revision=0,
            actor="reviewer-2",
            reason="Competing explicit context claim",
            photo=self.photo("context_b"),
            alternatives=["A"],
            considered_contradictions=[],
        )
        competing = self.send(
            "request_attachment",
            ticket_id="competing-b",
            candidate_id="B",
            label_id="label-a",
            expected_candidate_revision=1,
        )
        result = self.review(competing, which="context_b")
        self.assertEqual(result["reason"], "conflicting_current_assignment")
        self.assertEqual(self.current(), [])
        self.assertTrue(
            all(
                c["status"] == "contradicted"
                for c in self.a.snapshot()["candidates"].values()
            )
        )
        self.assertEqual(self.a.snapshot()["labels"], original)

    def test_candidate_revision_race(self):
        self.label()
        self.nominate()
        old = self.ticket()
        self.nominate("context_b")
        self.assertEqual(self.review(old)["status"], "stale_review")
        self.assertEqual(self.current(), [])
        self.assertEqual(
            self.a.snapshot()["candidates"]["A"]["status"], "context_nominated"
        )

    def test_review_ticket_revision_race(self):
        self.label()
        self.nominate()
        old = self.ticket()
        self.assertEqual(self.review(old, "unresolved")["status"], "unresolved")
        self.assertEqual(self.review(old, "attach")["status"], "stale_review")
        self.assertEqual(self.current(), [])
        current = self.a.snapshot()["tickets"]["ticket-a"]
        self.assertEqual(self.review(current, "attach")["status"], "attached")
        self.assertEqual(self.review(old, "unresolved")["status"], "stale_review")
        self.assertEqual(self.a.snapshot()["tickets"]["ticket-a"]["status"], "attached")
        self.assertEqual(len(self.current()), 1)

    def test_idempotency_and_export_revision(self):
        event = {
            "id": "sample",
            "type": "record_track",
            "data": {
                "observation_id": "sample",
                "tracker_uuid": "uuid",
                "confidence": 0.99,
                "photo": self.photo("context_a"),
            },
        }
        old_revision = self.a.snapshot()["revision"]
        first = self.a.apply(event)
        self.assertEqual(self.a.apply(copy.deepcopy(event)), first)
        event["data"]["confidence"] = 0.8
        with self.assertRaises(Conflict):
            self.a.apply(event)
        with self.assertRaises(Conflict):
            self.a.sidecar("0" * 64, old_revision)
        self.send("reset_scope", scope=copy.deepcopy(F["reset_scope"]), reason="Reset")
        with self.assertRaises(Conflict):
            self.send(
                "reset_scope",
                scope=copy.deepcopy(F["scope"]),
                reason="Old sequence must not return",
            )
        unknown = self.photo("label_a")
        unknown["scope"]["epoch_id"] = "future"
        with self.assertRaises(Conflict):
            self.send(
                "record_label",
                observation_id="future",
                text="saved?",
                producer="synthetic",
                raw_result_ref="synthetic/future.json",
                raw_result_sha256="f" * 64,
                photo=unknown,
            )
        self.assertNotIn("future", self.a.snapshot()["labels"])

    def assert_sibling_refused_atomically(self, ticket_id):
        before = self.a.snapshot()
        with self.assertRaises(Conflict):
            self.ticket(ticket_id)
        self.assertEqual(self.a.snapshot(), before)

    def test_sibling_ticket_rejection_order(self):
        self.label()
        self.nominate()
        first = self.ticket()
        # A queued second form cannot create independent authority before a decision.
        self.assert_sibling_refused_atomically("sibling-before-reject")
        self.assertEqual(self.review(first, "reject")["status"], "rejected")
        # Nor can a later request bypass this revision's rejection.
        self.assert_sibling_refused_atomically("sibling-after-reject")
        self.assertEqual(self.current(), [])
        self.nominate("context_b")
        fresh = self.ticket("fresh-after-rejected-nomination")
        self.assertEqual(self.review(fresh, which="context_b")["status"], "attached")
        self.assertEqual(len(self.current()), 1)
        self.assertEqual(self.a.snapshot()["tickets"]["ticket-a"]["status"], "rejected")

    def test_sibling_ticket_unresolved_order(self):
        self.label()
        self.nominate()
        first = self.ticket()
        self.assert_sibling_refused_atomically("sibling-before-unresolved")
        self.assertEqual(self.review(first, "unresolved")["status"], "unresolved")
        self.assert_sibling_refused_atomically("sibling-after-unresolved")
        self.assertEqual(self.current(), [])
        current = self.a.snapshot()["tickets"]["ticket-a"]
        self.assertEqual(self.review(current)["status"], "attached")
        self.assertEqual(len(self.current()), 1)

    def test_attached_ticket_replay_and_new_nomination(self):
        self.label()
        self.nominate()
        event = {
            "id": "exact-ticket-request",
            "type": "request_attachment",
            "data": {
                "ticket_id": "ticket-a",
                "candidate_id": "A",
                "label_id": "label-a",
                "expected_candidate_revision": self.revision(),
            },
        }
        original_result = self.a.apply(event)
        self.assertEqual(self.review(original_result)["status"], "attached")
        before = self.a.snapshot()
        self.assertEqual(self.a.apply(copy.deepcopy(event)), original_result)
        self.assertEqual(
            self.a.snapshot(), before, "Exact replay does not reapply or advance state"
        )
        self.assert_sibling_refused_atomically("sibling-after-attached")
        self.nominate("context_b")
        self.assertEqual(self.current(), [])
        fresh = self.ticket("fresh-after-attached-nomination")
        self.assertEqual(self.review(fresh, which="context_b")["status"], "attached")
        self.assertEqual(len(self.current()), 1)
        links = list(self.a.snapshot()["attachments"].values())
        self.assertEqual(sum(link["status"] == "historical_only" for link in links), 1)

    def test_same_pair_after_scope_reset(self):
        self.label()
        self.nominate()
        self.review(self.ticket())
        source = copy.deepcopy(self.a.snapshot()["labels"]["label-a"])
        self.send(
            "reset_scope",
            scope=copy.deepcopy(F["reset_scope"]),
            reason="New capture scope",
        )
        provisional = self.ticket("reset-needs-context")
        self.assertEqual(provisional["status"], "needs_context")
        self.nominate("new_context_a")
        self.assertEqual(
            self.a.snapshot()["tickets"]["reset-needs-context"]["status"], "stale"
        )
        fresh = self.ticket("reset-fresh-context")
        self.assertEqual(
            self.review(fresh, which="new_context_a")["status"], "attached"
        )
        self.assertEqual(len(self.current()), 1)
        self.assertEqual(self.a.snapshot()["labels"]["label-a"], source)
        links = list(self.a.snapshot()["attachments"].values())
        self.assertEqual(sum(link["status"] == "historical_only" for link in links), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
