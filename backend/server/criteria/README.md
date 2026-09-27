# Criteria engine

Owner: WS-09. Brief: `docs/build/ws-09-criteria-engine.md`.

Stage name: `criteria`.

Inputs: scene/scene.json, reads/reads.json, objects/objects_3d.json, objects/view_requests.json, coverage/coverage.npz, coverage/bands.json, coverage/points.ply, coverage/scene_draft.json, coverage/coverage_report.json, scale/scale.json.

Outputs: verdicts/verdict.{A,B,C,S}.json, verdicts/criteria_report.json, optional verdicts/placement.<profile>.json, sidecars verdicts/c1.<p>.raw.json, verdicts/c1.<p>.chain.json, verdicts/c2.sweep.json, verdicts/c3.facts.json, verdicts/c3.judge.json.

Reads one scene v0 folder and writes one verdict v0 per rules profile (A, B, C, S).

The 3D rules engine decides. It is the only path to a placement pass. When `HSS_CRITERIA_HYBRID` is on (the default), the PR #11 chain solver fills criterion cells the 3D engine left `unsure` with cause `unobserved` or `grazing` on the meter-wall chain. A chain `fail` propagates to the candidate. A chain `pass` decides that cell only and never lifts `candidates[].outcome` or `site.decision`. C2 can veto a passing candidate down to `unsure`. C2 never emits its own fail and never upgrades. C3 downgrades only. Photo-fact auto-pass stays off (`HSS_CRITERIA_C3_AUTOPASS`, flipped together with WS-08's `HSS_READS_PANEL_AUTOPASS`).

`views_needed` omits `view_angle_deg` until the verdict schema carries it. Ranking puts head-on shots of a non-meter chain wall, past-end shots, and downward ground shots ahead of other requests that unblock the same number of criteria.

```bash
uv run python -c "from server.criteria import evaluate_scene"
uv run pytest server/criteria/tests -q
make criteria-golden
```

`VISION_EXTERNAL_CALLS` defaults off (SD-67). `off`, or `HSS_CRITERIA_C3=off`, skips every external call. The C3 client reads only `OPENAI_API_KEY` and `XAI_API_KEY`. Ground material comes only from `ground.regions[].material`. `panel_recall` is copied from `reads.json` and is not recomputed here.
