import json
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from server.contracts.scale import ScaleBlock
from server.scale.block import REASON_DETAILS, GateView, decide
from server.scale.fuse import Source, fuse
from server.scale.meter_table import MeterTable, TableError, TableMatch, TableMiss
from server.scale.params import ScaleParams
from server.scale.reads_view import MeterIdentity

SEED = Path(__file__).resolve().parent / "fixtures" / "meter_models.seed.v1.json"
SCALE_SCHEMA = Path(__file__).resolve().parents[2] / "contracts" / "schemas" / "scale.v0.schema.json"
PARAMS = ScaleParams()

_BASE_ONLY = {
    "source": "none",
    "rel_bound_95": 0.1,
    "status": "unverified",
    "estimates": [{"kind": "meter_model", "rel_sd": 0.02, "used": False}],
    "conflict": False,
}


def test_base_only_scale_document_validates():
    """WS-00's required fields are enough. The extension keys are optional."""

    block = ScaleBlock.model_validate(_BASE_ONLY)
    assert block.origin_m is None
    assert block.reason is None
    assert block.arkit_gate is None
    assert block.evidence is None
    assert block.views_needed == []
    assert block.member_actions == []
    schema = json.loads(SCALE_SCHEMA.read_text())
    jsonschema.validate(_BASE_ONLY, schema)


def _identity(**kwargs) -> MeterIdentity:
    base = dict(
        manufacturer="Itron",
        model_family="Itron CENTRON",
        form="2S",
        model_evidence="printed",
        model_confidence=0.9,
        agree=True,
        images=["s1"],
        cover_material="polycarbonate",
        socket_type="ring",
        ansi_socket_meter=True,
        base_flange_visible=True,
        type_code=None,
        source="reads",
    )
    base.update(kwargs)
    return MeterIdentity(**base)


def test_lidar_miss_keeps_a_finished_meter_scale():
    from server.scale.run import _lidar_miss_unless_meter_scaled

    meter = type("Meter", (), {"code": None})()
    lidar = type("Lidar", (), {"applied": False, "reason": "lidar_too_few_pairs"})()
    assert _lidar_miss_unless_meter_scaled(None, meter, lidar) is None
    assert _lidar_miss_unless_meter_scaled(None, None, lidar) == "lidar_too_few_pairs"


def test_cover_mixture_and_fuzzy_match_change_the_meter_source():
    from server.scale.rim import CoverMeasurement
    from server.scale.run import _fuse_block

    meter = CoverMeasurement("two_view", 1.0, 0.0074, 0.0, np.zeros(3), np.zeros(3), [], [], 0.0, None, None, [])
    gate = type("Gate", (), {"available": False, "s_arkit_per_source": None, "rel_sd": None, "residual_pct": None})()
    single = TableMatch("centron", "family_material", [(0.16, 1.0)], None, None, None, "datasheet")
    mixed = TableMatch("centron", "family_material", [(0.16, 0.5), (0.16 * 1.02, 0.5)], None, None, None, "datasheet")
    fuzzy = TableMatch("centron", "fuzzy", [(0.16, 1.0)], None, None, None, "datasheet")
    _, _, bound_single, _, _ = _fuse_block(None, meter, gate, PARAMS, single)
    _, _, bound_mixed, _, _ = _fuse_block(None, meter, gate, PARAMS, mixed)
    _, _, _, _, fuzzy_estimates = _fuse_block(None, meter, gate, PARAMS, fuzzy)
    assert bound_mixed > bound_single
    assert fuzzy_estimates[0].rel_sd == pytest.approx(0.0074 * 1.5)


def test_implied_diameter_uses_the_closeup_distance():
    from server.scale.apply import CameraRecord
    from server.scale.contour import Ellipse
    from server.scale.run import _implied_diameter_m

    ellipse = Ellipse(0, 0, 100, 80, 0, 1, 1)
    camera = CameraRecord("s1", None, np.eye(4), [500.0, 500.0, 0.0, 0.0], 100, 100)
    packet = {"scaleReference": {"meterCloseUp": {"still": "s1", "arkitDistanceM": 0.4}}}
    assert _implied_diameter_m([ellipse], camera, packet, "s1") == pytest.approx(0.16)


