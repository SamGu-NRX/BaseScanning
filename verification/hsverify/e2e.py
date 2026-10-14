"""Send scenes through the placement server's HTTP API and judge every answer.

    uv run python -m hsverify.e2e --server-ref origin/t3/server            # cases/ + real scenes
    uv run python -m hsverify.e2e --server-url http://127.0.0.1:8000 --scene path/to/scene.json

The server under test is either started from a git ref (a detached worktree under /tmp,
`uv sync --locked`, then uvicorn on a free port) or reached at `--server-url`. Its scene
endpoint is read from its OpenAPI document; with more than one candidate the run stops and
asks for `--endpoint` instead of guessing.

A server started here is killed past 3.5 GB. Scenes go as the app sends them: JSON when the
endpoint takes it, a zip only for the compressed-scene hostile input. Bundles read from disk
stay within fixed entry and size limits, checked before reading.

Each scene is first validated against the scene schema published at the same ref, so a bad
input is reported as an input problem, not a server bug. Each response must be a JSON object
that validates against the result schema (anything else is a failure in the report, not a
crash) and passes the invariants in `resultcheck`, with rules read from the ref's `rules.yaml`.
Case files add the outcomes their geometry forces. Every scene is also resent
changed, and the answers must stay ordered: again (identical apart from timing), mirrored (same
decision and outcome lengths), without or with less coverage, with ground trimmed just short
of the largest clearance, with more error or tape re-measured by tap (nothing improves), and
with every requested view added, up to three rounds (no check left unsure for coverage). Five
hostile inputs must be refused or answered within 10 s and 500 MB of server growth. Every
answer, a result or an error, must arrive whole within its deadline and within 4 MB, or it
counts as no answer.

A run claims the contract (`contract_ok`) only when at least one scene was answered and
checked and none broke it, and real-scene latency only from an HTTP 200 result.

Reports go to ~/house-scanning-data/reports/e2e/<run>/ (outside git; real scenes can
reference dataset images).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import http.client
import io
import json
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from hsverify import gitref, memory
from hsverify.memory import peak_rss_mb
from hsverify.resultcheck import (
    RuleSet,
    assumption_mismatches,
    comparable,
    coverage_blocks,
    expectation_problems,
    invariant_problems,
    less_coverage_problems,
    mirror_scene,
    more_error_problems,
    outcome_lengths,
    public_assumption_warnings,
    reported_rules,
    schema_errors,
    tape_to_tap,
    unobserved_checks,
    with_ground_short_of,
    with_less_coverage,
    with_more_error,
    with_requests_captured,
)

HERE = Path(__file__).resolve().parents[1]
DEFAULT_CASES = HERE / "e2e" / "cases"
DEFAULT_REPORTS = Path.home() / "house-scanning-data" / "reports" / "e2e"
SCENE_SCHEMA = "server/schemas/scene.schema.json"
RESULT_SCHEMA = "server/schemas/result.schema.json"
# The plan's S2 metric: a real-derived scene returns a result in under 1 s.
LATENCY_BUDGET_MS = 1000.0
MIRROR_LENGTH_TOL_FT = 0.5


# --- Inputs ----------------------------------------------------------------------------------


@dataclass
class SceneInput:
    name: str
    scene: dict
    raw: bytes  # exactly the bytes sent, so the server's input hash can be checked
    images: dict[str, bytes] = field(default_factory=dict)
    expect: dict | None = None
    rules_assumed: dict[str, float] = field(default_factory=dict)
    real: bool = False
    source: str = ""
    skip_reason: str | None = None
    app_export: bool = False  # exported by the iOS app (from a replay), the S4 end-to-end path
    app_sha: str | None = None  # the app commit that exported it, when known
    hostile: bool = False  # judged by judge_hostile: refused or answered within a budget
    bundle: bytes | None = None  # a prebuilt upload, sent as is (the compressed-scene input)


def load_app_export(path: Path) -> SceneInput:
    """A scene the iOS app exported: a scan.zip, or a Simulator report folder holding one (whose
    report.json names the app commit that produced it)."""
    app_sha = None
    if path.is_dir():
        run = json.loads((path / "report.json").read_text())
        if not run.get("app_export"):
            raise SystemExit(f"{path} has no app export; the run never reached the upload")
        app_sha = run["sha"]
        path = path / run["app_export"]
    item = load_input(path, real=True)
    item.app_export, item.app_sha = True, app_sha
    item.source = str(path)
    return item


# Limits on a scene bundle this harness reads into memory: the server's own scene.json cap, and
# room for a few hundred phone JPEGs, well under the shared Mac's ~4 GB per-process budget. A
# compressed bundle can declare anything, so reads stop at the limit whatever it declares.
MAX_BUNDLE_ENTRIES = 2000
MAX_SCENE_BYTES = 10 * 2**20
MAX_IMAGE_BYTES = 32 * 2**20
MAX_BUNDLE_BYTES = 512 * 2**20


def read_bounded(bundle: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    """An entry's bytes, refused when it declares or turns out to hold more than `limit`."""
    if info.file_size > limit:
        raise SystemExit(f"{info.filename}: declares {info.file_size} bytes, limit {limit}")
    with bundle.open(info) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise SystemExit(f"{info.filename}: holds more than {limit} bytes")
    return data


