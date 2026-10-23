#!/usr/bin/env python3
"""One-command local capture API for reviewers, the iOS simulator and harness scripts.

`make local-api` (backend/) starts the real HTTP app `server.api.app:create_app`
under uvicorn, bound to loopback only, with generated tokens and a fresh HMAC
secret, and writes the session the iOS run and later packets read:

    backend/runs/local-api/session.json   (mode 0600; runs/ is gitignored)

The journey is the documented front door: create a capture, register files,
PUT bytes to the signed upload URLs, commit, finalize `packet.json`, long-poll
events, then read the result and the reviewer page — over a real TCP socket,
unlike `scripts/capture_flow_e2e.py`, which drives TestClient in process.

Loopback only: the server refuses any `--host` other than 127.0.0.1 or ::1, and
`CAPTURE_API_PUBLIC_URL` (which signs the upload URLs) is derived from the same
host, so it can never point a client at a routable address. The iOS simulator
shares the Mac's loopback, so it reaches 127.0.0.1 directly; a physical phone
does not and is out of scope.

Stand-in stages: `HSS_ORCH_STAGE_STUBS=1` runs every orchestrator stage as its
committed stub except `placement`, which is `stubs_exempt` and solves for real.
The launcher forces `HSS_ORCH_MODAL_REMOTE=0` — a developer's shell that exports
it as 1 must not send a 'local' run to deployed Modal functions, which cannot
see this process's memory state or volume — and refuses the other parent-shell
keys that would move work or bytes off this machine (REFUSED_PARENT_KEYS below).
Pass-through, without interpretation: `HSS_LOCAL_OUTCOME` and
`HSS_LOCAL_STAGE_DELAY_S` are honored by the stage stubs when the
burst11-bs-local-outcome-states packet lands; this script neither reads nor
changes them.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOOPBACK_HOSTS = {"127.0.0.1", "::1"}

# Twelve-stage graph (server/orchestrator/graph.py). With HSS_ORCH_STAGE_STUBS=1
# every one of these is a committed stub; `placement` alone is stubs_exempt.
STAND_IN_STAGES = (
    "validate",
    "poses",
    "objects",
    "reads",
    "scale",
    "dense",
    "coverage",
    "scene",
    "objects_3d",
    "criteria",
    "result",
)
REAL_STAGES = ("placement",)

SESSION_DEFAULT = ROOT / "runs" / "local-api"


def _git_sha() -> str:
    env = os.environ.get("GIT_SHA")
    if env:
        return env
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _listen(host: str, port: int) -> socket.socket:
    """Bind the listener before publishing credentials for this run."""

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((host, port))
        listener.listen(2048)
        listener.set_inheritable(True)
    except OSError:
        listener.close()
        raise
    return listener


def _public_url(host: str, port: int) -> str:
    host_part = f"[{host}]" if ":" in host else host
    return f"http://{host_part}:{port}"


def _port(value: str) -> int:
    """Parse --port; refuse an empty value loudly instead of argparse's bare
    "invalid int value" (a rule that expands an unset or empty $(PORT) here)."""
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError(
            "refusing an empty --port: pass a port number (0 picks a free one); "
            "make local-api supplies the documented default (8000)"
        )
    try:
        return int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid port {value!r}: pass a number (0 picks a free one)") from exc


def _write_session(path: Path, session: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(session, handle, indent=2)
        handle.write("\n")
    os.chmod(path, 0o600)


def _wait_health(base: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/v1/healthz", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.25)
    return False


def _announce(base: str, session_path: Path, timeout_s: float) -> None:
    """Print the banner once /v1/healthz answers. Runs on a side thread while
    uvicorn serves on the main thread, so the tokens stay off the terminal."""

    def _ready() -> None:
        if _wait_health(base, timeout_s):
            standins = ", ".join(STAND_IN_STAGES)
            print(f"\nlocal capture API ready: {base}", flush=True)
            print(f"session file: {session_path}", flush=True)
            print(f"stand-in stages: {standins}", flush=True)
            print(f"real stage: {', '.join(REAL_STAGES)}", flush=True)
            print("the app and reviewer tokens are in the session file only, never on stdout", flush=True)
        else:
            print(f"\nlocal capture API failed its health check within {timeout_s:.0f}s; stopping", flush=True)
            os._exit(1)

    threading.Thread(target=_ready, daemon=True).start()


def _local_env_overrides(
    *,
    app_token: str,
    reviewer_token: str,
    hmac_secret: str,
    git_sha: str,
    base: str,
    volume_root: Path,
) -> dict[str, str]:
    """The env a local run forces, wherever the developer's shell disagrees.

    Every value pins where work runs and where bytes go: memory state, the
    local volume, loopback-only URLs, and local dispatch — HSS_ORCH_MODAL_REMOTE=0
    (the Greptile P1 raised on #293: with the inherited value 1 a 'local' run
    dispatched to deployed Modal functions that cannot see this process's memory
    state or volume, and remote-on also flips state to a Modal Dict and storage
    to GCS via server/common/deploy_switch.py shared_remote_backends).
    """
    return {
        "SMP_ENVIRONMENT": "dev",
        "HSS_AUTH_MODE": "static",
        "CAPTURE_API_TOKENS": f"device:{app_token},reviewer:local-review:{reviewer_token}",
        "UPLOAD_URL_HMAC_SECRET": hmac_secret,
        "STATE_BACKEND": "memory",
        "STORAGE_BACKEND": "volume",
        "VOLUME_ROOT": str(volume_root),
        "CAPTURE_API_PUBLIC_URL": base,
        "HSS_INGEST_HOOKS": "auto",
        "HSS_ORCH_STAGE_STUBS": "1",
        "HSS_ORCH_PREWARM": "0",
        "HSS_ORCH_MODAL_REMOTE": "0",
        "VISION_EXTERNAL_CALLS": "off",
        "GIT_SHA": git_sha,
    }


# Parent-shell keys a local run refuses, each because it would move work or
# bytes off this machine or sabotage the run: Modal credentials and app
# targeting (any code path reaching for Modal would authenticate to the
# deployed app), the shared Modal Dict and packet-cache names (state and bytes
# would leave the local volume), and the fault-injection hooks a stray export
# would turn into a mysterious local failure or hang. The value is the
# one-line reason a refusal names.
REFUSED_PARENT_KEYS: dict[str, str] = {
    "MODAL_TOKEN_ID": "Modal credentials a Modal call would authenticate with",
    "MODAL_TOKEN_SECRET": "Modal credentials a Modal call would authenticate with",
    "MODAL_IDENTITY_TOKEN": "a Modal identity token that authenticates this process",
    "MODAL_APP_NAME": "names the deployed Modal app a Modal call would target",
    "MODAL_ENVIRONMENT": "selects which Modal environment the Modal client targets",
    "MODAL_TASK_ID": "marks the process as inside Modal and flips packet-cache behavior",
    "STATE_DICT_NAME": "names the shared Modal Dict state would land in",
    "HSS_PACKET_CACHE": "redirects packet-cache bytes away from the local volume",
    "HSS_ORCH_TEST_FAIL_STAGE": "injects a stage failure into the run",
    "HSS_ORCH_TEST_HANG_STAGE": "injects a stage hang into the run",
    "HSS_ORCH_TEST_KILL_COORDINATOR_AFTER": "kills the coordinator mid-run",
}


def _refused_parent_keys(parent: dict[str, str] | None = None) -> list[str]:
    """Parent-shell keys this launcher refuses, in REFUSED_PARENT_KEYS order."""
    env = os.environ if parent is None else parent
    return [key for key in REFUSED_PARENT_KEYS if key in env]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1", help="bind address; loopback only (127.0.0.1 or ::1)")
    parser.add_argument("--port", type=_port, default=8000, help="port (0 picks a free one)")
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=SESSION_DEFAULT,
        help="directory for session.json and the volume root (default: backend/runs/local-api)",
    )
    parser.add_argument("--health-timeout", type=float, default=60.0, help="seconds to wait for /v1/healthz")
    args = parser.parse_args(argv)

    if args.host not in LOOPBACK_HOSTS:
        print(
            f"refusing to bind {args.host!r}: the local capture API serves loopback only "
            f"(127.0.0.1 or ::1). A phone that is not the simulator cannot reach this server anyway.",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    try:
        listener = _listen(args.host, args.port)
    except OSError as exc:
        print(f"could not bind local capture API on {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    port = int(listener.getsockname()[1])
    base = _public_url(args.host, port)

    # Fresh per-run credentials: a device bearer for the app and harness, an
    # explicit reviewer bearer for the review page (server/api/auth.py reads
    # `reviewer:<label>:<token>`; tokens must be at least 32 chars).
    app_token = secrets.token_urlsafe(32)
    reviewer_token = secrets.token_urlsafe(32)
    hmac_secret = secrets.token_hex(32)
    git_sha = _git_sha()

    session_dir = args.session_dir
    volume_root = session_dir / "volume"
    session_path = session_dir / "session.json"
    session = {
        "baseUrl": f"{base}/v1",
        "appToken": app_token,
        "reviewerToken": reviewer_token,
        "pid": os.getpid(),
        "gitSha": git_sha,
        "stageStubs": True,
        "startedAt": datetime.now(UTC).isoformat(),
    }
    _write_session(session_path, session)

    os.environ.update(
        _local_env_overrides(
            app_token=app_token,
            reviewer_token=reviewer_token,
            hmac_secret=hmac_secret,
            git_sha=git_sha,
            base=base,
            volume_root=volume_root,
        )
    )
    refused = _refused_parent_keys()
    for key in refused:
        os.environ.pop(key, None)
    if refused:
        reasons = "; ".join(f"{key} ({REFUSED_PARENT_KEYS[key]})" for key in refused)
        print(f"refusing inherited shell env: {reasons}", file=sys.stderr, flush=True)
    # Webhooks stay off: no outbound target, no signing key.
    os.environ.pop("BASE_WEBHOOK_URL", None)
    os.environ.pop("WEBHOOK_HMAC_SECRET", None)
    # HSS_LOCAL_OUTCOME and HSS_LOCAL_STAGE_DELAY_S pass through untouched.

    config = uvicorn.Config(
        "server.api.app:create_app",
        factory=True,
        host=args.host,
        port=port,
        log_level="info",
    )
    _announce(base, session_path, args.health_timeout)
    uvicorn.Server(config).run(sockets=[listener])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