def test_pinned_centron_metres_and_version():
    table = MeterTable.load(SEED)
    assert table.table_version == "1.0.0"
    poly = table.lookup(_identity())
    glass = table.lookup(_identity(cover_material="glass"))
    assert isinstance(poly, TableMatch)
    assert isinstance(glass, TableMatch)
    assert poly.cover_hypotheses[0][0] == pytest.approx(0.1598)
    assert glass.cover_hypotheses[0][0] == pytest.approx(0.1631)
    assert poly.rim_offset_m == pytest.approx(0.0686)
    assert glass.rim_offset_m == pytest.approx(0.0770)
    assert poly.base_od_m == pytest.approx(0.1765)
    assert poly.ring_od_m == pytest.approx(0.1795)
    assert poly.cover_hypotheses[0][1] == pytest.approx(0.9)
    assert poly.cover_hypotheses[1][1] == pytest.approx(0.1)


def test_unknown_material_is_an_even_mixture():
    table = MeterTable.load(SEED)
    match = table.lookup(_identity(model_family="Itron OpenWay CENTRON", cover_material="unknown"))
    assert isinstance(match, TableMatch)
    weights = sorted(weight for _, weight in match.cover_hypotheses)
    assert weights == pytest.approx([0.5, 0.5])


def test_other_families_are_not_centron():
    table = MeterTable.load(SEED)
    for manufacturer, family in (
        ("Aclara", "Aclara I-210+"),
        ("Landis+Gyr", "Landis+Gyr FOCUS AX"),
        ("Tatung", "Tatung D4S"),
        ("Sensus", "Sensus iConA"),
    ):
        found = table.lookup(_identity(manufacturer=manufacturer, model_family=family, cover_material="glass"))
        assert isinstance(found, TableMiss)
        assert found.code == "meter_model_not_in_table"
        assert not (isinstance(found, TableMatch) and "centron" in found.model_id)


def test_generic_prior_is_assumed_and_refused():
    table = MeterTable.load(SEED)
    found = table.lookup(_identity(manufacturer="Generic", model_family="prior", cover_material="unknown"))
    assert isinstance(found, TableMiss)
    assert found.code == "meter_dimension_assumed"


def test_table_rejects_a_metre_without_provenance(tmp_path: Path):
    payload = json.loads(SEED.read_text())
    payload["entries"][0].pop("cover_od_prov")
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(TableError):
        MeterTable.load(path)


@pytest.mark.parametrize("detail", REASON_DETAILS)
def test_every_reason_detail_is_a_real_status(detail: str):
    metric = detail not in {"no_scale_source", "meter_closeup_missing"}
    source = "none" if detail == "no_scale_source" else "meter_model" if metric else "arkit"
    decision = decide(
        source=source,
        rel_bound_95=0.2 if detail == "no_scale_source" else 0.05,
        reason_detail=detail,
        gate=GateView(True, True),
        conflict_flag=detail == "scale_conflict_objects",
        params=PARAMS,
        meter_method="two_view",
        meter_prov="datasheet",
        lone=False,
        agree=True,
        identity_source="reads",
        pairs=0,
        frames=0,
        metric=metric and detail != "scale_conflict_objects",
    )
    assert decision.reason_detail == detail
    assert decision.status in {"verified", "single_reference", "unverified"}
    if detail == "no_scale_source":
        assert decision.reason is None
        assert decision.member_actions == []
    elif detail == "scale_conflict_objects":
        assert decision.reason == "scale_conflict"
        assert decision.status == "unverified"
    else:
        assert decision.reason == "scale_unresolved"
        assert decision.member_actions == ["retake_meter_closeups"]
        assert decision.views_needed[0]["id"] == "vn-scale-1"