def read_bundle(path: Path) -> tuple[bytes, dict[str, bytes]]:
    """scene.json and the JPEGs of a C1 zip bundle, within the limits above."""
    with zipfile.ZipFile(path) as bundle:
        infos = [i for i in bundle.infolist() if not i.is_dir()]
        if len(infos) > MAX_BUNDLE_ENTRIES:
            raise SystemExit(f"{path}: {len(infos)} entries, limit {MAX_BUNDLE_ENTRIES}")
        declared = sum(i.file_size for i in infos)
        if declared > MAX_BUNDLE_BYTES:
            raise SystemExit(f"{path}: declares {declared} bytes, limit {MAX_BUNDLE_BYTES}")
        scenes = [i for i in infos if i.filename == "scene.json"]
        if len(scenes) != 1:
            raise SystemExit(
                f"{path}: expected exactly one canonical scene.json, found {len(scenes)}"
            )
        scene = scenes[0]
        raw = read_bounded(bundle, scene, MAX_SCENE_BYTES)
        images = {
            Path(i.filename).name: read_bounded(bundle, i, MAX_IMAGE_BYTES)
            for i in infos
            if i.filename.lower().endswith(".jpg")
        }
    return raw, images


def load_input(path: Path, real: bool = False) -> SceneInput:
    """A case file (scene + expect), a bare scene.json, or a C1 zip bundle."""
    if path.suffix == ".zip":
        raw, images = read_bundle(path)
        return SceneInput(path.stem, json.loads(raw), raw, images, real=real, source=str(path))
    data = json.loads(path.read_text())
    if "scene_path" in data and "expect" in data:
        # A committed case about a scene that stays outside git (dataset-derived).
        target = Path(data["scene_path"]).expanduser()
        if not target.exists():
            return SceneInput(
                data["id"],
                {},
                b"",
                source=str(path),
                skip_reason=f"{target} not built here; see the case's README",
            )
        item = load_input(target, real=bool(data.get("real", real)))
        item.name, item.expect = data["id"], data["expect"]
        item.rules_assumed = data.get("rules_assumed", {})
        item.source = data.get("source", str(path))
        return item
    if "scene" in data and "expect" in data:
        raw = json.dumps(data["scene"], indent=1).encode()
        return SceneInput(
            data.get("id", path.stem),
            data["scene"],
            raw,
            expect=data["expect"],
            rules_assumed=data.get("rules_assumed", {}),
            real=bool(data.get("real", real)),
            source=data.get("source", str(path)),
            skip_reason=data.get("skip_reason"),
        )
    return SceneInput(path.stem, data, path.read_bytes(), real=real, source=str(path))


# --- Server ----------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_app(server_dir: Path) -> str:
    """`module:variable` of the FastAPI app. Exactly one is required."""
    found = []
    for path in sorted(server_dir.glob("*.py")):
        for match in re.finditer(r"^(\w+)\s*=\s*FastAPI\(", path.read_text(), re.M):
            found.append(f"{path.stem}:{match.group(1)}")
    if len(found) != 1:
        raise SystemExit(
            f"Expected one `app = FastAPI(...)` in {server_dir}, found {found or 'none'}. "
            "Start the server yourself and pass --server-url."
        )
    return found[0]


# A server under test that passes this is killed and the run says so: the Mac is shared, and a
# careless server can parse a small hostile input into gigabytes.
SERVER_LIMIT_MB = 3500


@contextlib.contextmanager
def server_from_ref(sha: str, log_path: Path) -> Iterator[tuple[str, memory.TreeLimit]]:
    """The server at `sha` running its own FastAPI app on a free port, under a memory limit."""
    with gitref.detached_worktree(sha) as tree:
        server_dir = tree / "server"
        subprocess.run(["uv", "sync", "--locked", "--quiet"], cwd=server_dir, check=True)
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        target = ["uvicorn", find_app(server_dir), "--host", "127.0.0.1", "--port", str(port)]
        with log_path.open("w") as log:
            proc = subprocess.Popen(
                ["uv", "run", "--quiet", *target],
                cwd=server_dir,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                with memory.TreeLimit(proc, SERVER_LIMIT_MB, interval_s=0.2) as limit:
                    wait_until_up(url, proc)
                    yield url, limit
            finally:
                # SIGTERM lets uvicorn finish in-flight requests, and a server still solving a
                # hostile input would run on for minutes: kill it after 10 s.
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


def wait_until_up(url: str, proc: subprocess.Popen, timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"The server exited with code {proc.returncode}; see server.log.")
        try:
            urllib.request.urlopen(f"{url}/openapi.json", timeout=2).read()
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.5)
    raise SystemExit(f"The server did not answer at {url} within {timeout_s:.0f} s.")


