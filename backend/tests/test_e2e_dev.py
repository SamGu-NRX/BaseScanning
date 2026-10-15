"""Contract check for the dev end-to-end result gate. No network."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_e2e_dev_default_timeout_covers_a_real_run() -> None:
    import scripts.e2e_dev as mod

    assert mod.REAL_RUN_CLIENT_WAIT_S == 1500
    assert mod.parser().parse_args([]).timeout == 1500
    assert mod.parser().parse_args(["--timeout", "180"]).timeout == 180


def test_e2e_dev_self_check() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    completed = subprocess.run(
        [sys.executable, "scripts/e2e_dev.py", "--self-check"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_e2e_dev_refuses_other_origins() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["CAPTURE_API_URL"] = "https://huntertcarver-prod--capture-api.modal.run"
    env["CAPTURE_API_TOKEN"] = "x" * 32
    completed = subprocess.run(
        [sys.executable, "scripts/e2e_dev.py", "--packet", "packet.json"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "https://huntertcarver-dev--capture-api.modal.run" in completed.stdout
    assert "SD-77" in completed.stdout


def test_e2e_dev_does_not_require_a_token() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["CAPTURE_API_URL"] = "https://huntertcarver-dev--capture-api.modal.run"
    env.pop("CAPTURE_API_TOKEN", None)
    completed = subprocess.run(
        [sys.executable, "scripts/e2e_dev.py", "--packet", "packet-does-not-exist.json"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 2, combined
    assert "CAPTURE_API_TOKEN" not in combined
    assert "Authorization" not in completed.stdout


def test_e2e_dev_omits_authorization_without_a_token() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scripts.e2e_dev import _request_headers; "
            "import json; "
            "print(json.dumps([_request_headers(''), _request_headers('secret-token')]))",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    import json

    empty, sent = json.loads(completed.stdout)
    assert "Authorization" not in empty
    assert sent["Authorization"] == "Bearer secret-token"


def test_signed_url_upload_omits_bearer_token() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scripts.e2e_dev import _request\n"
            "import json\n"
            "seen = []\n"
            "class _Resp:\n"
            "    status = 200\n"
            "    def read(self):\n"
            "        return b''\n"
            "    def __enter__(self):\n"
            "        return self\n"
            "    def __exit__(self, *args):\n"
            "        return False\n"
            "def _open(request, timeout):\n"
            "    seen.append({k: v for k, v in request.header_items()})\n"
            "    return _Resp()\n"
            "import scripts.e2e_dev as mod\n"
            "mod.urllib.request.urlopen = _open\n"
            "mod._request('https://storage.example/signed', 'secret-token', 'PUT', b'img', {'Content-MD5': 'abc'}, authorize=False)\n"
            "mod._request('https://huntertcarver-dev--capture-api.modal.run/v1/captures', 'secret-token', 'POST', b'{}')\n"
            "print(json.dumps(seen))\n",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    import json

    signed, api = json.loads(completed.stdout)
    assert "Authorization" not in signed
    assert signed.get("Content-md5") == "abc" or signed.get("Content-MD5") == "abc"
    assert api["Authorization"] == "Bearer secret-token"


def test_packet_write_timeout_covers_a_95_file_commit() -> None:
    import scripts.e2e_dev as mod

    assert mod.READ_TIMEOUT_S == 150
    assert mod.PACKET_WRITE_TIMEOUT_S >= 600
    assert mod.PACKET_WRITE_TIMEOUT_S > mod.READ_TIMEOUT_S
    assert mod.WRITE_ATTEMPTS >= 2


def test_create_payload_sends_fixture_meter() -> None:
    import scripts.e2e_dev as mod

    packet = {"packetId": "packet-0001", "session": {"tier": "photo_only"}}
    plain = mod.create_payload(packet)
    assert "fixtureMeter" not in plain
    sent = mod.create_payload(packet, "k00023:989.573,712.173", "reference_point")
    assert sent["fixtureMeter"] == {"meterFrames": "k00023:989.573,712.173", "anchorLabel": "reference_point"}
    try:
        mod.create_payload(packet, "", "reference_point")
    except SystemExit as exc:
        assert "meter-frames" in str(exc)
    else:
        raise AssertionError("anchor without frames should exit")


def test_commit_retries_the_same_body_after_a_timeout(monkeypatch) -> None:
    import scripts.e2e_dev as mod

    calls: list[tuple] = []

    def fake_json(url, token, method, payload=None, headers=None, timeout=mod.READ_TIMEOUT_S):
        calls.append((url, timeout, payload))
        commits = [item for item in calls if str(item[0]).endswith("files:commit")]
        if len(commits) == 1:
            raise TimeoutError("timed out")
        return {"committed": ["a.jpg"], "notFound": [], "mismatch": []}

    monkeypatch.setattr(mod, "_json", fake_json)
    monkeypatch.setattr(mod, "_pause", lambda _seconds: None)
    result = mod.commit_batch("https://api", "", "cap", [{"path": "a.jpg", "sha256": "ab"}])
    assert result["committed"] == ["a.jpg"]
    commits = [item for item in calls if str(item[0]).endswith("files:commit")]
    assert len(commits) == 2
    assert commits[0][1] == mod.PACKET_WRITE_TIMEOUT_S
    assert commits[0][2] == commits[1][2]


def test_finalize_does_not_repost_after_a_timeout_once_a_run_exists(monkeypatch) -> None:
    import scripts.e2e_dev as mod

    posts: list[bytes] = []

    def fake_request(url, token, method, body=None, headers=None, authorize=True, timeout=mod.READ_TIMEOUT_S):
        posts.append(body or b"")
        raise TimeoutError("timed out")

    def fake_json(url, token, method, payload=None, headers=None, timeout=mod.READ_TIMEOUT_S):
        assert method == "GET"
        return {"status": "processing", "runId": "run_existing"}

    monkeypatch.setattr(mod, "_request", fake_request)
    monkeypatch.setattr(mod, "_json", fake_json)
    monkeypatch.setattr(mod, "_pause", lambda _seconds: None)
    accepted = mod.finalize_capture("https://api", "", "cap", b"{}", "packet-0001")
    assert accepted["runId"] == "run_existing"
    assert len(posts) == 1


def test_finalize_retries_the_same_packet_while_uploading(monkeypatch) -> None:
    import scripts.e2e_dev as mod

    bodies: list[bytes] = []

    def fake_request(url, token, method, body=None, headers=None, authorize=True, timeout=mod.READ_TIMEOUT_S):
        bodies.append(body or b"")
        if len(bodies) == 1:
            raise TimeoutError("timed out")
        assert timeout == mod.PACKET_WRITE_TIMEOUT_S
        return 202, b'{"status":"processing","runId":"run_new","missing":[]}'

    def fake_json(url, token, method, payload=None, headers=None, timeout=mod.READ_TIMEOUT_S):
        return {"status": "uploading", "runId": None}

    monkeypatch.setattr(mod, "_request", fake_request)
    monkeypatch.setattr(mod, "_json", fake_json)
    monkeypatch.setattr(mod, "_pause", lambda _seconds: None)
    accepted = mod.finalize_capture("https://api", "", "cap", b'{"packetId":"packet-0001"}', "packet-0001")
    assert accepted["runId"] == "run_new"
    assert bodies[0] == bodies[1]
