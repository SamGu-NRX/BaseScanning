"""The inspection server shows only what the independent reader verified.

A pack the reader refuses must reach the page as a refusal with no evidence
attached, and the refusal demonstrations must come from running the reader
now. The HTTP tests start the real server process."""

import ast
import importlib
import json
import select
import subprocess
import sys
import urllib.error
import urllib.request

import pytest
import read
from support import (
    EXAMPLE_PACK,
    TOOL_DIR,
    example_entries,
    rebuilt_example,
    run_reader,
    sha256_hex,
    write_pack,
)

SERVE = TOOL_DIR / "inspection" / "serve.py"


@pytest.fixture(scope="module")
def serve():
    sys.path.insert(0, str(SERVE.parent))
    try:
        yield importlib.import_module("serve")
    finally:
        sys.path.remove(str(SERVE.parent))


def test_example_payload_is_exactly_what_the_reader_verified(serve):
    payload = serve.verification_payload(EXAMPLE_PACK)
    assert payload["status"] == "verified"
    assert payload["evidence"] == read.verify_pack(EXAMPLE_PACK.read_bytes())
    assert payload["evidence"]["geometry_correctness"]["assertion"] is None
    assert payload["evidence"]["pack_sha256"] == sha256_hex(EXAMPLE_PACK.read_bytes())


def claim_correctness(manifest):
    manifest["geometry_correctness"]["assertion"] = "geometry verified correct"


def point_at_absent_source(manifest):
    manifest["capture"]["source_refs"][0]["path"] = "capture/frame_9999.json"


def claim_other_result(manifest):
    manifest["result"]["result_id"] = "result-synth-0002"


def mismatched_report(tmp_path):
    manifest, _ = example_entries()
    return write_pack(tmp_path / "bad.zip", manifest, b"# a different report\n")


def malformed_manifest(tmp_path):
    _, report = example_entries()
    return write_pack(tmp_path / "bad.zip", b'{"evidence_pack_format_version": "1.0",', report)


def not_a_zip(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"plain text")
    return path


BAD_PACKS = [
    ("non-null-assertion", lambda tmp: rebuilt_example(tmp, claim_correctness, "bad.zip"), "invalid-manifest", "must be null"),
    ("absent-source", lambda tmp: rebuilt_example(tmp, point_at_absent_source, "bad.zip"), "missing-source", "frame_9999.json"),
    ("other-result-claimed", lambda tmp: rebuilt_example(tmp, claim_other_result, "bad.zip"), "mismatched-result", "result-synth-0002"),
    ("report-hash-mismatch", mismatched_report, "invalid-manifest", "does not match its recorded hash"),
    ("malformed-manifest-json", malformed_manifest, "invalid-manifest", "not readable JSON"),
    ("not-a-zip", not_a_zip, "invalid-archive", "not a readable zip"),
]


@pytest.mark.parametrize("name,build,kind,needle", BAD_PACKS, ids=[case[0] for case in BAD_PACKS])
def test_refused_pack_reaches_the_page_as_a_refusal_only(serve, tmp_path, name, build, kind, needle):
    pack = build(tmp_path)
    payload = serve.verification_payload(pack)
    assert payload["status"] == "refused"
    assert "evidence" not in payload
    assert payload["refusal"]["kind"] == kind
    assert needle in payload["refusal"]["detail"]
    assert payload["refusal"]["rejected_pack_sha256"] == sha256_hex(pack.read_bytes())
    # The CLI reader gives the same verdict for the same bytes.
    cli = run_reader(pack)
    assert cli.returncode == 2 and f"REFUSAL {kind}:" in cli.stdout


def test_missing_pack_is_a_refusal_without_a_hash(serve, tmp_path):
    payload = serve.verification_payload(tmp_path / "absent.zip")
    assert payload["status"] == "refused"
    assert payload["refusal"]["rejected_pack_sha256"] is None


def test_refusal_demonstrations_run_the_reader_now(serve):
    payload = serve.refusal_demo_payload()
    assert [demo["expected_kind"] for demo in payload["refusals"]] == ["missing-source", "mismatched-result"]
    for demo in payload["refusals"]:
        assert demo["outcome"] == "refused", demo
        assert demo["kind"] == demo["expected_kind"]
        assert demo["rejected_pack_sha256"] == sha256_hex(EXAMPLE_PACK.read_bytes())
        assert demo["command"].startswith("python tools/capture-evidence-pack/read.py tools/capture-evidence-pack/evidence/example.zip --capture-root ")


