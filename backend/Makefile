# `ci` includes `validate` (committed fixtures only). Large PLYs and tars stay in `.fixtures/`.
.PHONY: setup lint test schemas hygiene fixtures gt validate validate-full ci gates-offline gates-update e2e e2e-local lamar criteria-golden evals e2e-dev orchestrator-e2e ios-kit result-golden reads-gate reads-extra weights

ONLY ?= small

setup:
	uv sync --frozen

lint:
	uv run --frozen ruff check .
	uv run --frozen ruff format --check .
	uv run --frozen lint-imports

# api extra only. `--all-extras` installs the gpu torch pin, and the OWLv2
# parity test then runs on agent-store B1 images and misses by one box.
test:
	uv run --frozen --extra api pytest -q

schemas:
	uv run --frozen python -m server.contracts.export_schemas --check

hygiene:
	uv run --frozen python scripts/check_repo_hygiene.py

fixtures:
	uv run --frozen python scripts/fetch_fixtures.py --only $(ONLY) $(if $(ITEM),--item $(ITEM),)

# Public Hugging Face weights onto house-capture-pipeline:/vol/hf.
# Local-dir <org>--<repo> and hub/models--<org>--<repo> (see server/weights/fetch.py).
weights:
	SMP_ENVIRONMENT=dev uv run modal run -e dev -m server.weights.fetch

gt:
	bash scripts/rebuild_gt.sh

validate:
	uv run --frozen python scripts/validate_fixtures.py

validate-full:
	uv run --frozen python scripts/validate_fixtures.py --full

ci: lint test schemas hygiene validate

# Scores the committed fixtures. Producers write under runs/ unless --update.
gates-offline:
	nice -n 10 uv run --frozen python -m server.eval.cli gates --config server/eval/gates.yaml --inputs data/fixtures --out runs/gates-offline/gates.json

gates-update:
	uv run --frozen python -m server.eval.producers --update --inputs data/fixtures
	uv run --frozen python -m server.eval.cli gates --refresh-hashes --config server/eval/gates.yaml --inputs data/fixtures
	$(MAKE) gates-offline

e2e:
	nice -n 10 uv run python -m server.eval.e2e --config server/eval/gates.yaml --inputs data/fixtures --out runs/e2e/latest

e2e-local:
	nice -n 10 uv run python -m server.eval.e2e --local --config server/eval/gates.yaml --inputs data/fixtures --out runs/e2e/local

lamar:
	nice -n 10 uv run python -m server.eval.lamar run --data $(HOME)/lamar --out runs/lamar

# Unit suite, then verdicts for the three ETH3D laser sites at /tmp/crit/<site>/.
# Laser PLYs come from `make fixtures ONLY=all`. C3 stays off.
criteria-golden:
	uv run --frozen pytest server/criteria/tests -q
	HSS_CRITERIA_C3=off VISION_EXTERNAL_CALLS=off uv run --frozen python -m server.criteria.run --scene data/fixtures/scenes/eth3d-facade/gt-laser/scene.json --out /tmp/crit/facade --no-c3
	HSS_CRITERIA_C3=off VISION_EXTERNAL_CALLS=off uv run --frozen python -m server.criteria.run --scene data/fixtures/scenes/eth3d-delivery/gt-laser/scene.json --out /tmp/crit/delivery --no-c3
	HSS_CRITERIA_C3=off VISION_EXTERNAL_CALLS=off uv run --frozen python -m server.criteria.run --scene data/fixtures/scenes/eth3d-electro/gt-laser/scene.json --out /tmp/crit/electro --no-c3

# One-command evaluation suite over the committed fixtures: runs the real
# criteria engine (profiles A/B/C/S/public, footprint3in, C3 off — the criteria
# -golden posture), scores the produced verdicts against the committed labels
# under gates.yaml's bars, geomscores the committed recon scenes against the
# committed reference scores, and writes runs/suite/latest/{suite.json,SUMMARY.md}.
# Laser PLYs come from `make gt`; the recon PLYs (points.ply under
# data/fixtures/scenes/*/colmap-*) are gitignored Modal-pipeline outputs
# (uv run modal run scripts/modal_scene.py). A stage that cannot run FAILS;
# nothing SKIPs.
evals:
	nice -n 10 uv run --frozen python -m server.eval.suite --config server/eval/gates.yaml --inputs data/fixtures --out runs/suite/latest

# SD-77: the live stack only (Modal dev capture-api, house-scanning bucket).
# CAPTURE_API_URL must be https://huntertcarver-dev--capture-api.modal.run
# SD-86: no CAPTURE_API_TOKEN. PACKET=path/to/packet.json (or its directory).
e2e-dev:
	PYTHONPATH=. uv run --frozen python scripts/e2e_dev.py --self-check
	PYTHONPATH=. uv run --frozen python scripts/e2e_dev.py $(if $(PACKET),--packet $(PACKET),)

