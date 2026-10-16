"""Frozen prompts and the class table. Wording is ours."""

from __future__ import annotations

import hashlib

# Detector query strings. The first 12 match B1 common.CLASSES; the rest are the brief's extras.
DETECTOR_QUERIES: dict[str, str] = {
    "electric_meter": "electric meter",
    "meter_can": "electric meter socket box",
    "main_panel": "electrical breaker panel",
    "disconnect": "electrical disconnect switch box",
    "gas_meter": "gas meter",
    "gas_regulator": "gas pressure regulator",
    "gas_pipe": "gas pipe on a wall",
    "ac_condenser": "air conditioner condenser unit",
    "water_heater": "water heater",
    "wall_box": "utility box on a wall",
    "vent": "vent on a wall",
    "window": "window",
    "door": "door",
    "garage_door": "garage door",
    "basement_well": "basement window well",
    "downspout": "downspout",
    "hose_bib": "outdoor water faucet",
    "pool": "swimming pool",
    "hot_tub": "hot tub",
    "fence": "fence",
    "gate": "gate in a fence",
    "driveway": "driveway",
    "shrub": "shrub",
    "tree": "tree",
    "bin": "trash bin",
    "deck": "wooden deck",
    "stairs": "outdoor stairs",
    "eave": "roof eave overhang",
    "porch_roof": "porch roof",
}

# B1's 12-class detector list (OWLv2 parity and GDINO token spans use this order).
B1_CLASSES: dict[str, str] = {
    "electric_meter": "electric meter",
    "meter_can": "electric meter socket box",
    "main_panel": "electrical breaker panel",
    "gas_meter": "gas meter",
    "ac_condenser": "air conditioner condenser unit",
    "window": "window",
    "door": "door",
    "downspout": "downspout",
    "hose_bib": "outdoor water faucet",
    "fence": "fence",
    "pool": "swimming pool",
    "driveway": "driveway",
}

# Scene enum. electric_meter is views-only (WS-06 owns the anchor id "meter").
# fence, gate and driveway are presence sidecars (C-05). shrub, tree and bin are
# emitted geometry; A/S blocking is WS-09's class map (SD-54).
SCENE_CLASS: dict[str, str] = {
    "electric_meter": "meter",
    "meter_can": "meter_can",
    "main_panel": "panel",
    "disconnect": "disconnect",
    "gas_meter": "gas_meter",
    "gas_regulator": "gas_regulator",
    "gas_pipe": "gas_pipe",
    "ac_condenser": "ac_unit",
    "water_heater": "other",
    "wall_box": "wall_box",
    "vent": "vent",
    "window": "window",
    "door": "door",
    "garage_door": "garage_door",
    "basement_well": "basement_well",
    "downspout": "downspout",
    "hose_bib": "hose_bib",
    "pool": "pool",
    "hot_tub": "hot_tub",
    "gate": "gate",
    "shrub": "shrub",
    "tree": "tree",
    "bin": "bin",
    "deck": "deck",
    "stairs": "stairs",
    "eave": "eave",
    "porch_roof": "porch_roof",
}

LABEL_OVERRIDE = {"water_heater": "water_heater"}
SIDECAR_ONLY = frozenset({"fence", "gate", "driveway"})


def detector_class(name: str | None) -> str | None:
    """Detector key for a detector key or a scene class name. Unknown names pass through."""
    if not name:
        return None
    if name in SCENE_CLASS:
        return name
    for key, scene_name in SCENE_CLASS.items():
        if scene_name == name:
            return key
    return name


def row_detector_class(row: dict) -> str:
    """2D rows store the scene class in `class`. `label` keeps the detector key when it was written."""
    label = row.get("label")
    if isinstance(label, str) and label in SCENE_CLASS:
        return label
    return detector_class(str(row.get("class") or "")) or ""


def is_free_standing(name: str | None) -> bool:
    key = detector_class(name)
    return bool(key) and key in FREE_STANDING


ANCHOR_VIEWS_ONLY = frozenset({"electric_meter"})

