"""Response decoders against the published HouseScanKit types.

Each case states one published rule and the single result it allows.
"""

from __future__ import annotations

import pytest

from hunter_local_ingest import contract
from hunter_local_ingest.contract import (
    decode_commit,
    decode_create,
    decode_events,
    decode_finalize,
    decode_register,
    decode_result,
    parse_body,
)

PATHS = ["synthetic/scan00.bin", "synthetic/scan01.bin"]
DECLARED = frozenset(PATHS)
URL = "http://127.0.0.1:9000/upload/t"


def _target(path, url=URL, method="PUT", headers=None, state="pending"):
    return {
        "path": path,
        "state": state,
        "upload": {"method": method, "url": url, "headers": headers or {}},
    }


def test_published_status_sets_match_capture_api():
    # CaptureAPI.terminalStatuses / statusesWithoutOutcome, verbatim
    assert contract.TERMINAL_STATUSES == {
        "complete",
        "manual_review",
        "failed",
        "expired",
        "needs_views",
    }
    assert contract.STATUSES_WITHOUT_OUTCOME == {"failed", "expired"}
    assert contract.TERMINAL_STATUSES <= contract.CAPTURE_STATUSES


def test_create_requires_capture_id_and_published_status():
    assert decode_create({"captureId": "c1", "status": "uploading"}).ok
    assert "missing:captureId" in decode_create({"status": "uploading"}).violations
    assert "enum:status" in decode_create({"captureId": "c1", "status": "ready"}).violations
    assert "range:captureId" in decode_create({"captureId": "", "status": "uploading"}).violations
    limits = decode_create({"captureId": "c", "status": "uploading", "upload": {"maxBatch": -1}})
    assert "type:upload" in limits.violations
    assert (
        decode_create({"captureId": "c", "status": "uploading", "upload": {"maxBatch": 5}}).data[
            "maxBatch"
        ]
        == 5
    )


def test_register_matches_by_path_not_order():
    dec = decode_register(
        {"files": [_target(PATHS[1], URL + "1"), _target(PATHS[0], URL + "0")]}, PATHS, DECLARED
    )
    assert dec.ok
    assert dec.data["targets"][PATHS[0]]["url"] == URL + "0"
    assert dec.data["targets"][PATHS[1]]["url"] == URL + "1"


@pytest.mark.parametrize(
    ("files", "violation"),
    [
        ([_target(PATHS[0])], "not-acknowledged:files"),
        ([_target(PATHS[0]), _target(PATHS[1]), _target(PATHS[0])], "duplicate:files"),
        (
            [_target(PATHS[0]), _target(PATHS[1]), _target("synthetic/scan02.bin")],
            "unexpected-path:files",
        ),
        ([_target(PATHS[0], method="POST"), _target(PATHS[1])], "upload_method"),
        ([_target(PATHS[0], url="http://example.org/u"), _target(PATHS[1])], "upload_url_insecure"),
        ([_target(PATHS[0], url="not a url"), _target(PATHS[1])], "upload_url_invalid"),
        ([_target(PATHS[0], headers={"X": 1}), _target(PATHS[1])], "type:headers"),
        ([{"path": PATHS[0], "state": "pending"}, _target(PATHS[1])], "upload_method"),
    ],
)
def test_register_strict_cases(files, violation):
    dec = decode_register({"files": files}, PATHS, DECLARED)
    assert violation in dec.violations


def test_register_committed_state_needs_no_target_and_signed_headers_are_reported():
    dec = decode_register(
        {
            "files": [
                _target(PATHS[0], state="committed"),
                _target(PATHS[1], headers={"Authorization": "x", "Content-Type": "y"}),
            ]
        },
        PATHS,
        DECLARED,
    )
    assert dec.ok
    assert dec.data["committed"] == [PATHS[0]]
    upload = dec.public["files"][1]["upload"]
    assert upload["signs_auth_header"] is True and upload["signed_content_type"] is True
    assert "url" not in upload and "headers" not in upload


def test_commit_requires_all_three_lists_and_only_requested_paths():
    ok = decode_commit({"committed": PATHS, "notFound": [], "mismatch": []}, PATHS, DECLARED)
    assert ok.ok
    assert (
        "missing:mismatch"
        in decode_commit({"committed": [], "notFound": []}, PATHS, DECLARED).violations
    )
    dup = decode_commit(
        {"committed": [PATHS[0], PATHS[0]], "notFound": [], "mismatch": []}, PATHS, DECLARED
    )
    assert "duplicate:committed" in dup.violations
    other = decode_commit({"committed": [], "notFound": ["x"], "mismatch": []}, PATHS, DECLARED)
    assert "unexpected-path:notFound" in other.violations


