"""Load rules.yaml, merge the private override over it, and validate the result.

Every threshold the solver compares against lives in the rules file. A missing or misspelled key is
a startup error naming the key, never a silent default.
"""

import base64
import binascii
import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SERVER_DIR = Path(__file__).resolve().parent
PUBLIC_RULES = SERVER_DIR / "rules.yaml"
PRIVATE_RULES_ENV = "HOUSESCAN_PRIVATE_RULES"  # a path to the private file
# The private file's YAML itself, base64-encoded: how a deployment gets it without uploading a file.
PRIVATE_RULES_B64_ENV = "HOUSESCAN_PRIVATE_RULES_B64"
DEFAULT_PRIVATE_RULES = SERVER_DIR.parent / "private" / "rules.yaml"
# Which public policy applies when no private rules are loaded: rules.yaml's demo policy, which
# decides automatically, or the strict one, which sends every answer to a person because some
# public values are placeholders.
POLICY_ENV = "HOUSESCAN_POLICY"
STRICT_POLICY = {
    "id": "public-strict",
    "auto_approve": False,
    "notice": "Public rules with placeholder values; a person confirms every answer.",
}

Effect = Literal["fail", "review", "detour", "allow"]
ObjectType = Literal[
    "window",
    "door",
    "garage_door",
    "ac",
    "gas_meter",
    "elec_box",
    "vent",
    "downspout",
    "pool",
    "battery",
]
GroundType = Literal["drive", "concrete", "gravel", "lawn", "mulch", "deck"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Value(_Strict):
    value: float = Field(ge=0, allow_inf_nan=False)
    source: str = Field(min_length=1)
    placeholder: bool = False


class Policy(_Strict):
    id: str | None
    version: str | None
    auto_approve: bool
    allow_reject: bool
    # Shown with every answer: whose rules these are.
    notice: str | None = None


class Battery(_Strict):
    width_ft: Value
    depth_ft: Value
    height_ft: Value


class Errors(_Strict):
    tap_ft: Value
    vlm_ft: Value
    mesh_ft: Value
    plane_ft: Value
    tape_ft: Value
    wall_ft: Value
    meter_ft: Value
    drift_per_ft: Value


class Sweep(_Strict):
    step_ft: Value
    wall_join_ft: Value
    meter_to_wall_max_ft: Value

    @model_validator(mode="after")
    def _positive_step(self) -> "Sweep":
        # Every solve steps the battery along the wall by this much; zero or less can't advance.
        if self.step_ft.value <= 0:
            raise ValueError(f"sweep.step_ft must be positive, got {self.step_ft.value}")
        return self


class Clearances(_Strict):
    gas_ft: Value
    ac_ft: Value
    battery_ft: Value
    opening_ft: Value
    drive_ft: Value
    pool_ft: Value
    wall_equipment_ft: Value


class Openings(_Strict):
    types: list[ObjectType]
    exempt_fixed_windows: bool
    exempt_bottom_above_ft: float | None


class WallEquipment(_Strict):
    types: list[ObjectType]


class Facing(_Strict):
    min_ft: Value
    measured_from: Literal["battery_front", "wall"]


class Headroom(_Strict):
    min_ft: Value


class MeterWorkingSpace(_Strict):
    width_ft: Value
    depth_ft: Value


class Ground(_Strict):
    allowed: list[GroundType]
    drivable: list[GroundType]
    source: str = Field(min_length=1)
    placeholder: bool


class Route(_Strict):
    max_ft: Value
    confident_reach_ft: Value
    height_ft: Value
    corner_allowance_ft: Value
    crossing: dict[ObjectType, Effect]

    @model_validator(mode="after")
    def _confident_within_max(self) -> "Route":
        # Past the confident reach a run goes to review, past the maximum it fails; a confident
        # reach beyond the maximum would let a run over the maximum pass.
        if self.confident_reach_ft.value > self.max_ft.value:
            raise ValueError(
                f"route.confident_reach_ft ({self.confident_reach_ft.value}) must not exceed "
                f"route.max_ft ({self.max_ft.value})"
            )
        return self


class Rules(_Strict):
    policy: Policy
    battery: Battery
    errors: Errors
    sweep: Sweep
    clearances: Clearances
    openings: Openings
    wall_equipment: WallEquipment
    facing: Facing
    headroom: Headroom
    meter_working_space: MeterWorkingSpace
    ground: Ground
    route: Route

    @model_validator(mode="after")
    def _exemption_within_the_opening_checks_height(self) -> "Rules":
        # The opening check needs the wall seen only up to headroom height; a window above that
        # would still count under a higher exemption but could be missed, unseen, above the view.
        exempt = self.openings.exempt_bottom_above_ft
        if exempt is not None and exempt > self.headroom.min_ft.value:
            raise ValueError(
                f"openings.exempt_bottom_above_ft ({exempt}) must not exceed headroom.min_ft "
                f"({self.headroom.min_ft.value}), the wall height the opening check requires seen"
            )
        return self


@dataclass(frozen=True)
class LoadedRules:
    rules: Rules
    sources: tuple[str, ...]
    sha256: str
    # Dotted keys the private file overrides. Answers withhold their source text.
    private_keys: frozenset[str] = frozenset()


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Mappings merge key by key; any other override value replaces the base value."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses a repeated key. PyYAML keeps the last copy silently, so a rules
    file with two `clearances:` blocks would load a threshold nobody meant."""


def _unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> Any:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ValueError(
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1} of "
                f"{key_node.start_mark.name}"
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _read_yaml(path: Path) -> dict[str, Any]:
    return _parse_yaml(path.read_text(), str(path))


def _parse_yaml(text: str, where: str) -> dict[str, Any]:
    data = yaml.load(text, Loader=_UniqueKeyLoader)
    if not isinstance(data, dict):
        raise ValueError(f"{where}: expected a mapping at the top level")
    return data


def rules_from_dict(
    data: dict[str, Any],
    sources: tuple[str, ...] = ("public",),
    private_keys: frozenset[str] = frozenset(),
) -> LoadedRules:
    rules = Rules.model_validate(data)
    canonical = json.dumps(rules.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return LoadedRules(rules, sources, hashlib.sha256(canonical.encode()).hexdigest(), private_keys)


def overridden_keys(public: dict[str, Any], private: dict[str, Any], path: str = "") -> set[str]:
    """Dotted keys the private file sets. A private threshold must carry its own source: merged
    over the public one, it would otherwise be shown with the public citation."""
    keys: set[str] = set()
    for key, value in private.items():
        dotted = f"{path}{key}"
        base = public.get(key)
        if isinstance(value, dict) and isinstance(base, dict):
            if "value" in base and "source" in base and "source" not in value:
                raise ValueError(
                    f"private rules: {dotted} overrides a cited value without its own source"
                )
            if "value" in base:
                keys.add(dotted)
            else:
                keys |= overridden_keys(base, value, f"{dotted}.")
        else:
            keys.add(dotted)
    return keys


def public_rules_dict() -> dict[str, Any]:
    return _read_yaml(PUBLIC_RULES)


def load_rules(private_path: Path | None = None) -> LoadedRules:
    """Public rules under the policy HOUSESCAN_POLICY names, with the private rules merged over
    them when there are any: from `private_path`, else HOUSESCAN_PRIVATE_RULES_B64 (the YAML
    itself) or HOUSESCAN_PRIVATE_RULES (a path), else private/rules.yaml if it exists."""
    data = _public_policy(public_rules_dict())
    private = _private_rules(private_path)
    if private is None:
        return rules_from_dict(data)
    private_keys = frozenset(overridden_keys(data, private))
    merged = deep_merge(data, private)
    # The public notice says the answers are not under Base's rules, which private rules make
    # untrue; but any public placeholder they leave in place still decides answers, so the
    # notice names those checks instead of going quiet.
    own = private.get("policy", {}).get("notice")
    merged["policy"]["notice"] = _mixed_notice(own, _placeholders_left(merged, private_keys))
    return rules_from_dict(merged, ("public", "private"), private_keys)


# The check each placeholder rule decides; placeholders not tied to one check are named by key.
PLACEHOLDER_CHECKS = {
    "clearances.drive_ft": "drive_clearance",
    "clearances.pool_ft": "pool_clearance",
    "clearances.wall_equipment_ft": "wall_equipment_above",
    "headroom.min_ft": "headroom",
    "ground": "ground_surface",
    "route.confident_reach_ft": "route_length",
    "route.corner_allowance_ft": "route_length",
    "route.height_ft": "route_path",
}


def _placeholders_left(merged: dict[str, Any], private_keys: frozenset[str]) -> list[str]:
    """Dotted keys still marked placeholder that the private file did not set."""
    left: list[str] = []

    def walk(node: dict[str, Any], path: str) -> None:
        if node.get("placeholder") is True:
            # A cited value is replaced when the private file sets it; a group (such as ground)
            # only when it sets every child that decides something, not just one of them.
            if "value" in node:
                replaced = path in private_keys
            else:
                children = [k for k in node if k not in ("source", "placeholder")]
                replaced = all(
                    any(p == f"{path}.{k}" or p.startswith(f"{path}.{k}.") for p in private_keys)
                    for k in children
                )
            if not replaced:
                left.append(path)
            return
        for key, value in node.items():
            if isinstance(value, dict):
                walk(value, f"{path}.{key}" if path else key)

    walk(merged, "")
    return left


def _mixed_notice(own: str | None, left: list[str]) -> str | None:
    if not left:
        return own
    checks = sorted({PLACEHOLDER_CHECKS[k] for k in left if k in PLACEHOLDER_CHECKS})
    other = sorted(k for k in left if k not in PLACEHOLDER_CHECKS)
    parts = []
    if checks:
        parts.append("these checks still use public placeholder values: " + ", ".join(checks))
    if other:
        parts.append("placeholder settings still apply: " + ", ".join(other))
    mixed = "Private rules, but " + "; ".join(parts) + "."
    return f"{own} {mixed}" if own else mixed


def _public_policy(data: dict[str, Any]) -> dict[str, Any]:
    name = os.environ.get(POLICY_ENV, "demo")
    if name == "demo":
        return data
    if name == "strict":
        return deep_merge(data, {"policy": STRICT_POLICY})
    raise ValueError(f"{POLICY_ENV}={name!r}: expected 'demo' or 'strict'")


def _private_rules(private_path: Path | None) -> dict[str, Any] | None:
    if private_path is not None:
        return _read_yaml(private_path) if private_path.is_file() else None
    encoded = os.environ.get(PRIVATE_RULES_B64_ENV)
    path = os.environ.get(PRIVATE_RULES_ENV)
    if encoded and path:
        raise ValueError(f"both {PRIVATE_RULES_B64_ENV} and {PRIVATE_RULES_ENV} are set; set one")
    if encoded:
        try:
            text = base64.b64decode(encoded, validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            raise ValueError(f"{PRIVATE_RULES_B64_ENV} is not base64-encoded UTF-8") from None
        return _parse_yaml(text, PRIVATE_RULES_B64_ENV)
    if path:
        if not Path(path).is_file():
            raise FileNotFoundError(f"{PRIVATE_RULES_ENV}={path} does not name a file")
        return _read_yaml(Path(path))
    return _read_yaml(DEFAULT_PRIVATE_RULES) if DEFAULT_PRIVATE_RULES.is_file() else None