DECISION = frozenset(
    {
        "electric_meter",
        "meter_can",
        "main_panel",
        "disconnect",
        "gas_meter",
        "gas_regulator",
        "gas_pipe",
        "ac_condenser",
        "water_heater",
        "wall_box",
        "vent",
        "pool",
        "hot_tub",
    }
)
HAZARD = frozenset({"gas_meter", "gas_regulator", "gas_pipe", "pool", "hot_tub"})
GEOMETRY = frozenset(
    {
        "window",
        "door",
        "garage_door",
        "basement_well",
        "downspout",
        "hose_bib",
        "shrub",
        "tree",
        "bin",
        "deck",
        "stairs",
        "eave",
        "porch_roof",
    }
)
OPENINGS = frozenset({"window", "door", "garage_door", "basement_well"})
FREE_STANDING = frozenset({"ac_condenser", "pool", "hot_tub", "shrub", "tree", "bin", "water_heater"})
WALL_MOUNTED = frozenset(
    {
        "meter_can",
        "main_panel",
        "disconnect",
        "gas_meter",
        "gas_regulator",
        "gas_pipe",
        "wall_box",
        "vent",
        "window",
        "door",
        "garage_door",
        "basement_well",
        "downspout",
        "hose_bib",
        "eave",
        "porch_roof",
    }
)

# Gate ranking uses B3's equipment names (not our scene names).
GATE_EQUIP = ("meter", "panel", "gas_meter", "ac_condenser", "pool")
GATE_HAZARD = frozenset({"gas_meter", "pool"})
GATE_CLASSES = ("meter", "panel", "gas_meter", "ac_condenser", "pool", "water_heater")

# B3 gate_eval.PROMPTS, unchanged, so class_scores matches gate_scores_all.json.
GATE_PROMPTS: dict[str, list[str]] = {
    "meter": [
        "a close-up photo of an electric meter",
        "an electricity meter on a house wall",
        "a meter nameplate with printed numbers",
    ],
    "panel": [
        "an electrical breaker panel",
        "a circuit breaker box with breakers",
        "an electrical panel label",
        "a fuse box",
    ],
    "gas_meter": ["a gas meter with pipes", "a gas regulator and gas meter beside a house"],
    "ac_condenser": ["an outdoor air conditioner condenser unit", "a heat pump unit beside a house"],
    "pool": ["a swimming pool"],
    "water_heater": ["a water heater tank"],
    "downspout": ["a rain downspout on a house wall"],
    "hose_bib": ["an outdoor water faucet on a wall"],
    "vent": ["a dryer vent on an exterior wall"],
    "comms_box": ["a small grey utility box on a wall", "a cable tv box on a house"],
    "window": ["a window of a house"],
    "door": ["a door of a house", "a garage door"],
    "other": [
        "a lawn",
        "the sky",
        "trees",
        "a street",
        "a roof",
        "the exterior of a house",
        "a brick wall",
        "a fence",
        "a driveway",
        "a car",
        "a room interior",
        "a person",
    ],
}

# B3 gate_features.OWL_QUERIES (the gate's OWLv2, not the B1 class list).
OWL_QUERIES = [
    "electric meter",
    "meter nameplate with printed text",
    "electrical breaker panel",
    "electrical panel label",
    "circuit breakers",
    "gas meter",
    "air conditioner condenser unit",
    "water heater",
    "downspout",
    "outdoor water faucet",
    "vent on a wall",
    "utility box on a wall",
    "window",
    "door",
    "swimming pool",
]

GROUND_TYPES = ["grass", "gravel", "concrete_or_paving", "mulch_or_soil", "deck", "other"]

# SD-47. B1 names map into the scene Region.material enum. pavers, dirt, drive and
# walkway are kept only when the model names them; they are not in the prompt.
EXPLICIT_GROUND = ("pavers", "dirt", "drive", "walkway")
GROUND_MATERIAL = {
    "grass": "lawn",
    "concrete_or_paving": "concrete",
    "mulch_or_soil": "mulch",
    "gravel": "gravel",
    "deck": "deck",
    "other": "unknown",
}
GROUND_MATERIAL.update({name: name for name in EXPLICIT_GROUND})