@dataclass
class Endpoint:
    path: str
    content_type: str  # "application/json" or "multipart/form-data"
    file_field: str | None = None
    accepts_zip: bool = False  # also takes a raw application/zip body (a prebuilt bundle)


def discover_endpoint(openapi: dict, override: str | None = None) -> Endpoint:
    """The POST operation that takes a scene: JSON body, or a multipart upload of the bundle."""
    candidates = []
    for path, ops in openapi.get("paths", {}).items():
        post = ops.get("post", {})
        # The result is JSON; a POST declared to answer with something else (an SVG site
        # plan, say) is not the placement endpoint.
        answers = post.get("responses", {}).get("200", {}).get("content")
        if answers and "application/json" not in answers:
            continue
        body = post.get("requestBody", {}).get("content", {})
        for kind in ("application/json", "multipart/form-data"):
            if kind in body:
                candidates.append((path, kind, body[kind].get("schema", {})))
    if override:
        declared = [c for c in candidates if c[0] == override]
        # An endpoint that reads the raw request body declares no body schema; the caller has
        # named it, so it takes the scene as JSON.
        if not declared and "post" in openapi.get("paths", {}).get(override, {}):
            declared = [(override, "application/json", {})]
        if not declared:
            raise SystemExit(f"--endpoint {override} is not a POST in the server's OpenAPI")
        candidates = declared
    elif len(candidates) > 1:
        named = [c for c in candidates if re.search(r"scene|place|solve|placement", c[0])]
        candidates = named if len(named) == 1 else candidates
    # One path taking both a JSON body and an upload: use JSON, which is how the app sends
    # scene.json since t3/ios-mvf 657ab28 (the hosted API refuses bodies over 4.5 MB).
    if len({c[0] for c in candidates}) == 1 and len(candidates) > 1:
        candidates = [c for c in candidates if c[1] == "application/json"] or candidates[:1]
    if len(candidates) != 1:
        listed = ", ".join(f"{p} ({k})" for p, k, _ in candidates) or "none"
        raise SystemExit(f"Cannot tell which endpoint takes a scene: {listed}. Pass --endpoint.")
    path, kind, schema = candidates[0]
    body = openapi.get("paths", {}).get(path, {}).get("post", {}).get("requestBody", {})
    accepts_zip = "application/zip" in body.get("content", {})
    file_field = None
    if kind == "multipart/form-data":
        props = resolve_ref(openapi, schema).get("properties", {})
        binary = [
            k
            for k, v in props.items()
            if v.get("format") == "binary"
            or v.get("contentMediaType") == "application/octet-stream"
        ]
        if len(binary) != 1:
            raise SystemExit(f"{path}: expected one file field in the upload form, got {binary}")
        file_field = binary[0]
    return Endpoint(path, kind, file_field, accepts_zip)


def resolve_ref(openapi: dict, schema: dict) -> dict:
    ref = schema.get("$ref")
    if not ref:
        return schema
    node = openapi
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return node


def bundle_zip(item: SceneInput) -> bytes:
    if item.bundle is not None:
        return item.bundle
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("scene.json", item.raw)
        for name, data in item.images.items():
            z.writestr(name, data)
    return buf.getvalue()


# The most of any response body the harness reads, a result or an error. Not derived from any
# requirement: the largest result in the 20260927-102101 run against t3/server 1d1e7e1 was 16 KB
# (eth3d-facade), so this leaves room for 250 times that.
MAX_RESPONSE_BYTES = 4 * 2**20
READ_CHUNK_BYTES = 2**16


