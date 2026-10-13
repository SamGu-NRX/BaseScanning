# Independent verification. Every target runs from this folder; reports go to
# ~/house-scanning-data/reports/ (outside git) because runs can show dataset frames.

REF ?= origin/t3/ios-mvf
SERVER_REF ?= origin/t3/server
# Set SERVER_URL to use a running server (the hosted API) instead of starting one from SERVER_REF.
SERVER_URL ?=
SERVER = $(if $(SERVER_URL),--server-url $(SERVER_URL),--server-ref $(SERVER_REF))
REPLAY ?= $(HOME)/house-scanning-data/replays/advio-20-0040-0075
ARGS ?=

.PHONY: test sim sim-app sim-probe e2e scoreboard

test:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest -q

# Build REF for the Simulator, run it with ARGS (e.g. ARGS="--replay <dir> --autopilot"),
# screenshot every STATE.
sim:
	uv run python -m hsverify.simrun --ref $(REF) $(ARGS)

# The app at REF on the real ADVIO replay (C3), driven by its autopilot, uploading to the server
# started from SERVER_REF (or at SERVER_URL): a screenshot per STATE in ~/house-scanning-data/reports/sim/.
# Fails unless the run walks the flow in order (meter, close-up, wall walk, feature review,
# upload, result) and uploads after every gap request it shows, so each recapture round ends in
# an upload. Reaching `result` alone would also pass an app that showed a cached answer. A round
# the app never opens leaves no STATE to check. The scan bundle is copied into the report as
# scan.zip. State names are ScanPhase in ios/HouseScan/Contract/ScanContract.swift.
sim-app:
	uv run python -m hsverify.simrun --ref $(REF) --replay $(REPLAY) --autopilot \
		$(SERVER) --extra-arg=-autopilotHold --extra-arg=3 --idle 60 --timeout 900 \
		--require-order findMeter,meterCloseUp,wallWalk,markFeatures,uploading,result \
		--require-then gapRequest:uploading --require-export --label replay $(ARGS)

# Every case in e2e/cases/ plus the ETH3D facade scene through the server's API at SERVER_REF;
# each result is validated against server/schemas/result.schema.json at the same ref.
e2e:
	uv run python -m hsverify.e2e $(SERVER) --scene scenes/eth3d-facade/case.json $(ARGS)

# Self-test of the runner against a fixture app that logs a known STATE sequence.
sim-probe:
	uv run python -m hsverify.simrun --ref HEAD --project verification/fixtures/state-probe/StateProbe.xcodeproj \
		--scheme StateProbe --until probe_done --extra-arg=-probeArg --label probe

# Every open PR with its CI, and every overnight-plan metric with its evidence or gap. Writes
# SCOREBOARD.md and scoreboard/scoreboard.json. ARGS=--slow also runs the slow test probes;
# ARGS="--slow S2-1" runs only that one.
scoreboard:
	uv run python -m hsverify.scoreboard $(ARGS)
