import importlib
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = sorted(ROOT.glob("packet/schema/*.schema.json")) + sorted(ROOT.glob("eval/schemas/*.schema.json"))
MODULES = [f"packetgen.{p.stem}" for p in sorted((ROOT / "packet/packetgen").glob("*.py")) if p.stem != "__main__"] + [
    f"evalharness.{p.stem}" for p in sorted((ROOT / "eval/evalharness").glob("*.py"))
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)


@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.name)
def test_schema_is_valid(path):
    schema = json.loads(path.read_text())
    jsonschema.validators.validator_for(schema).check_schema(schema)


@pytest.mark.parametrize("name", ["example-packet.json", "example-packet-photo-only.json"])
def test_example_packets_match_schema(name):
    schema = json.loads((ROOT / "packet/schema/capture-packet.schema.json").read_text())
    jsonschema.validate(json.loads((ROOT / "packet/schema" / name).read_text()), schema)


def test_criteria_yaml_parses():
    assert yaml.safe_load((ROOT / "criteria/spec/criteria.yaml").read_text())


def test_manifest_parses():
    assert json.loads((ROOT / "data/manifest.json").read_text())
