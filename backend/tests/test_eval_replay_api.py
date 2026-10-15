"""Replay uses the shipped API contract, beyond the fake-server retry tests."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from requests import Response

from server.api.tests.conftest import client as api_client_fixture
from server.api.tests.conftest import photo_packet
from server.api.tests.test_ingest import _create
from server.contracts.packet import Packet
from server.contracts.result import ResultResponse
from server.eval.replay import ReplayError, ReplayRun, _download_result, _poll_events, plan_uploads, replay


class LocalSession:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def request(self, method, url, **kwargs):
        kwargs.pop("timeout", None)
        if "data" in kwargs:
            kwargs["content"] = kwargs.pop("data")
        response = self.client.request(method, url, **kwargs)
        self.calls.append((method, url, response.status_code))
        return response


@pytest.fixture
def local_session(tmp_path):
    client = api_client_fixture.__wrapped__(tmp_path / "api")
    client.app_state.settings.HSS_AUTH_MODE = "none"
    client.headers.pop("Authorization", None)
    return LocalSession(client)


@pytest.fixture
def photo_folder(tmp_path):
    folder = tmp_path / "packet"
    (folder / "stills").mkdir(parents=True)
    blob = b"synthetic-replay-image"
    (folder / "stills/s1.jpg").write_bytes(blob)
    (folder / "packet.json").write_text(
        json.dumps(photo_packet("packet-replay-current-api", [("stills/s1.jpg", blob)]))
    )
    return folder


def test_photo_replay_commits_and_resumes(local_session, photo_folder):
    first = replay(photo_folder, "http://testserver", "", rate=0, until="processing", session=local_session)
    assert first.errors == []
    assert first.files["committed"] == 1
    puts = sum(method == "PUT" for method, _, _ in local_session.calls)
    second = replay(photo_folder, "http://testserver", "", rate=0, until="processing", session=local_session)
    assert second.captureId == first.captureId
    assert second.runId == first.runId
    assert second.files["committed"] == 1
    assert sum(method == "PUT" for method, _, _ in local_session.calls) == puts


def test_resume_committed_files_before_finalize(local_session, photo_folder, monkeypatch):
    def interrupted(*args, **kwargs):
        raise ReplayError("synthetic interruption", 2)

    with monkeypatch.context() as patch:
        patch.setattr("server.eval.replay._finalize", interrupted)
        with pytest.raises(ReplayError, match="synthetic interruption"):
            replay(photo_folder, "http://testserver", "", rate=0, until="processing", session=local_session)
    puts = sum(method == "PUT" for method, _, _ in local_session.calls)
    resumed = replay(photo_folder, "http://testserver", "", rate=0, until="processing", session=local_session)
    assert resumed.errors == []
    assert resumed.files["committed"] == 1
    assert sum(method == "PUT" for method, _, _ in local_session.calls) == puts


@pytest.mark.parametrize("finalize_at", ["last", "early"])
def test_lidar_replay_commits_every_manifest_file(local_session, finalize_at):
    folder = Path("ios-kit/fixtures/mini-arkit-lidar")
    packet = json.loads((folder / "packet.json").read_text())
    Packet.model_validate(packet)
    expected = {entry["path"] for entry in packet["files"]}
    assert all((folder / path).stat().st_size < 5_000_000 for path in expected)
    local_session.client.app_state.settings.HSS_INGEST_MAX_BATCH = 7
    run = replay(
        folder,
        "http://testserver",
        "",
        rate=0,
        until="processing",
        finalize_at=finalize_at,
        timeout_s=20,
        session=local_session,
    )
    assert run.errors == []
    assert run.files["committed"] == len(expected) == 29
    state = local_session.client.app_state.state
    assert state.get_capture(run.captureId).status == "processing"
    assert {rec.path for rec in state.files(run.captureId) if rec.state == "committed"} == expected
    paths = [item.path for item in plan_uploads(packet).files]
    for frame in packet["keyframes"]:
        if frame.get("depth"):
            index = paths.index(frame["img"])
            assert paths[index + 1 : index + 3] == [frame["depth"]["file"], frame["depth"]["confidenceFile"]]


def test_manifest_only_resource_is_included():
    packet = photo_packet("packet-extra-file", [])
    packet["files"].append({"path": "extra/resource.bin", "role": "other", "bytes": 1, "sha256": "a" * 64})
    assert [item.path for item in plan_uploads(packet).files] == ["extra/resource.bin"]


def test_downloads_contract_verdict_url(tmp_path):
    payload = ResultResponse(
        runId="run_replay_test",
        status="manual_review",
        viewsNeeded=[],
        memberActions=[],
        outcome=None,
        verdictUrl="https://artifact.example/verdict.json",
    ).model_dump(mode="json")
    verdict = {"evaluator": {"rules_profile": "C"}, "synthetic": True}
    calls = []

    class Session:
        def request(self, method, url, **kwargs):
            calls.append((method, url))
            response = Response()
            response.status_code = 200
            response._content = json.dumps(payload if url.endswith("/result") else verdict).encode()
            return response

    run = ReplayRun(captureId="cap_replay_test")
    _download_result(Session(), "https://api.example", run, {}, tmp_path, lambda _: None, float("inf"))
    assert calls == [
        ("GET", "https://api.example/v1/captures/cap_replay_test/result"),
        ("GET", payload["verdictUrl"]),
    ]
    assert json.loads(Path(run.verdict_paths["C"]).read_text()) == verdict


def test_retake_request_finishes_event_poll(local_session):
    capture_id = _create(local_session.client, "packet-replay-retake")
    state = local_session.client.app_state.state
    state.set_status(capture_id, "needs_views")
    state.append_event(capture_id, "retake_request", {"viewsNeeded": [], "memberActions": []})
    run = ReplayRun(captureId=capture_id)
    _poll_events(local_session, "http://testserver", run, {}, None, lambda _: None, time.monotonic() + 2)
    assert run.events[-1]["type"] == "retake_request"
    assert len(local_session.calls) == 1


def test_invalid_result_is_not_accepted(tmp_path):
    class Session:
        def request(self, *args, **kwargs):
            response = Response()
            response.status_code = 200
            response._content = b'{"unexpected": true}'
            return response

    with pytest.raises(ReplayError, match="result failed contract validation") as caught:
        _download_result(Session(), "https://api.example", ReplayRun(), {}, tmp_path, lambda _: None, float("inf"))
    assert caught.value.code == 4