# Stills WS-08 owns. They never enter the VLM set S.
WS08_PURPOSES = frozenset(
    {
        "meter_close",
        "meter_oblique",
        "panel_close",
        "panel_open",
        "main_disconnect",
        "panel_surround",
    }
)
# Checklist stills that are always selected.
ALWAYS_PURPOSES = frozenset(
    {
        "meter_surround",
        "meter_left",
        "meter_right",
        "adjacent_wall",
        "behind_fence",
        "house_overview",
    }
)

PRESENCE_CLASSES = {
    "electric_meter": "electric utility meter (round glass or digital meter on a socket, or a meter bank)",
    "meter_can": "the metal socket box an electric meter plugs into",
    "main_panel": "electrical breaker panel / load center / deadfront / panel label (open or closed)",
    "disconnect": "electrical disconnect switch box",
    "gas_meter": "gas meter",
    "gas_regulator": "gas pressure regulator",
    "gas_pipe": "gas pipe on a wall",
    "ac_condenser": "outdoor air-conditioner condenser or heat-pump unit",
    "water_heater": "water heater or tankless water heater",
    "wall_box": "small utility / comms / cable box on a wall",
    "vent": "wall or dryer vent",
    "window": "window",
    "door": "door",
    "garage_door": "garage door",
    "basement_well": "basement window well",
    "downspout": "rain downspout",
    "hose_bib": "outdoor water faucet / hose bib",
    "pool": "swimming pool, or pool equipment (pump, filter, heater)",
    "hot_tub": "hot tub",
    "fence": "fence",
    "gate": "gate in a fence",
    "driveway": "driveway",
    "shrub": "shrub",
    "tree": "tree",
    "bin": "trash bin",
    "deck": "wooden deck",
    "stairs": "outdoor stairs",
    "eave": "roof eave overhang",
    "porch_roof": "porch roof",
}

PROMPT_PRESENCE_V1 = (
    "You are screening a photo for a home battery-installation site survey. For each class below say "
    'whether at least one instance is visible: "yes", "no" or "unsure".\n'
    + "\n".join(f"- {k}: {v}" for k, v in PRESENCE_CLASSES.items())
    + f"""

Notes: a water meter is not a gas meter. A disconnect switch box alone is not a main_panel unless it is a
breaker panel. Only report what you can actually see.
Also report:
- "readable_equipment_text": "yes" if the photo shows equipment text (meter nameplate, panel label,
  breaker ratings) large and sharp enough to read, else "no".
- "ground_type": the dominant outdoor ground surface near the building, one of {", ".join(GROUND_TYPES)} (use "other" if no ground is visible).
- "quality": "good" | "blurry" | "dark" | "obstructed".
Reply with JSON only: {{"present": {{"<class>": "yes|no|unsure", ...}}, "ground_type": "<ground type>", "readable_equipment_text": "yes|no", "quality": "..."}}"""
)

_BOX_CLASS_TEXT = "\n".join(f"- {k}: {v}" for k, v in DETECTOR_QUERIES.items())
PROMPT_BOXES_V1 = f"""You are inspecting a photo for a home battery installation survey. Find every visible instance of these object classes:
{_BOX_CLASS_TEXT}

Notes: meter_can is the metal socket enclosure an electric meter plugs into (box it separately from the meter glass). main_panel is an electrical breaker/distribution panel or its closed enclosure. hose_bib is an outdoor water tap/spigot. driveway is a paved/gravel vehicle driveway area. A water meter is not a gas meter. Only box objects you can actually see; do not guess hidden ones. Box each window/door individually.

Also say, for each class, whether at least one instance is visible ("yes", "no" or "unsure"), and the dominant ground surface visible outdoors near the building, one of {GROUND_TYPES} (use "other" if no ground is visible).

Reply with JSON only, no prose:
{{"objects": [{{"label": "<class>", "box_2d": [ymin, xmin, ymax, xmax], "confidence": 0.0-1.0}}],
 "present": {{"<class>": "yes|no|unsure", ...}},
 "ground_type": "<ground type>"}}
box_2d coordinates are integers 0-1000 relative to image height (y) and width (x)."""


def prompt_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


PRESENCE_SHA = prompt_sha256(PROMPT_PRESENCE_V1)
BOXES_SHA = prompt_sha256(PROMPT_BOXES_V1)