def post(
    url: str, endpoint: Endpoint, item: SceneInput, timeout_s: float = 60
) -> tuple[int | None, bytes, float]:
    """Send one scene and read the whole answer within `timeout_s`, headers and body together.

    Status None means no complete answer, and the payload then says why: the deadline passed,
    the body ran past MAX_RESPONSE_BYTES, it ended short of its Content-Length or chunk framing,
    or the connection failed. A socket timeout alone bounds
    each read, not the response. A bounded worker wait covers DNS as well as TCP, TLS, headers
    and body; deadline expiry shuts down sockets already owned by the worker."""
    if item.bundle is not None and endpoint.accepts_zip:
        body, ctype = item.bundle, "application/zip"
    elif endpoint.content_type == "application/json":
        body, ctype = item.raw, "application/json"
    else:
        boundary = uuid.uuid4().hex
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{endpoint.file_field}"; '
                f'filename="scene.zip"\r\nContent-Type: application/zip\r\n\r\n'
            ).encode()
            + bundle_zip(item)
            + f"\r\n--{boundary}--\r\n".encode()
        )
        ctype = f"multipart/form-data; boundary={boundary}"
    target = urllib.parse.urlsplit(url + endpoint.path)
    https = target.scheme == "https"
    conn = http.client.HTTPConnection(
        target.hostname, port=target.port or (443 if https else 80), timeout=timeout_s
    )
    start = time.perf_counter()
    deadline = start + timeout_s
    expired = threading.Event()
    done = threading.Event()
    lock = threading.Lock()
    sockets: list[socket.socket] = []
    answer: list[tuple[int | None, bytes]] = []

    def remaining() -> float:
        left = deadline - time.perf_counter()
        if expired.is_set() or left <= 0:
            raise TimeoutError
        return left

    def expire() -> None:
        with lock:
            expired.set()
            for sock in sockets:
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
                sock.close()

    def connect() -> None:
        # getaddrinfo has no cancellable stdlib deadline. The daemon worker bounds the caller's
        # wait; a late DNS result must check the deadline before allocating or sending anything.
        addresses = socket.getaddrinfo(conn.host, conn.port, type=socket.SOCK_STREAM)
        error = None
        for family, kind, proto, _, address in addresses:
            with lock:
                remaining()
                sock = socket.socket(family, kind, proto)
                sockets.append(sock)  # own it before a blocking TCP connect or TLS handshake
            try:
                sock.settimeout(remaining())
                sock.connect(address)
                if https:
                    context = ssl.create_default_context()
                    with lock:
                        remaining()
                        sock = context.wrap_socket(
                            sock, server_hostname=conn.host, do_handshake_on_connect=False
                        )
                        sockets.append(sock)
                    sock.settimeout(remaining())
                    sock.do_handshake()
                sock.settimeout(remaining())
                conn.sock = sock
                return
            except OSError as exc:
                sock.close()
                error = exc
                remaining()
        if error is not None:
            raise error
        raise OSError("DNS returned no connection addresses")

    def exchange() -> None:
        status: int | None = None
        payload = bytearray()
        try:
            connect()
            path = target.path + (f"?{target.query}" if target.query else "")
            conn.sock.settimeout(remaining())
            conn.request("POST", path, body=body, headers={"Content-Type": ctype})
            conn.sock.settimeout(remaining())
            response = conn.getresponse()
            while len(payload) <= MAX_RESPONSE_BYTES and (chunk := response.read(READ_CHUNK_BYTES)):
                remaining()
                payload += chunk
            if len(payload) <= MAX_RESPONSE_BYTES and response.length:
                raise http.client.IncompleteRead(bytes(payload), response.length)
            remaining()
            status = response.status
        except TimeoutError:
            expired.set()
        except (OSError, http.client.HTTPException) as exc:
            status, payload = None, bytearray(f"{type(exc).__name__}: {exc}".encode())
        finally:
            conn.close()
            with lock:
                for sock in sockets:
                    sock.close()
            answer.append((status, bytes(payload)))
            done.set()

    # DNS may remain blocked after this returns. The worker is a daemon and owns no socket
    # during DNS; once lookup returns, remaining() prevents a late request from being sent.
    worker = threading.Thread(target=exchange, daemon=True)
    worker.start()
    if not done.wait(max(0.0, deadline - time.perf_counter())):
        expire()
    ms = (time.perf_counter() - start) * 1000
    if expired.is_set() or time.perf_counter() >= deadline:
        expire()
        return None, f"no answer within {timeout_s:g} s".encode(), ms
    status, payload = answer[0]
    if len(payload) > MAX_RESPONSE_BYTES:
        return None, f"HTTP {status} body over {MAX_RESPONSE_BYTES} bytes".encode(), ms
    return status, payload, ms


# --- Judging ---------------------------------------------------------------------------------


def variant(item: SceneInput, name: str, scene: dict) -> SceneInput:
    raw = json.dumps(scene, indent=1).encode()
    # Keep the bundle's images: a variant that still lists keyframes must still carry them.
    return SceneInput(
        f"{item.name}~{name}", scene, raw, images=item.images, real=item.real, source=item.source
    )


def parse_result(payload: bytes) -> dict | None:
    """The body as a JSON object, or None: a server's malformed answer is a failure to report,
    not a reason to stop the run before the report is written."""
    try:
        body = json.loads(payload)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def answer(url: str, endpoint: Endpoint, item: SceneInput) -> tuple[dict | None, str | None]:
    """The server's result for a scene, or why there is none."""
    status, payload, _ = post(url, endpoint, item)
    if status is None:
        return None, payload.decode(errors="replace")[:200]
    if status != 200:
        return None, f"HTTP {status}: {payload[:300]!r}"
    result = parse_result(payload)
    if result is None:
        return None, f"HTTP 200 with a body that is not a JSON object: {payload[:200]!r}"
    return result, None