# Burst11: the one-command local capture API. Serves loopback only (127.0.0.1);
# the iOS simulator shares the Mac's loopback, a physical phone does not.
# Writes backend/runs/local-api/session.json (mode 0600, gitignored) with the
# base URL, app and reviewer tokens, pid and git sha. Stop with Ctrl-C.
# The documented default (docs/local-capture-api.md) is port 8000; override
# with PORT=8123 make local-api. The empty-value guard catches an exported
# PORT= so the launcher never sees a bare --port.
PORT ?= 8000
ifeq ($(PORT),)
override PORT := 8000
endif
local-api:
	uv run --frozen --extra api python scripts/local_capture_api.py --host 127.0.0.1 --port $(PORT)

# Burst11: drives the committed mini-arkit packet against the local capture API
# over a real TCP socket — the launcher script starts as a subprocess on an
# ephemeral port, no TestClient.
local-api-smoke:
	uv run --frozen --extra api pytest tests/test_local_capture_api.py -q

orchestrator-e2e:
	STATE_BACKEND=memory STORAGE_BACKEND=local HSS_ORCH_STAGE_STUBS=1 VISION_EXTERNAL_CALLS=off SMP_ENVIRONMENT=dev \
		uv run --frozen pytest server/orchestrator/tests -q

# Refresh the committed schema copies and pack the zip the iOS team installs.
ios-kit:
	cp server/contracts/schemas/capture-packet.v0.4.schema.json ios-kit/schemas/capture-packet.v0.4.schema.json
	cp server/contracts/schemas/result.v0.schema.json ios-kit/schemas/result.v0.schema.json
	cp server/contracts/schemas/api.v1.schema.json ios-kit/schemas/api.v1.schema.json
	cp server/contracts/schemas/capture-packet.v0.4.schema.json ios-kit/schema/capture-packet.schema.json
	cp data/fixtures/packets/examples/example-packet.json ios-kit/examples/packets/example-packet.json
	cp data/fixtures/packets/examples/example-packet-photo-only.json ios-kit/examples/packets/example-packet-photo-only.json
	cp data/fixtures/packets/mini-arkit/packet.json ios-kit/examples/packets/example-packet-meter-stills.json
	rm -rf dist/ios-kit-stage dist/ios-kit-0.4.0.zip
	mkdir -p dist/ios-kit-stage/spec dist/ios-kit-stage/schema dist/ios-kit-stage/fixtures
	cp ios-kit/README.md ios-kit/VERSION ios-kit/CHANGELOG.md ios-kit/requirements.txt dist/ios-kit-stage/
	cp docs/capture-packet-spec.md dist/ios-kit-stage/spec/capture-packet-spec.md
	cp -R ios-kit/schemas ios-kit/examples ios-kit/prompts ios-kit/packetgen dist/ios-kit-stage/
	cp ios-kit/schema/capture-packet.schema.json dist/ios-kit-stage/schema/
	cp ios-kit/fixtures/LICENCE-NOTES.md dist/ios-kit-stage/fixtures/
	cp -R ios-kit/fixtures/mini-arkit ios-kit/fixtures/mini-arkit-lidar ios-kit/fixtures/mini-photo-only dist/ios-kit-stage/fixtures/
	cd dist/ios-kit-stage && zip -qr ../ios-kit-0.4.0.zip . -x '*__pycache__*'
	rm -rf dist/ios-kit-stage
	cmp server/contracts/schemas/capture-packet.v0.4.schema.json ios-kit/schemas/capture-packet.v0.4.schema.json

# Compose the committed synthetic fixture. ETH3D rows wait on make fixtures and make criteria-golden.
result-golden:
	uv run --frozen --extra result python -m server.result.compose \
	  --capture server/result/tests/data/capture.sample.json \
	  --run server/result/tests/data/run.ok.json \
	  --scene server/result/tests/data/synthetic \
	  --verdicts server/result/tests/data/synthetic/verdicts \
	  --out /tmp/res/synthetic \
	  --reads server/result/tests/data/reads.auto_fill.json \
	  --catalogue server/result/tests/data/prompts.en-US.json
	uv run --frozen --extra result python -m server.result.compose \
	  --capture server/result/tests/data/capture.sample.json \
	  --run server/result/tests/data/run.ok.json \
	  --scene server/result/tests/data/photo \
	  --verdicts server/result/tests/data/photo/verdicts \
	  --out /tmp/res/photo \
	  --catalogue server/result/tests/data/prompts.en-US.json
	uv run --frozen --extra result python -m server.result.compose \
	  --capture server/result/tests/data/capture.sample.json \
	  --run server/result/tests/data/run.ok.json \
	  --scene server/result/tests/data/synthetic \
	  --verdicts server/result/tests/data/reject/verdicts \
	  --out /tmp/res/reject \
	  --catalogue server/result/tests/data/prompts.en-US.json \
	  --show-reject
	uv run --frozen --extra result python -c "from server.contracts.result import ResultResponse; import json; \
	[ResultResponse.model_validate(json.load(open(f'/tmp/res/{name}/result.json'))) for name in ('synthetic','photo','reject')]; print('ok synthetic photo reject')"

reads-gate:
	uv run --frozen python scripts/reads_gate.py --set meter --set panel --set extra --out /tmp/reads-gate

reads-extra:
	uv sync --frozen --extra reads
	uv run --frozen --extra reads python -c "import openai, zxingcpp"

include infra/deploy.mk
