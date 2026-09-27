"""The demo policy, the strict one, private rules from the environment, and the key that guards a
server holding them. The private rules here are synthetic."""

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import observed_band, shared_fixture

import api
from rules import load_rules, public_rules_dict, rules_from_dict
from scene import parse_scene
from solver import solve

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
SYNTHETIC_PRIVATE = (
    "policy: {id: private-test, version: '1', auto_approve: true, allow_reject: true}\n"
    "clearances:\n  gas_ft: {value: 4.0, source: 'test citation'}\n"
)
KEY = "test-key-not-a-secret"


def decide(name: str, loaded=None) -> dict:
    loaded = loaded or rules_from_dict(public_rules_dict())
    raw = json.loads((EXAMPLES / name).read_text())
    return solve(parse_scene(raw, loaded.rules), loaded)


# --- the demo policy ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "decision"),
    [
        ("pass-clear-side-wall.json", "pass"),
        ("reject-garage-in-the-way.json", "reject"),
        ("review-corner-not-walked.json", "manual_review"),
    ],
)
def test_each_example_gets_its_decision_under_the_demo_policy(name: str, decision: str) -> None:
    result = decide(name)
    assert result["decision"] == decision
    assert result["policy"]["id"] == "demo"
    # Every answer says whose rules decided it.
    assert "not Base's" in result["policy"]["notice"]
    assert "not Base's" in result["summary"]


def test_the_unsure_example_asks_for_views() -> None:
    # The walk stopped at the unexplored right end, so the view it lacks is past that end. It
    # used to ask for ground at s [-3.93, -3] as well, which its capture had already seen past
    # the left limit end.
    missing = decide("review-corner-not-walked.json")["missing_evidence"]
    assert [m["kind"] for m in missing] == ["past_end"]


def test_the_strict_policy_sends_everything_to_a_person(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOUSESCAN_POLICY", "strict")
    loaded = load_rules(Path("/nonexistent/rules.yaml"))
    assert (loaded.rules.policy.id, loaded.rules.policy.auto_approve) == ("public-strict", False)
    assert decide("pass-clear-side-wall.json", loaded)["decision"] == "manual_review"


def test_an_unknown_policy_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOUSESCAN_POLICY", "lenient")
    with pytest.raises(ValueError, match="HOUSESCAN_POLICY"):
        load_rules(Path("/nonexistent/rules.yaml"))


# --- private rules from the environment -----------------------------------------------------------


def test_private_rules_load_from_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HOUSESCAN_PRIVATE_RULES_B64", base64.b64encode(SYNTHETIC_PRIVATE.encode()).decode()
    )
    loaded = load_rules()
    assert loaded.sources == ("public", "private")
    assert loaded.rules.clearances.gas_ft.value == 4.0
    # The demo notice belongs to the public policy; private rules replace it with one naming the
    # checks still on public placeholders (this synthetic file sets only gas_ft).
    notice = loaded.rules.policy.notice or ""
    assert "not Base's" not in notice
    assert "pool_clearance" in notice


def test_bad_base64_is_a_startup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOUSESCAN_PRIVATE_RULES_B64", "not base64!")
    with pytest.raises(ValueError, match="HOUSESCAN_PRIVATE_RULES_B64"):
        load_rules()


def test_two_private_sources_are_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(SYNTHETIC_PRIVATE)
    monkeypatch.setenv("HOUSESCAN_PRIVATE_RULES", str(path))
    monkeypatch.setenv(
        "HOUSESCAN_PRIVATE_RULES_B64", base64.b64encode(SYNTHETIC_PRIVATE.encode()).decode()
    )
    with pytest.raises(ValueError, match="both"):
        load_rules()


# --- the key --------------------------------------------------------------------------------------


@pytest.fixture
def private_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    path = tmp_path / "rules.yaml"
    path.write_text(SYNTHETIC_PRIVATE)
    monkeypatch.setattr(api, "LOADED", load_rules(path))
    monkeypatch.setattr(api, "API_KEY", KEY)
    return TestClient(api.app)


SCENE = (EXAMPLES / "pass-clear-side-wall.json").read_bytes()
JSON = {"content-type": "application/json"}


@pytest.mark.parametrize("headers", [{}, {"authorization": "Bearer wrong"}, {"authorization": KEY}])
def test_a_private_server_refuses_without_the_key(private_api: TestClient, headers: dict) -> None:
    resp = private_api.post("/v1/placements", content=SCENE, headers=JSON | headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/v1/schemas/scene.json"])
def test_every_route_but_health_needs_the_key(private_api: TestClient, path: str) -> None:
    assert private_api.get(path).status_code == 401


def test_with_the_key_the_private_policy_answers(private_api: TestClient) -> None:
    auth = {"authorization": f"Bearer {KEY}"}
    resp = private_api.post("/v1/placements", content=SCENE, headers=JSON | auth)
    assert resp.status_code == 200
    assert resp.json()["policy"]["id"] == "private-test"


def test_health_says_private_rules_are_loaded_but_not_what(private_api: TestClient) -> None:
    body = private_api.get("/health").json()
    assert body["policy"] == {"sources": ["public", "private"]}
    assert body["auth"] == "bearer"
    assert "private-test" not in json.dumps(body)


def test_a_browser_preflight_is_not_refused(private_api: TestClient) -> None:
    resp = private_api.options(
        "/v1/placements",
        headers={"origin": "https://example.com", "access-control-request-method": "POST"},
    )
    assert resp.status_code == 200


def test_private_rules_without_a_key_answer_nothing(
    private_api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "API_KEY", None)
    resp = private_api.post("/v1/placements", content=SCENE, headers=JSON)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "no_api_key"


def test_the_public_server_needs_no_key() -> None:
    resp = TestClient(api.app).post("/v1/placements", content=SCENE, headers=JSON)
    assert resp.status_code == 200


def test_one_unseen_check_reads_in_the_singular() -> None:
    raw = shared_fixture()
    observed_band(raw, "overhead", [(-40, 5), (10, 40)])  # headroom alone unseen at the pad
    loaded = rules_from_dict(public_rules_dict())
    result = solve(parse_scene(raw, loaded.rules), loaded)
    assert "1 check depends on areas the scan did not see" in result["summary"]