def test_verified_is_impossible_for_the_blocked_cases():
    common = dict(
        source="meter_model",
        rel_bound_95=0.01,
        reason_detail=None,
        conflict_flag=False,
        params=PARAMS,
        meter_method="two_view",
        meter_prov="datasheet",
        lone=False,
        agree=True,
        identity_source="reads",
        pairs=400,
        frames=8,
        metric=True,
    )
    blocked = [
        dict(gate=GateView(False, False)),
        dict(source="arkit", meter_method=None, metric=False, gate=GateView(True, True)),
        dict(identity_source="on_device_text", gate=GateView(True, True)),
        dict(agree=False, gate=GateView(True, True)),
        dict(gate=GateView(True, True), params=ScaleParams(meter_verified_enabled=False)),
    ]
    for override in blocked:
        decision = decide(**{**common, **override})
        assert decision.status != "verified"


def test_lidar_can_verify_only_when_the_gate_passes():
    decision = decide(
        source="lidar",
        rel_bound_95=0.012,
        reason_detail=None,
        gate=GateView(True, True),
        conflict_flag=False,
        params=PARAMS,
        meter_method=None,
        meter_prov=None,
        lone=False,
        agree=True,
        identity_source=None,
        pairs=250,
        frames=6,
        metric=True,
    )
    assert decision.status == "verified"
    unavailable = decide(
        source="lidar",
        rel_bound_95=0.012,
        reason_detail=None,
        gate=GateView(False, False),
        conflict_flag=False,
        params=PARAMS,
        meter_method=None,
        meter_prov=None,
        lone=False,
        agree=True,
        identity_source=None,
        pairs=250,
        frames=6,
        metric=True,
    )
    assert unavailable.status == "single_reference"


def test_student_t_bounds_match_the_arkit_profiles():
    modern = fuse([Source("arkit", 0.0, 0.03, student=True)])
    old = fuse([Source("arkit", 0.0, 0.10, student=True)])
    assert 0.08 <= modern.bound_95 <= 0.11
    assert 0.25 <= old.bound_95 <= 0.33


def test_single_gaussian_stays_near_the_observation():
    posterior = fuse([Source("lidar", float(np.log(1.15)), 0.005)])
    assert posterior.scale == pytest.approx(1.15, rel=0.005)


@pytest.mark.parametrize("scale", [0.3, 1.5, 3.0, 10.0])
def test_fuse_keeps_metric_scales_outside_the_unit_interval(scale: float):
    posterior = fuse([Source("lidar", float(np.log(scale)), 0.005)])
    assert posterior.scale == pytest.approx(scale, rel=0.005)
    assert posterior.bound_95 < 0.05


def test_reason_detail_is_a_closed_set():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScaleBlock(
            source="none",
            rel_bound_95=1,
            status="unverified",
            estimates=[],
            conflict=False,
            reason_detail="not_a_code",
        )


def test_estimate_method_is_a_closed_set():
    from pydantic import ValidationError

    from server.contracts.scale import ScaleEstimate

    with pytest.raises(ValidationError):
        ScaleEstimate(kind="lidar", rel_sd=0.01, used=True, method="not_a_code")


def test_a_short_door_leaf_conflicts_with_the_metric_anchor():
    from types import SimpleNamespace

    from server.scale.apply import CameraRecord
    from server.scale.checks import object_sources
    from server.scale.run import _objects_conflict

    pose = np.eye(4)
    camera = CameraRecord("door", "door", pose, [1000, 1000, 500, 500], 1000, 1000)
    cloud = np.array([(x, y, -2.0) for x in np.linspace(-1, 1, 5) for y in np.linspace(-1, 1, 4)])
    packet = {
        "scaleReference": {"ext": {"doorViews": [{"frame": "door", "kind": "entry_door", "box": [200, 300, 800, 800]}]}}
    }
    measured = object_sources(packet, [camera], {"door": cloud})
    assert measured and measured[0][0] == "entry_door"
    door_scale = float(np.exp(measured[0][1].log_obs))
    assert door_scale == pytest.approx(80 * 0.0254 / 1.2, rel=0.05)
    gate = SimpleNamespace(available=True, s_arkit_per_source=door_scale, rel_sd=0.02, passed=True)
    lidar = SimpleNamespace(applied=True, scale=1.0, rel_sd=0.005)
    flag, gap = _objects_conflict(lidar, None, gate, PARAMS, None, measured)
    assert flag
    assert gap is not None and gap > 0.2