def judge(
    url: str,
    endpoint: Endpoint,
    item: SceneInput,
    schemas: dict,
    rules: RuleSet,
    limit: memory.TreeLimit | None = None,
) -> dict:
    record: dict = {
        "name": item.name,
        "source": item.source,
        "real": item.real,
        "app_export": item.app_export,
        "app_sha": item.app_sha,
    }
    if item.skip_reason:
        return record | {"status": "skipped", "problems": [], "skip_reason": item.skip_reason}
    if item.hostile:
        return record | {"hostile": True} | judge_hostile(url, endpoint, item, schemas, limit)
    input_errors = schema_errors(item.scene, schemas["scene"])
    if input_errors:
        return record | {"status": "bad input", "problems": input_errors[:20]}

    status, payload, ms = post(url, endpoint, item)
    record |= {"http_status": status, "latency_ms": round(ms, 1)}
    if status != 200:
        failure = [
            f"HTTP {status} for a scene that validates: {payload[:500]!r}"
            if status is not None
            else payload.decode(errors="replace")[:200]
        ]
        return record | {"status": "fail", "problems": failure, "contract_problems": failure}
    result = parse_result(payload)
    if result is None:
        failure = [f"HTTP 200 with a body that is not a JSON object: {payload[:200]!r}"]
        return record | {"status": "fail", "problems": failure, "contract_problems": failure}
    record["result"] = result
    problems = [f"result schema: {e}" for e in schema_errors(result, schemas["result"])]
    if problems:
        return record | {"status": "fail", "problems": problems[:20], "contract_problems": problems}

    # Unreported overrides cannot establish rule-dependent geometry or case expectations.
    mismatches = assumption_mismatches(item.rules_assumed, result)
    warnings = public_assumption_warnings(item.rules_assumed, result, rules)
    active_rules = reported_rules(result, rules)
    contract = invariant_problems(
        item.scene, result, sent=item.raw, rules=None if warnings else active_rules
    )
    if not warnings:
        contract += property_problems(url, endpoint, item, result, schemas, active_rules)
    else:
        record["rule_checks_unverified"] = True
    if item.real and ms > LATENCY_BUDGET_MS:
        contract.append(f"real-derived scene took {ms:.0f} ms, budget {LATENCY_BUDGET_MS:.0f}")
    expected = (
        expectation_problems(item.expect, result)
        if item.expect and not mismatches and not warnings
        else []
    )
    problems = contract + expected

    record["decision"] = result["decision"]
    record["outcome_lengths_ft"] = outcome_lengths(result)
    if mismatches:
        record["assumption_mismatches"] = mismatches
    if warnings:
        record["assumption_warnings"] = warnings
    record["contract_problems"] = contract
    record["expectation_problems"] = expected
    record["problems"] = problems
    record["status"] = (
        "fail"
        if problems
        else "assumption mismatch"
        if mismatches
        else "assumptions unverified"
        if warnings
        else "pass"
    )
    return record


def property_problems(
    url: str, endpoint: Endpoint, item: SceneInput, result: dict, schemas: dict, rules: RuleSet
) -> list[str]:
    """The same scene resent unchanged and transformed. Every answer to a transformed scene must
    also satisfy the invariants, and the transforms that only lose information must not make
    the server surer."""
    problems: list[str] = []

    def resend(name: str, scene: dict) -> dict | None:
        transformed = variant(item, name, scene)
        other, failure = answer(url, endpoint, transformed)
        if failure:
            problems.append(f"{name}: {failure}")
            return None
        errors = schema_errors(other, schemas["result"])
        if errors:
            problems.append(f"{name}: result schema: {errors[0]}")
            return None
        problems.extend(
            f"{name}: {p}"
            for p in invariant_problems(
                scene, other, sent=transformed.raw, rules=reported_rules(other, rules)
            )
        )
        return other

    # 1. Same input, same answer.
    status, payload, _ = post(url, endpoint, item)
    again = parse_result(payload) if status == 200 else None
    if again is None or comparable(again) != comparable(result):
        problems.append("sending the same scene twice gave different results")

    # 2. Mirrored left to right: same decision, same amount of each outcome along the wall.
    mirrored = resend("mirrored", mirror_scene(item.scene))
    if mirrored is not None:
        if mirrored["decision"] != result["decision"]:
            problems.append(
                f"mirrored: decided {mirrored['decision']}, original {result['decision']}"
            )
        a, b = outcome_lengths(result), outcome_lengths(mirrored)
        for outcome in a:
            if abs(a[outcome] - b[outcome]) > MIRROR_LENGTH_TOL_FT:
                problems.append(
                    f"mirrored: {outcome} starts cover {b[outcome]:.2f} ft, "
                    f"original {a[outcome]:.2f} ft"
                )

    # 3. Less information never makes the server surer.
    if "coverage" in item.scene:
        bare = {k: v for k, v in item.scene.items() if k != "coverage"}
        if (other := resend("no coverage", bare)) is not None:
            problems += less_coverage_problems(result, other, "no coverage")
    for name, scene in (
        ("less coverage", with_less_coverage(item.scene)),
        ("ground short of the largest clearance", with_ground_short_of(item.scene, rules)),
    ):
        if scene is not None and (other := resend(name, scene)) is not None:
            problems += less_coverage_problems(result, other, name)
    for name, scene in (
        ("more error", with_more_error(item.scene, rules)),
        ("tape re-measured by tap", tape_to_tap(item.scene)),
    ):
        if scene is not None and (other := resend(name, scene)) is not None:
            problems += more_error_problems(result, other, name)

    # 4. Showing everything the result asked for settles every check that was unsure only
    # because an area was unobserved (the chosen spot may move; three rounds at most).
    scene, current = item.scene, result
    for round_ in range(1, 4):
        if not coverage_blocks(current):
            break
        scene = with_requests_captured(scene, current)
        if scene is None:
            break  # what remains needs a walk past an end, not another view
        current = resend(f"requests captured ({round_})", scene)
        if current is None:
            break
    else:
        if coverage_blocks(current):
            problems.append(
                "after capturing every requested view three times, checks are still unsure "
                f"for coverage: {unobserved_checks(current)}"
            )
    return problems


