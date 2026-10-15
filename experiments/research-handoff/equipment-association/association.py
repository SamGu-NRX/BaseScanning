"""Prepared evidence-association reducer; software checks do not prove physical identity."""

from __future__ import annotations
import copy
import hashlib
import json
import math
import re
import threading


class Conflict(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Conflict(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def scope_key(scope):
    require(
        isinstance(scope, dict)
        and set(scope) == {"session_id", "epoch_id", "sequence_id"},
        "scope fields",
    )
    require(all(text(v) for v in scope.values()), "scope identifiers")
    return canonical(scope)


def photo(region):
    require(
        isinstance(region, dict)
        and set(region)
        == {
            "frame_id",
            "asset_ref",
            "asset_sha256",
            "width",
            "height",
            "xywh",
            "scope",
        },
        "photo region fields",
    )
    require(
        text(region["frame_id"])
        and text(region["asset_ref"])
        and digest(region["asset_sha256"]),
        "photo reference/hash",
    )
    require(
        all(type(region[k]) is int and region[k] > 0 for k in ("width", "height")),
        "photo dimensions",
    )
    box = region["xywh"]
    require(
        isinstance(box, list)
        and len(box) == 4
        and all(type(v) in (int, float) and math.isfinite(v) for v in box),
        "finite photo region",
    )
    x, y, w, h = box
    require(
        x >= 0
        and y >= 0
        and w > 0
        and h > 0
        and x + w <= region["width"]
        and y + h <= region["height"],
        "photo region bounds",
    )
    scope_key(region["scope"])
    return region


class Associations:
    """One process-local actor; persist events/state atomically in a real app adapter."""

    def __init__(self, scope):
        key = scope_key(scope)
        self._lock = threading.RLock()
        self._state = {
            "revision": 0,
            "scope": copy.deepcopy(scope),
            "seen_scopes": [key],
            "candidates": {},
            "labels": {},
            "tracks": {},
            "nominations": {},
            "tickets": {},
            "reviews": {},
            "attachments": {},
            "events": {},
        }

    def apply(self, event):
        """Apply one event atomically; duplicate exact IDs return the original result."""
        frozen = json.loads(canonical(event))
        require(
            isinstance(frozen, dict) and set(frozen) == {"id", "type", "data"},
            "event fields",
        )
        require(
            text(frozen["id"])
            and text(frozen["type"])
            and isinstance(frozen["data"], dict),
            "event identity/data",
        )
        encoded = canonical(frozen)
        with self._lock:
            old = self._state["events"].get(frozen["id"])
            if old:
                require(
                    old["event"] == frozen, "event ID reused with different content"
                )
                return copy.deepcopy(old["result"])
            state = copy.deepcopy(self._state)
            result = self._reduce(state, frozen)
            state["revision"] += 1
            result["snapshot_revision"] = state["revision"]
            state["events"][frozen["id"]] = {
                "event": frozen,
                "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                "result": result,
            }
            self._state = state
            return copy.deepcopy(result)

    @staticmethod
    def _reduce(s, e):
        kind, d, eid = e["type"], e["data"], e["id"]

        def fields(*names):
            require(set(d) == set(names), "unexpected/missing event fields")

        def known_photo(value):
            p = photo(value)
            require(
                scope_key(p["scope"]) in s["seen_scopes"], "unregistered photo scope"
            )
            return p

        def current_photo(value):
            p = known_photo(value)
            require(p["scope"] == s["scope"], "context photo is not in current scope")
            return p

        def candidate(cid, revision=None):
            require(cid in s["candidates"], "unknown equipment candidate")
            c = s["candidates"][cid]
            if revision is not None:
                require(
                    type(revision) is int and revision == c["revision"],
                    "stale candidate revision",
                )
            return c

        def invalidate(cid, reason):
            for ticket in s["tickets"].values():
                if ticket["candidate_id"] == cid and ticket["status"] in {
                    "needs_context",
                    "needs_review",
                    "unresolved",
                }:
                    ticket.update(
                        status="stale", reason=reason, revision=ticket["revision"] + 1
                    )

        if kind == "create_candidate":
            fields("candidate_id", "description")
            require(
                text(d["candidate_id"]) and text(d["description"]),
                "candidate description",
            )
            require(
                d["candidate_id"] not in s["candidates"], "candidate ID already exists"
            )
            s["candidates"][d["candidate_id"]] = {
                "id": d["candidate_id"],
                "description": d["description"],
                "revision": 0,
                "status": "needs_context",
                "nomination_id": None,
                "contradictions": [],
            }
            return {"status": "needs_context", "reason": "context_photo_required"}
        if kind in {"record_label", "record_track"}:
            if kind == "record_label":
                fields(
                    "observation_id",
                    "text",
                    "producer",
                    "raw_result_ref",
                    "raw_result_sha256",
                    "photo",
                )
                require(
                    isinstance(d["text"], str)
                    and text(d["producer"])
                    and text(d["raw_result_ref"])
                    and digest(d["raw_result_sha256"]),
                    "label source fields",
                )
                table = s["labels"]
            else:
                fields("observation_id", "tracker_uuid", "confidence", "photo")
                require(
                    text(d["tracker_uuid"])
                    and type(d["confidence"]) in (int, float)
                    and math.isfinite(d["confidence"])
                    and 0 <= d["confidence"] <= 1,
                    "tracker diagnostics",
                )
                table = s["tracks"]
            require(
                text(d["observation_id"]) and d["observation_id"] not in table,
                "observation ID reused",
            )
            known_photo(d["photo"])
            table[d["observation_id"]] = copy.deepcopy(d)
            return {
                "status": "source_saved",
                "reason": "no_equipment_attachment_inferred",
            }
        if kind == "nominate_context":
            fields(
                "candidate_id",
                "expected_candidate_revision",
                "actor",
                "reason",
                "photo",
                "alternatives",
                "considered_contradictions",
            )
            c = candidate(d["candidate_id"], d["expected_candidate_revision"])
            require(
                text(d["actor"]) and text(d["reason"]),
                "explicit context reviewer required",
            )
            current_photo(d["photo"])
            require(
                isinstance(d["alternatives"], list)
                and len(set(d["alternatives"])) == len(d["alternatives"])
                and all(
                    x in s["candidates"] and x != c["id"] for x in d["alternatives"]
                ),
                "inspectable alternatives",
            )
            require(
                isinstance(d["considered_contradictions"], list)
                and sorted(d["considered_contradictions"])
                == sorted(c["contradictions"]),
                "explicitly consider outstanding contradictions",
            )
            invalidate(c["id"], "context_changed")
            c.update(
                revision=c["revision"] + 1,
                status="context_nominated",
                nomination_id=eid,
                contradictions=[],
            )
            s["nominations"][eid] = {
                **copy.deepcopy(d),
                "candidate_revision": c["revision"],
                "scope": copy.deepcopy(s["scope"]),
            }
            return {
                "status": "context_nominated",
                "candidate_revision": c["revision"],
                "nomination_id": eid,
                "reason": "label_specific_review_still_required",
            }
        if kind == "request_attachment":
            fields(
                "ticket_id", "candidate_id", "label_id", "expected_candidate_revision"
            )
            c = candidate(d["candidate_id"], d["expected_candidate_revision"])
            require(
                text(d["ticket_id"])
                and d["ticket_id"] not in s["tickets"]
                and d["label_id"] in s["labels"],
                "new ticket and saved label required",
            )
            # One authority per label/candidate/context; sibling tickets must not
            # re-admit an older decision after this pair was resolved elsewhere.
            require(
                not any(
                    t["candidate_id"] == c["id"]
                    and t["label_id"] == d["label_id"]
                    and t["candidate_revision"] == c["revision"]
                    for t in s["tickets"].values()
                ),
                "ticket already exists for candidate/label/context revision",
            )
            status = (
                "needs_review"
                if c["status"] == "context_nominated"
                else "needs_context"
            )
            ticket = {
                "id": d["ticket_id"],
                "candidate_id": c["id"],
                "label_id": d["label_id"],
                "candidate_revision": c["revision"],
                "nomination_id": c["nomination_id"],
                "scope": copy.deepcopy(s["scope"]),
                "revision": 1,
                "status": status,
                "reason": "review_label_and_context"
                if status == "needs_review"
                else "context_photo_required",
            }
            s["tickets"][ticket["id"]] = ticket
            return copy.deepcopy(ticket)
        if kind == "review_attachment":
            fields(
                "ticket_id",
                "expected_ticket_revision",
                "expected_candidate_revision",
                "decision",
                "actor",
                "reason",
                "photo",
            )
            require(
                d["ticket_id"] in s["tickets"]
                and d["decision"] in {"attach", "unresolved", "reject"}
                and text(d["actor"])
                and text(d["reason"]),
                "explicit label review required",
            )
            known_photo(d["photo"])
            ticket = s["tickets"][d["ticket_id"]]
            c = candidate(ticket["candidate_id"])
            fresh = (
                type(d["expected_ticket_revision"]) is int
                and type(d["expected_candidate_revision"]) is int
                and d["expected_ticket_revision"] == ticket["revision"]
                and d["expected_candidate_revision"] == c["revision"]
                and ticket["candidate_revision"] == c["revision"]
                and ticket["scope"] == s["scope"]
                and d["photo"]["scope"] == s["scope"]
                and ticket["nomination_id"] == c["nomination_id"]
                and c["status"] == "context_nominated"
                and ticket["status"] in {"needs_review", "unresolved"}
            )
            outcome = {
                "status": "stale_review",
                "reason": "context_scope_or_review_revision_changed",
            }
            conflicts = set()
            if fresh and d["decision"] == "attach":
                for link in s["attachments"].values():
                    other = candidate(link["candidate_id"])
                    if (
                        link["label_id"] == ticket["label_id"]
                        and other["id"] != c["id"]
                        and other["status"] == "context_nominated"
                        and other["revision"] == link["candidate_revision"]
                        and link["scope"] == s["scope"]
                    ):
                        conflicts.add(other["id"])
            if conflicts:
                # A later incompatible explicit claim does not silently replace the earlier one.
                # Both require context review; the original observation and decisions survive.
                for cid in conflicts | {c["id"]}:
                    disputed = candidate(cid)
                    disputed.update(
                        revision=disputed["revision"] + 1, status="contradicted"
                    )
                    disputed["contradictions"].append(eid)
                    invalidate(cid, "conflicting_current_assignment")
                outcome = {
                    "status": "unresolved",
                    "reason": "conflicting_current_assignment",
                }
                s["reviews"][eid] = {
                    **copy.deepcopy(d),
                    "outcome": copy.deepcopy(outcome),
                }
                return outcome
            if fresh:
                status = {
                    "attach": "attached",
                    "unresolved": "unresolved",
                    "reject": "rejected",
                }[d["decision"]]
                ticket.update(
                    status=status,
                    reason={
                        "attach": "explicit_contextual_review",
                        "unresolved": "association_unresolved",
                        "reject": "review_rejected",
                    }[d["decision"]],
                    revision=ticket["revision"] + 1,
                )
                outcome = {
                    "status": status,
                    "reason": ticket["reason"],
                    "ticket_revision": ticket["revision"],
                }
                if status == "attached":
                    s["attachments"][eid] = {
                        "id": eid,
                        "review_id": eid,
                        "ticket_id": ticket["id"],
                        "candidate_id": c["id"],
                        "label_id": ticket["label_id"],
                        "candidate_revision": c["revision"],
                        "nomination_id": c["nomination_id"],
                        "scope": copy.deepcopy(s["scope"]),
                    }
            s["reviews"][eid] = {**copy.deepcopy(d), "outcome": copy.deepcopy(outcome)}
            return outcome
        if kind == "contradict":
            fields(
                "candidate_id",
                "expected_candidate_revision",
                "actor",
                "reason",
                "photo",
            )
            c = candidate(d["candidate_id"], d["expected_candidate_revision"])
            require(
                text(d["actor"]) and text(d["reason"]), "contradiction source required"
            )
            known_photo(d["photo"])
            c.update(revision=c["revision"] + 1, status="contradicted")
            c["contradictions"].append(eid)
            invalidate(c["id"], "association_contradicted")
            return {
                "status": "contradicted",
                "reason": "context_review_required",
                "candidate_revision": c["revision"],
            }
        if kind == "reset_scope":
            fields("scope", "reason")
            key = scope_key(d["scope"])
            require(
                text(d["reason"]) and key not in s["seen_scopes"],
                "fresh reset scope and reason required",
            )
            new = d["scope"]
            old = s["scope"]
            seen = [json.loads(x) for x in s["seen_scopes"]]
            if new["session_id"] != old["session_id"]:
                require(
                    not any(x["session_id"] == new["session_id"] for x in seen),
                    "session ID cannot be resurrected",
                )
            elif new["epoch_id"] != old["epoch_id"]:
                require(
                    not any(
                        x["session_id"] == new["session_id"]
                        and x["epoch_id"] == new["epoch_id"]
                        for x in seen
                    ),
                    "coordinate epoch cannot be resurrected",
                )
            s["seen_scopes"].append(key)
            s["scope"] = copy.deepcopy(d["scope"])
            for c in s["candidates"].values():
                c.update(
                    revision=c["revision"] + 1,
                    status="needs_context",
                    nomination_id=None,
                )
                invalidate(c["id"], "scope_changed")
            return {
                "status": "needs_context",
                "reason": "fresh_context_after_reset_required",
            }
        raise Conflict("unsupported event; tracker continuity never attaches labels")

    def snapshot(self):
        with self._lock:
            s = copy.deepcopy(self._state)
        for attachment in s["attachments"].values():
            c = s["candidates"][attachment["candidate_id"]]
            current = (
                c["status"] == "context_nominated"
                and c["revision"] == attachment["candidate_revision"]
                and s["scope"] == attachment["scope"]
            )
            attachment.update(
                current=current,
                status="reviewed_current" if current else "historical_only",
            )
        for ticket in s["tickets"].values():
            c = s["candidates"][ticket["candidate_id"]]
            if ticket["status"] == "attached" and (
                ticket["scope"] != s["scope"]
                or ticket["candidate_revision"] != c["revision"]
            ):
                ticket.update(
                    status="historical_only", reason="current_context_requires_review"
                )
        return s

    def sidecar(self, scene_sha256, expected_snapshot_revision):
        """Proposed C1 adjunct only. No C1 object/admission/physical confidence emitted."""
        require(digest(scene_sha256), "exact C1 scene byte hash required")
        with self._lock:
            require(
                type(expected_snapshot_revision) is int
                and expected_snapshot_revision == self._state["revision"],
                "stale export snapshot",
            )
            s = self.snapshot()
        return {
            "schema": "equipment-association-sidecar/1",
            "scene_sha256": scene_sha256,
            "snapshot_revision": s["revision"],
            "scope": s["scope"],
            "coordinate_contract": "Original encoded photo top-left pixel-cell xywh; caller must preserve camera/image transform provenance",
            "candidates": s["candidates"],
            "label_observations": s["labels"],
            "track_observations": s["tracks"],
            "nominations": s["nominations"],
            "review_tickets": s["tickets"],
            "reviews": s["reviews"],
            "associations": s["attachments"],
            "events": s["events"],
            "not_established": [
                "physical equipment identity",
                "photo/exposure provenance",
                "OCR correctness",
                "measurement uncertainty",
                "C1 ingestion compatibility",
                "capture-admission completion",
            ],
        }