def test_server_reads_packs_only_through_the_reader():
    """serve.py must not open the archive itself (a second, unchecked reading
    path) and must not use the packer's validator."""
    tree = ast.parse(SERVE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "read" in imported
    assert not imported & {"zipfile", "pack", "validate"}


class Server:
    def __init__(self, *args):
        self.process = subprocess.Popen(
            [sys.executable, str(SERVE), "--port", "0", *map(str, args)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # A server that never announces its port fails the test instead of
        # hanging it: wait at most 10 s for the first line.
        ready, _, _ = select.select([self.process.stdout], [], [], 10)
        if not ready:
            self.close()
            raise AssertionError("the server printed no address within 10 s")
        line = self.process.stdout.readline()
        assert line.startswith("inspection page: http://127.0.0.1:"), line
        self.base = line.split()[2].rstrip("/")
        assert not self.base.endswith(":0"), "the server must print the port it bound, not the one requested"

    def get(self, path: str) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.process.stdout.close()
        self.process.stderr.close()


@pytest.fixture
def server_factory():
    servers = []

    def start(*args):
        server = Server(*args)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def test_http_serves_a_bad_pack_as_refused(server_factory, tmp_path):
    pack = rebuilt_example(tmp_path, claim_correctness, "bad.zip")
    status, body = server_factory("--pack", pack).get("/data.json")
    payload = json.loads(body)
    assert status == 200
    assert payload["status"] == "refused"
    assert "evidence" not in payload and "manifest" not in payload


def test_http_serves_the_example_as_verified(server_factory):
    status, body = server_factory().get("/data.json")
    payload = json.loads(body)
    assert status == 200 and payload["status"] == "verified"
    assert payload["evidence"]["pack_sha256"] == sha256_hex(EXAMPLE_PACK.read_bytes())


@pytest.mark.parametrize("fault,path", [("data-malformed", "/data.json"), ("refusals-malformed", "/refusals.json")])
def test_http_malformed_faults_send_a_body_that_is_not_json(server_factory, fault, path):
    status, body = server_factory("--fault", fault).get(path)
    assert status == 200
    with pytest.raises(json.JSONDecodeError):
        json.loads(body)


def test_http_incomplete_faults_send_json_missing_a_record(server_factory):
    _, body = server_factory("--fault", "data-incomplete").get("/data.json")
    assert json.loads(body)["evidence"]["packaged_files"] == [{}]
    _, body = server_factory("--fault", "refusals-incomplete").get("/refusals.json")
    assert json.loads(body)["refusals"][0]["kind"] is None


@pytest.mark.parametrize("fault,path", [("data", "/data.json"), ("refusals", "/refusals.json"), ("both", "/data.json"), ("both", "/refusals.json")])
def test_http_failure_faults_send_500(server_factory, fault, path):
    status, _ = server_factory("--fault", fault).get(path)
    assert status == 500


def test_http_serves_no_other_files(server_factory):
    server = server_factory()
    assert server.get("/vendor/axe.min.js")[0] == 200
    for path in ("/serve.py", "/../read.py", "/screenshots/axe-results.json", "/vendor/NOTICE.txt"):
        assert server.get(path)[0] == 404, path



@pytest.mark.parametrize("bad", ["capture/frame\x00.json", "capture/frame\x1f.json"], ids=["nul", "control"])
def test_control_character_path_reaches_the_page_as_a_refusal(serve, tmp_path, bad):
    def mutate(manifest):
        manifest["capture"]["source_refs"][0]["path"] = bad

    pack = rebuilt_example(tmp_path, mutate, "bad.zip")
    payload = serve.verification_payload(pack)
    assert payload["status"] == "refused"
    assert "evidence" not in payload
    assert payload["refusal"]["kind"] == "invalid-manifest"


def test_http_control_character_path_is_refused_not_dropped(server_factory, tmp_path):
    def mutate(manifest):
        manifest["result"]["path"] = "result/result\x00.json"

    pack = rebuilt_example(tmp_path, mutate, "bad.zip")
    status, body = server_factory("--pack", pack).get("/data.json")
    assert status == 200
    assert json.loads(body)["status"] == "refused"