# --- Hostile inputs ---------------------------------------------------------------------------

# A refusal or an answer must come within this; an input that keeps the server busy longer is a
# denial-of-service risk. Not derived from any requirement: it is ten times the real-scene
# budget. S2 at 739fb6f answered the crowded input in 8.2 s on this Mac, close to the line.
HOSTILE_BUDGET_MS = 10_000.0


HOSTILE_PROBE_NAMES = (
    "hostile: 150 objects, each its own error",
    "hostile: coordinates of 1e300 ft",
    "hostile: a 5000 ft wall",
    "hostile: nested 5000 deep",
    "hostile: 400 MB scene.json in a small zip",
)


def hostile_inputs() -> list[SceneInput]:
    """Small requests that cost a careless server a lot. Built here, not stored, so the
    repository carries no blobs; none takes the harness more than a few MB to build."""
    base = {"meter": {"pos": [0.0, 4.5, 0.0], "wall_id": "w1"}}
    # Every object and coverage fragment adds boundaries, and each object its own error: a
    # solver that pairs every boundary with every error offset grows quadratically.
    crowded = base | {
        "walls": [{"id": "w1", "baseline": [[-60.0, 0.0], [60.0, 0.0]]}],
        "objects": [
            {
                "type": "elec_box",
                "wall_id": "w1",
                "span_ft": [-55 + 0.75 * i, -54.85 + 0.75 * i],
                "bottom_ft": 3.5,
                "top_ft": 4.5,
                "source": "tap",
                "plus_minus_ft": round(0.02 + 0.0013 * i, 4),
            }
            for i in range(150)
        ],
        "coverage": {
            "observed": [
                {"band": "wall", "span_ft": [-55.6 + 0.75 * i, -55.2 + 0.75 * i]}
                for i in range(150)
            ]
            + [{"band": "ground", "span_ft": [-60, 60], "out_ft": 15}]
        },
    }
    huge = {
        "meter": {"pos": [1e300, 4.5, 0.0], "wall_id": "w1"},
        "walls": [{"id": "w1", "baseline": [[-1e300, 0.0], [1e300, 0.0]]}],
    }
    long_wall = base | {
        "walls": [{"id": "w1", "baseline": [[-2500.0, 0.0], [2500.0, 0.0]]}],
        "coverage": {
            "observed": [
                {"band": "wall", "span_ft": [-2500, 2500]},
                {"band": "ground", "span_ft": [-2500, 2500], "out_ft": 20},
            ]
        },
    }
    nested = ('{"meter": ' + "[" * 5000 + "]" * 5000 + "}").encode()
    items = [
        SceneInput(HOSTILE_PROBE_NAMES[0], crowded, json.dumps(crowded).encode()),
        SceneInput(HOSTILE_PROBE_NAMES[1], huge, json.dumps(huge).encode()),
        SceneInput(HOSTILE_PROBE_NAMES[2], long_wall, json.dumps(long_wall).encode()),
        SceneInput(HOSTILE_PROBE_NAMES[3], {}, nested),
        SceneInput(HOSTILE_PROBE_NAMES[4], {}, b"", bundle=zip_bomb(400)),
    ]
    for item in items:
        item.hostile = True
    return items


def zip_bomb(megabytes: int) -> bytes:
    """A zip whose scene.json is `megabytes` of spaces around a number: under 1 MB to send,
    written in chunks so the harness never holds the expanded text."""
    buf = io.BytesIO()
    chunk = b" " * 2**20
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z, z.open("scene.json", "w") as f:
        f.write(b'{"meter": ')
        for _ in range(megabytes):
            f.write(chunk)
        f.write(b"1}")
    return buf.getvalue()


# A hostile input may cost the server this much memory above what it held before the request.
# Not derived from any requirement: S2 at 739fb6f grew by at most 186 MB on these inputs, and at
# 26d2870, before its input caps, by 557 to 1176 MB.
HOSTILE_MEMORY_MB = 500


