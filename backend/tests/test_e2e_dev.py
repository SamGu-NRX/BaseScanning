"""Contract check for the dev end-to-end result gate. No network."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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
