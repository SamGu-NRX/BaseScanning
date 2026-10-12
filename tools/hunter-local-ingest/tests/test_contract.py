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


# -- every present published nested field, with Swift Codable semantics ---------
#
# CaptureResult.Response decodes with JSONDecoder: a non-optional field
# rejects both missing and null, an optional one accepts missing and null but
# rejects any other wrong type. Unknown extra keys are ignored by Swift, so
# they are not violations here either.


def _view(**over):
    view = {
        "id": "v",
        "kind": "wall_band",
        "criteria": ["c1"],
        "why": "w",
        "promptId": "p",
        "prompt": {"title": "t", "body": "b"},
        "band": "low",
        "wallId": "w1",
        "target": "meter",
        "referenceCrop": {"image": "img", "boxPx": [0, 0, 10.5, 10]},
    }
    view.update(over)
    return view


def _complete(**over):
    """A result with every published optional field present and valid."""
    body = {
        "runId": "r1",
        "status": "complete",
        "viewsNeeded": [_view()],
        "memberActions": ["call"],
        "outcome": {
            "kind": "eligible",
            "profile": "p",
            "message": "m",
            "viewsNeeded": [_view()],
            "reasons": [
                {
                    "criterion": "c",
                    "text": "t",
                    "measuredFt": 3,
                    "plusMinusFt": 0.5,
                    "thresholdFt": 3.0,
                    "comparison": "gte",
                }
            ],
            "arkitEpoch": "e1",
            "recommendedPlacement": {
                "wallId": "w1",
                "startSM": -1.2,
                "confidence": 0.5,
                "boxArkitWorld": {"pose": [1] * 16, "size": [0.7, 1.2, 0.3]},
            },
        },
        "criteria": [
            {
                "id": "c",
                "outcome": "pass",
                "unsureCause": "u",
                "measuredFt": 1,
                "plusMinusFt": 0.1,
                "thresholdFt": 1,
                "coverage": "full",
            }
        ],
        "previewUrl": "https://s.example/p.png",
        "reviewUrl": "/v1/review/1",
    }
    body.update(over)
    return body


def _with(path, value, base=None):
    body = base or _complete()
    node = body
    for key in path[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return body


_DELETE = object()


def test_complete_result_baseline_is_ok():
    dec = decode_result(_complete())
    assert dec.ok, dec.violations


@pytest.mark.parametrize(
    "path, value",
    [
        (("outcome", "recommendedPlacement"), 7),
        (("outcome", "recommendedPlacement", "wallId"), _DELETE),
        (("outcome", "recommendedPlacement", "startSM"), "1"),
        (("outcome", "recommendedPlacement", "startSM"), True),
        (("outcome", "recommendedPlacement", "confidence"), None),
        (("outcome", "recommendedPlacement", "boxArkitWorld"), 7),
        (("outcome", "recommendedPlacement", "boxArkitWorld", "pose"), [1, "a"]),
        (("outcome", "recommendedPlacement", "boxArkitWorld", "size"), _DELETE),
        (("outcome", "arkitEpoch"), 5),
        (("outcome", "reasons"), ["a string reason"]),
        (("outcome", "reasons", 0, "measuredFt"), "3"),
        (("outcome", "reasons", 0, "comparison"), 5),
        (("outcome", "viewsNeeded", 0, "criteria"), [7]),
        (("viewsNeeded", 0, "criteria"), [7]),
        (("viewsNeeded", 0, "band"), 5),
        (("viewsNeeded", 0, "wallId"), ["w"]),
        (("viewsNeeded", 0, "referenceCrop"), "img"),
        (("viewsNeeded", 0, "referenceCrop", "boxPx"), ["x"]),
        (("viewsNeeded", 0, "referenceCrop", "image"), _DELETE),
        (("viewsNeeded", 0, "prompt", "title"), None),
        (("criteria", 0, "outcome"), 5),
        (("criteria", 0, "measuredFt"), "x"),
        (("criteria", 0, "coverage"), 1),
        (("criteria", 0, "unsureCause"), 2),
        (("previewUrl",), 5),
        (("reviewUrl",), {"u": 1}),
        (("memberActions",), None),
        (("memberActions",), ["a", 1]),
        (("outcome", "profile"), None),
        (("runId",), 5),
        (("status",), None),
    ],
)
def test_result_rejects_what_swift_codable_rejects(path, value):
    dec = decode_result(_with(path, value))
    assert not dec.ok, (path, value)


@pytest.mark.parametrize(
    "path",
    [
        ("outcome", "recommendedPlacement"),
        ("outcome", "recommendedPlacement", "boxArkitWorld"),
        ("outcome", "arkitEpoch"),
        ("outcome", "reasons", 0, "measuredFt"),
        ("viewsNeeded", 0, "band"),
        ("viewsNeeded", 0, "referenceCrop"),
        ("criteria", 0, "coverage"),
        ("criteria",),
        ("previewUrl",),
        ("runId",),
    ],
)
def test_result_optionals_accept_null_and_missing(path):
    assert decode_result(_with(path, None)).ok, path
    assert decode_result(_with(path, _DELETE)).ok, path


def test_result_unknown_extra_keys_are_ignored_like_swift():
    body = _complete()
    body["outcome"]["recommendedPlacement"]["extra"] = {"anything": 1}
    body["viewsNeeded"][0]["extra"] = [1]
    assert decode_result(body).ok


def test_result_placement_values_never_reach_the_public_view():
    secret = "wall-" + "x" * 12
    dec = decode_result(_with(("outcome", "recommendedPlacement", "wallId"), secret))
    assert dec.ok and secret not in repr(dec.public)