def judge_hostile(
    url: str,
    endpoint: Endpoint,
    item: SceneInput,
    schemas: dict,
    limit: memory.TreeLimit | None = None,
) -> dict:
    """A refusal (400, 413, 422) or a valid result, either within the time budget and, for a
    server started here, the memory budget. A crash, a hang, a slow answer or a large
    allocation is a failure."""
    if (
        item.bundle is not None
        and endpoint.content_type == "application/json"
        and not (endpoint.accepts_zip)
    ):
        return {
            "status": "fail",
            "problems": ["large-input probe was not exercised: endpoint accepts no archive"],
            "contract_problems": [
                "large-input probe was not exercised: endpoint accepts no archive"
            ],
        }
    before = limit.reset() if limit else 0.0
    status, payload, ms = post(url, endpoint, item, timeout_s=HOSTILE_BUDGET_MS / 1000 + 5)
    record = {"http_status": status, "latency_ms": round(ms, 1), "hostile_probe_executed": True}
    grew = limit.peak_mb - before if limit else 0.0
    if limit:
        record["server_growth_mb"] = round(grew, 1)
    if grew > HOSTILE_MEMORY_MB:
        failure = f"the server grew by {grew:.0f} MB, budget {HOSTILE_MEMORY_MB} MB"
    elif status is None:
        failure = payload.decode(errors="replace")[:200]
    elif ms > HOSTILE_BUDGET_MS:
        failure = f"answered HTTP {status} after {ms:.0f} ms, budget {HOSTILE_BUDGET_MS:.0f}"
    elif status in (400, 413, 422):
        failure = None
    elif status == 200:
        result = parse_result(payload)
        if result is None:
            failure = f"HTTP 200 with a body that is not a JSON object: {payload[:200]!r}"
        else:
            errors = schema_errors(result, schemas["result"])
            failure = f"result schema: {errors[0]}" if errors else None
    else:
        failure = f"HTTP {status}: {payload[:200]!r}"
    problems = [failure] if failure else []
    return record | {
        "status": "fail" if problems else "pass",
        "problems": problems,
        "contract_problems": problems,
    }


# --- Report ----------------------------------------------------------------------------------