def test_finalize_requires_status_missing_and_run_id():
    assert decode_finalize({"status": "processing", "missing": [], "runId": "r"}, DECLARED).ok
    for key in ("status", "missing", "runId"):
        body = {"status": "processing", "missing": [], "runId": "r"}
        del body[key]
        assert f"missing:{key}" in decode_finalize(body, DECLARED).violations
    bad_eta = decode_finalize(
        {"status": "processing", "missing": [], "runId": "r", "etaS": 1.5}, DECLARED
    )
    assert "type:etaS" in bad_eta.violations


def test_events_ordering_and_identity():
    good = decode_events(
        {
            "status": "complete",
            "next": 3,
            "events": [{"seq": 3, "type": "verdict_ready", "at": "t", "data": {"runId": "r1"}}],
        },
        after=2,
        run_id="r1",
    )
    assert good.ok and good.data["next"] == 3
    assert (
        "order:next"
        in decode_events(
            {"status": "processing", "next": 1, "events": []}, after=2, run_id="r1"
        ).violations
    )
    assert (
        "order:seq"
        in decode_events(
            {
                "status": "processing",
                "next": 5,
                "events": [{"seq": 2, "type": "failed", "at": "t"}],
            },
            after=2,
            run_id="r1",
        ).violations
    )
    other = decode_events(
        {
            "status": "complete",
            "next": 3,
            "events": [{"seq": 3, "type": "verdict_ready", "at": "t", "data": {"runId": "r2"}}],
        },
        after=2,
        run_id="r1",
    )
    assert "verdict-for-another-run" in other.violations
    retry = decode_events(
        {
            "status": "processing",
            "next": 1,
            "events": [{"seq": 1, "type": "failed", "at": "t", "data": {"next": "retry_finalize"}}],
        },
        after=0,
        run_id="r1",
    )
    assert retry.ok and retry.data["retry_finalize"] is True
    assert (
        "type:next"
        in decode_events(
            {"status": "processing", "next": True, "events": []}, after=0, run_id=None
        ).violations
    )


def _result(**over):
    body = {
        "runId": "r1",
        "status": "complete",
        "viewsNeeded": [],
        "memberActions": [],
        "outcome": {
            "kind": "eligible",
            "profile": "p",
            "message": "m",
            "viewsNeeded": [],
            "reasons": [],
        },
    }
    body.update(over)
    return body


def test_result_decodes_like_the_app():
    dec = decode_result(_result())
    assert dec.ok and dec.data["runId"] == "r1" and dec.data["outcome_present"]
    assert (
        "missing:viewsNeeded"
        in decode_result({k: v for k, v in _result().items() if k != "viewsNeeded"}).violations
    )
    assert (
        "missing:memberActions"
        in decode_result({k: v for k, v in _result().items() if k != "memberActions"}).violations
    )
    assert (
        "enum:kind"
        in decode_result(_result(outcome={**_result()["outcome"], "kind": "maybe"})).violations
    )
    assert (
        "type:reasons"
        in decode_result(
            _result(outcome={**_result()["outcome"], "reasons": [{"text": 1}]})
        ).violations
    )
    assert "enum:status" in decode_result(_result(status="done")).violations
    null = decode_result(_result(outcome=None, status="failed"))
    assert null.ok and not null.data["outcome_present"]


@pytest.mark.parametrize("status", ["manual_review", "needs_views"])
def test_result_terminal_statuses_with_outcome(status):
    assert decode_result(_result(status=status)).ok


def test_result_public_view_never_keeps_homeowner_text_or_urls():
    secret = "homeowner-text-" + "x" * 8
    dec = decode_result(
        _result(
            outcome={
                **_result()["outcome"],
                "message": secret,
                "reasons": [{"criterion": "c", "text": secret}],
            },
            previewUrl=f"https://s.example/{secret}",
            viewsNeeded=[
                {
                    "id": "v",
                    "kind": "k",
                    "criteria": [],
                    "why": secret,
                    "promptId": "p",
                    "prompt": {"title": secret, "body": secret},
                }
            ],
        )
    )
    assert dec.ok
    assert secret not in repr(dec.public)
    assert dec.public["has_preview_url"] is True and dec.public["viewsNeeded"] == 1


def test_bodies_that_are_not_objects():
    assert "shape:not-json" in decode_create(parse_body(b"<html>")).violations
    assert "shape:empty" in decode_create(parse_body(b"")).violations
    assert "shape:not-object" in decode_create(parse_body(b"[1]")).violations