def write_report(out: Path, meta: dict, records: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    # A scene counts as checked only when the server answered it and the answer was judged; a
    # skipped scene, a refused one or scene input that failed its own schema checks nothing.
    checked = [r for r in records if "decision" in r and not r.get("hostile")]
    # Latency counts only a real scene the server answered with a result, never a fast refusal.
    real = [r["latency_ms"] for r in checked if r.get("real") and r.get("http_status") == 200]
    exported = [r for r in records if r.get("app_export")]
    contract_problems = sum(len(r.get("contract_problems", [])) for r in records)
    killed = bool(meta["peak_memory"].get("server_killed"))
    all_passed = all(r["status"] == "pass" for r in records) and bool(checked) and not killed
    contract_ok = (
        bool(checked)
        and contract_problems == 0
        and not killed
        and not any(r.get("rule_checks_unverified") for r in checked)
    )
    executed = sorted(
        {r["name"] for r in records if r.get("hostile") and r.get("hostile_probe_executed") is True}
    )
    hostile_complete = set(HOSTILE_PROBE_NAMES).issubset(executed)
    report = meta | {
        "counts": counts,
        # A missing listed scene leaves the run incomplete, even when other scenes passed.
        "all_passed": all_passed,
        "latency_ms": max(real) if real else None,
        "scenes_answered": sum(1 for r in records if "decision" in r),
        "scenes_checked": len(checked),
        "scenes_skipped": counts.get("skipped", 0),
        "scenes_bad_input": counts.get("bad input", 0),
        # Invariants and properties broken, summed over every answered scene. Case
        # expectations are counted separately because a case can be wrong itself.
        "contract_problem_count": contract_problems,
        # The contract holds only on evidence: at least one scene checked, none broken.
        "contract_ok": contract_ok,
        "hostile_probes_required": list(HOSTILE_PROBE_NAMES),
        "hostile_probes_executed": executed,
        "hostile_probes_complete": hostile_complete,
        # Local --no-hostile success proves only that shortened run, not S2-8's full guarantee.
        "full_contract_ok": all_passed and contract_ok and hostile_complete,
        "expectation_problem_count": sum(len(r.get("expectation_problems", [])) for r in records),
        "real_scene_passed": any(r.get("real") and r["status"] == "pass" for r in records),
        # Null when the run had no app export, so the scoreboard looks for an older run.
        "app_export_scene_valid": all(r["status"] != "bad input" for r in exported)
        if exported
        else None,
        "app_export_passed": all(r["status"] == "pass" for r in exported) if exported else None,
        # The app commit the export came from (the scoreboard ties app metrics to it); null when
        # the run had none or several, or the export's origin is unknown.
        "app_sha": app_shas.pop()
        if len(app_shas := {r.get("app_sha") for r in exported}) == 1
        else None,
        "app_export_source": [r["source"] for r in exported],
        "records": records,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        f"# End-to-end run against `{meta['server_ref']}` at `{meta['server_sha'][:12]}`",
        "",
        f"{meta['started_at']}. Endpoint `{meta['endpoint']}`. "
        f"Load average {meta['load_average_1_5_15']} on {os.cpu_count()} cores. "
        + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())),
        "",
    ]
    if meta["peak_memory"].get("server_killed"):
        lines += [
            f"**The server passed {SERVER_LIMIT_MB} MB and was killed**; every scene after that "
            "failed to connect.",
            "",
        ]
    lines += [
        "| Scene | Status | Decision | ms | Problems |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in records:
        issues = "; ".join(
            r.get("problems", [])
            + r.get("assumption_mismatches", [])
            + r.get("assumption_warnings", [])
        )
        issues = issues or r.get("skip_reason", "")
        lines.append(
            f"| {r['name']}{' (real)' if r.get('real') else ''} | {r['status']} | "
            f"{r.get('decision', '')} | {r.get('latency_ms', '')} | {issues.replace('|', '/')} |"
        )
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--server-ref", default="origin/t3/server")
    where.add_argument("--server-url", help="use a running server instead of starting one")
    parser.add_argument("--schema-ref", help="ref for the schemas (default: --server-ref)")
    parser.add_argument("--endpoint", help="POST path, when discovery is ambiguous")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--scene",
        type=Path,
        action="append",
        default=[],
        help="extra case file, scene.json or scene.zip",
    )
    parser.add_argument(
        "--real",
        type=Path,
        action="append",
        default=[],
        help="scene derived from real data; held to the latency budget",
    )
    parser.add_argument(
        "--app-export",
        type=Path,
        action="append",
        default=[],
        help="scan.zip the app exported, or a sim report folder with one (records the app SHA)",
    )
    parser.add_argument(
        "--no-hostile", action="store_true", help="skip the hostile inputs (quicker local runs)"
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    gitref.fetch()
    # --server-ref keeps its default when --server-url is given, so it also names the schemas then.
    schema_ref = args.schema_ref or args.server_ref
    schema_sha = gitref.resolve(schema_ref)
    schemas = {}
    for key, path in (("scene", SCENE_SCHEMA), ("result", RESULT_SCHEMA)):
        text = gitref.show(schema_sha, path)
        if text is None:
            raise SystemExit(f"{path} does not exist at {schema_ref}")
        schemas[key] = json.loads(text)
    rules_text = gitref.show(schema_sha, "server/rules.yaml")
    if rules_text is None:
        raise SystemExit(f"server/rules.yaml does not exist at {schema_ref}")
    rules = RuleSet.from_yaml(rules_text)

    items = (
        [load_input(p) for p in sorted(args.cases.glob("*.json"))] if args.cases.exists() else []
    )
    items += [load_input(p) for p in args.scene]
    items += [load_input(p, real=True) for p in args.real]
    for path in args.app_export:
        items.append(load_app_export(path.expanduser()))
    if not args.no_hostile:
        items += hostile_inputs()
    if not items:
        raise SystemExit("No scenes: add case files to e2e/cases or pass --scene/--real.")

    server_sha = "(running server)" if args.server_url else gitref.resolve(args.server_ref)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    label = urllib.parse.urlsplit(args.server_url).hostname if args.server_url else server_sha[:8]
    out = (args.out or DEFAULT_REPORTS / f"{stamp}-{label}").expanduser()
    out.mkdir(parents=True, exist_ok=True)

    limit = None
    with contextlib.ExitStack() as stack:
        if args.server_url:
            url = args.server_url
        else:
            url, limit = stack.enter_context(server_from_ref(server_sha, out / "server.log"))
        openapi = json.loads(urllib.request.urlopen(f"{url}/openapi.json", timeout=10).read())
        endpoint = discover_endpoint(openapi, args.endpoint)
        print(f"Server {url}, endpoint POST {endpoint.path} ({endpoint.content_type})")
        records = []
        for item in items:
            record = judge(url, endpoint, item, schemas, rules, limit)
            records.append(record)
            print(
                f"  {record['status']:<20} {item.name}"
                + (
                    f"  [{record.get('decision')}, {record.get('latency_ms')} ms]"
                    if "decision" in record
                    else ""
                )
            )
            for problem in (record.get("problems", []) + record.get("assumption_warnings", []))[:6]:
                print(f"      - {problem}")

    meta = {
        "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "server_ref": args.server_url or args.server_ref,
        "server_sha": server_sha,
        "schema_ref": schema_ref,
        "schema_sha": schema_sha,
        "sha": server_sha,  # the scoreboard matches a report to a branch head by this key
        "endpoint": f"POST {endpoint.path} ({endpoint.content_type})",
        "command": " ".join([sys.executable, "-m", "hsverify.e2e", *(argv or sys.argv[1:])]),
        # Latency on this shared Mac depends on what else runs; keep the load with the numbers.
        "load_average_1_5_15": [round(x, 1) for x in os.getloadavg()],
        "peak_memory": peak_rss_mb()
        | ({"server_mb": round(limit.peak_mb, 1), "server_killed": limit.killed} if limit else {}),
        "cases_sha256": hashlib.sha256(b"".join(i.raw for i in items)).hexdigest(),
    }
    report = write_report(out, meta, records)
    print(f"Report: {out / 'report.md'}")
    if report["all_passed"]:
        return 0
    # An unreported override is incomplete evidence, not a proven server failure.
    if (
        not meta["peak_memory"].get("server_killed")
        and all(r["status"] in ("pass", "assumptions unverified") for r in records)
        and any(r["status"] == "assumptions unverified" for r in records)
    ):
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
