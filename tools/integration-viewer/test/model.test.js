import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { connectionView, initialState, reduce, resultExpected, resultKey, stageRows, stageTimingUninterpretable, viewsToShow } from "../public/model.js";

const source = { key: "t", label: "test", kind: "synthetic" };

function selected(captureId = "cap_TEST_1") {
  return reduce(initialState(), { type: "select", mode: "live", source, captureId });
}

function run(state, ...actions) {
  return actions.reduce((s, a) => reduce(s, { session: s.session, ...a }), state);
}

const committed = (seq, paths) => ({ seq, type: "files_committed", at: "2026-09-27T00:00:00Z", data: { paths, committed: paths.length } });
const stage = (seq, name, status, extra = {}) => ({ seq, type: "stage", at: "2026-09-27T00:00:00Z", data: { stage: name, status, attempt: 1, runId: "run_a", ...extra } });
const events = (list, next = list.at(-1)?.seq ?? 0, status = "uploading") => ({ type: "events", at: 1000, body: { status, next, events: list } });

describe("events", () => {
  test("the same events delivered twice add no files and no arrivals", () => {
    const batch = events([committed(1, ["stills/a.jpg", "stills/b.jpg"])]);
    const once = run(selected(), events([]), batch);
    const twice = run(once, batch);
    assert.equal(twice.counts.acknowledged, 2);
    assert.equal(twice.arrivals.length, 1);
    assert.equal(twice.arrivalSerial, once.arrivalSerial);
    assert.equal(twice.log.length, once.log.length);
  });

  test("a path acknowledged again under a new seq counts once and animates nothing", () => {
    const s = run(selected(), events([]), events([committed(1, ["depth/k1.bin"])]), events([committed(2, ["depth/k1.bin"])]));
    assert.equal(s.counts.acknowledged, 1);
    assert.equal(s.arrivals.length, 1);
  });

  test("events apply in seq order and the cursor never moves backwards", () => {
    const s = run(selected(), events([stage(3, "validate", "done"), stage(2, "validate", "running")], 3));
    assert.equal(stageRows(s).find((r) => r.id === "validate").status, "done");
    assert.equal(s.cursor, 3);
    const older = run(s, events([], 1));
    assert.equal(older.cursor, 3);
    const ahead = run(older, events([], 9));
    assert.equal(ahead.cursor, 9);
  });

  test("an older stage update does not overwrite a newer one", () => {
    const s = run(selected(), events([stage(5, "dense", "done", { durationS: 2 })]), events([stage(4, "dense", "running")], 5));
    assert.equal(stageRows(s).find((r) => r.id === "dense").status, "done");
  });

  test("the first batch after connecting is backlog; later arrivals are not", () => {
    const s = run(selected(), events([committed(1, ["stills/a.jpg"])]), events([committed(2, ["stills/b.jpg"])]));
    assert.deepEqual(s.arrivals.map((a) => a.backlog), [true, false]);
  });

  test("unknown event types and malformed rows do not throw or change the pipeline", () => {
    const weird = [
      { seq: 1, type: "retry_finalize", at: "x", data: {} },
      { seq: 2, type: "stage", data: "not an object" },
      { seq: "3", type: "files_committed", data: { paths: ["x"] } },
      { type: "hint" },
      null,
      { seq: 4, type: "files_committed", data: { paths: [7, "stills/ok.jpg", null] } },
    ];
    const s = run(selected(), { type: "events", at: 1, body: { status: "brand_new_status", next: 4, events: weird } });
    assert.deepEqual(s.unknownTypes, ["retry_finalize"]);
    assert.equal(s.counts.acknowledged, 1);
    assert.equal(s.status, "brand_new_status");
    assert.ok(stageRows(s).every((r) => r.status === "unreported"));
    const nonsense = run(s, { type: "events", body: "garbage" }, { type: "events", body: { events: "nope" } }, { type: "status", body: null });
    assert.equal(nonsense.counts.acknowledged, 1);
  });

  test("a stage this viewer does not know is shown after the known ones", () => {
    const s = run(selected(), events([stage(1, "denoise_v2", "running")]));
    assert.equal(stageRows(s).at(-1).id, "denoise_v2");
  });

  test("joining a capture with two runs keeps the latest run the status named", () => {
    const runB = (seq, name, status) => ({ ...stage(seq, name, status), data: { stage: name, status, attempt: 1, runId: "run_b" } });
    const s = run(
      selected(),
      { type: "status", body: { captureId: "cap_TEST_1", status: "processing", runId: "run_b" } },
      { ...events([stage(1, "validate", "done"), stage(2, "poses", "running")]), catchUp: true },
      { ...events([runB(3, "validate", "running")]), catchUp: true },
    );
    assert.equal(s.currentRunId, "run_b");
    assert.equal(stageRows(s).find((r) => r.id === "validate").status, "running");
    const withResult = run(s, { type: "result", body: { runId: "run_b", status: "complete", outcome: { kind: "eligible" } } });
    assert.equal(withResult.result.phase, "ready");
  });

  test("the before-you-connected total survives more batches than the arrival queue keeps", () => {
    const batches = Array.from({ length: 30 }, (_, i) => committed(i + 1, [`keyframes/k${i}.jpg`]));
    const s = run(selected(), { ...events(batches), catchUp: true });
    assert.equal(s.arrivals.length < 30, true);
    assert.equal(s.counts.backlog, 30);
  });

  test("history read while catching up counts as backlog on every page", () => {
    const s = run(selected(), { ...events([committed(1, ["stills/a.jpg"])]), catchUp: true }, { ...events([committed(2, ["stills/b.jpg"])]), catchUp: true }, { ...events([committed(3, ["stills/c.jpg"])]), catchUp: false });
    assert.deepEqual(s.arrivals.map((a) => a.backlog), [true, true, false]);
  });

});

describe("sessions", () => {
  test("callbacks from an earlier selection are ignored", () => {
    const first = selected("cap_FIRST_1");
    const oldSession = first.session;
    const second = reduce(first, { type: "select", mode: "live", source, captureId: "cap_SECOND_1" });
    const after = reduce(second, { session: oldSession, ...events([committed(1, ["stills/a.jpg"])]) });
    assert.equal(after, second);
    const stale = reduce(second, { session: oldSession, type: "result", body: { status: "complete", outcome: { kind: "eligible" } } });
    assert.equal(stale.result.phase, "none");
  });

  test("a status body for another capture is ignored", () => {
    const s = run(selected("cap_MINE_1"), { type: "status", body: { captureId: "cap_OTHER_1", status: "complete", filesRegistered: 9 } });
    assert.equal(s.status, null);
    assert.equal(s.counts.registered, null);
  });
});

describe("connection", () => {
  test("a failed contact keeps the evidence and reports reconnecting or offline", () => {
    const withData = run(selected(), events([committed(1, ["stills/a.jpg"]), stage(2, "validate", "done")]));
    const failed = run(withData, { type: "contact-failed", error: "network error", offline: false });
    assert.equal(failed.counts.acknowledged, 1);
    assert.equal(stageRows(failed)[0].status, "done");
    assert.equal(connectionView(failed, 2000).phase, "reconnecting");
    const offline = run(withData, { type: "contact-failed", error: "network error", offline: true });
    assert.equal(connectionView(offline, 2000).phase, "offline");
  });

  test("a live connection with no reply for too long reads as stale", () => {
    const s = run(selected(), { type: "contact-ok", at: 10_000 });
    assert.equal(connectionView(s, 20_000).phase, "live");
    assert.equal(connectionView(s, 60_000).phase, "stale");
  });

  test("gone and stopped keep what was already seen", () => {
    const s = run(selected(), events([committed(1, ["stills/a.jpg"])]), { type: "gone", error: "404" });
    assert.equal(s.connection.phase, "gone");
    assert.equal(s.counts.acknowledged, 1);
  });
});

describe("results", () => {
  test("verdict_ready alone claims no outcome and no model", () => {
    const s = run(selected(), events([{ seq: 1, type: "verdict_ready", at: "x", data: { runId: "run_a", kind: "eligible" } }]));
    assert.ok(resultExpected(s));
    assert.equal(s.result.phase, "none");
    assert.equal(s.preview.phase, "none");
  });

  test("a minimal result while processing stays pending", () => {
    const s = run(selected(), { type: "result", body: { runId: "run_a", status: "processing", outcome: null, viewsNeeded: [], memberActions: [] } });
    assert.equal(s.result.phase, "pending");
    const refreshing = run(s, { type: "result-loading" });
    assert.equal(refreshing.result.phase, "pending");
    assert.equal(refreshing.result.body, s.result.body);
  });

  test("a model with zero points is empty, not ready", () => {
    const withResult = run(selected(), { type: "result", body: { runId: "run_a", status: "manual_review", outcome: { kind: "manual_review" } } });
    const s = run(withResult, { type: "preview", runId: "run_a", cloud: { count: 0, kept: 0, positions: new Float32Array(), generated: null, bounds: null } });
    assert.equal(s.preview.phase, "empty");
  });

  test("an expired capture asks for its result", () => {
    assert.ok(resultExpected(run(selected(), events([], 0, "expired"))));
  });

  test("a complete status without an outcome is still pending", () => {
    const s = run(selected(), { type: "result", body: { runId: "run_a", status: "complete", outcome: null } });
    assert.equal(s.result.phase, "pending");
    const failed = run(selected(), { type: "result", body: { runId: "run_a", status: "failed", outcome: null } });
    assert.equal(failed.result.phase, "ready");
  });

  test("a model for a run other than the result's is dropped", () => {
    const withResult = run(selected(), { type: "result", body: { runId: "run_a", status: "complete", outcome: { kind: "eligible" } } });
    const s = run(withResult, { type: "preview", runId: "run_b", cloud: { count: 5, kept: 5, positions: new Float32Array(15), generated: null, bounds: null } });
    assert.equal(s.preview.phase, "none");
  });

  test("an unreadable model is unsupported", () => {
    const s = run(selected(), { type: "preview-error", runId: "run_a", unsupported: true, error: "not a PLY file" });
    assert.equal(s.preview.phase, "unsupported");
  });
});

test("a stage left running when the capture settles shows no reported end", () => {
  const s = run(selected(), events([stage(1, "validate", "running")], 1, "failed"));
  assert.equal(stageRows(s)[0].status, "unended");
  const open = run(selected(), events([stage(1, "validate", "running")], 1, "processing"));
  assert.equal(stageRows(open)[0].status, "running");
});

test("requested views show before any result exists, and the result's own list wins once it arrives", () => {
  const retake = run(selected(), events([{ seq: 1, type: "retake_request", at: "x", data: { runId: "run_a", viewsNeeded: ["vn3"], memberActions: [] } }]));
  assert.equal(retake.result.body, null);
  assert.deepEqual(viewsToShow(retake), [{ id: "vn3", title: null }]);
  const withResult = run(retake, { type: "result", body: { runId: "run_a", status: "needs_views", outcome: null, viewsNeeded: [{ id: "vn3", prompt: { title: "Show the ground left of the meter", body: "b" } }] } });
  assert.deepEqual(viewsToShow(withResult), [{ id: "vn3", title: "Show the ground left of the meter" }]);
});

test("a ready result's empty views list replaces an earlier retake for the same run", () => {
  const s = run(
    selected(),
    events([{ seq: 1, type: "retake_request", at: "x", data: { runId: "run_a", viewsNeeded: ["vn1"], memberActions: [] } }]),
    { type: "result", body: { runId: "run_a", status: "complete", viewsNeeded: [], outcome: { kind: "eligible", viewsNeeded: [] } } },
  );
  assert.deepEqual(viewsToShow(s), []);
  const pending = run(selected(), events([{ seq: 1, type: "retake_request", at: "x", data: { runId: "run_a", viewsNeeded: ["vn1"], memberActions: [] } }]), { type: "result", body: { runId: "run_a", status: "processing", viewsNeeded: [], outcome: null } });
  assert.deepEqual(viewsToShow(pending), [{ id: "vn1", title: null }]);
});

test("a failed model reload keeps the model already shown", () => {
  const cloud = { count: 3, kept: 3, positions: new Float32Array(9), generated: null, bounds: { min: [0, 0, 0], max: [1, 1, 1] } };
  const shown = run(selected(), { type: "result", body: { runId: "run_a", status: "complete", outcome: { kind: "eligible" }, previewUrl: "withheld-by-viewer-relay" } }, { type: "preview", runId: "run_a", cloud });
  const failed = run(shown, { type: "preview-loading", runId: "run_a" }, { type: "preview-error", runId: "run_a", error: "network error" });
  assert.equal(failed.preview.phase, "ready");
  assert.equal(failed.preview.cloud, cloud);
});

test("each accepted result is a new revision, and a same-run model stays up while it reloads", () => {
  const cloud = { count: 3, kept: 3, positions: new Float32Array(9), generated: null, bounds: { min: [0, 0, 0], max: [1, 1, 1] } };
  const a = run(selected(), { type: "result", body: { runId: "run_a", status: "complete", outcome: { kind: "eligible" }, previewUrl: "withheld-by-viewer-relay" } }, { type: "preview", runId: "run_a", cloud });
  const b = run(a, { type: "result", body: { runId: "run_a", status: "complete", outcome: { kind: "eligible" }, previewUrl: "withheld-by-viewer-relay" } }, { type: "preview-loading", runId: "run_a" });
  assert.equal(b.result.revision, a.result.revision + 1);
  assert.equal(b.preview.phase, "ready");
});

test("a retake names the views the server asked for", () => {
  const s = run(selected(), events([{ seq: 1, type: "retake_request", at: "x", data: { runId: "run_a", viewsNeeded: ["vn3", "vn4"], memberActions: [] } }]));
  assert.deepEqual(s.retake.views, ["vn3", "vn4"]);
  assert.match(s.log.at(-1).text, /vn3, vn4/);
});

test("a refreshed result without a preview drops the earlier model", () => {
  const cloud = { count: 3, kept: 3, positions: new Float32Array(9), generated: null, bounds: { min: [0, 0, 0], max: [1, 1, 1] } };
  const shown = run(selected(), { type: "result", body: { runId: "run_a", status: "complete", outcome: { kind: "eligible" }, previewUrl: "withheld-by-viewer-relay" } }, { type: "preview", runId: "run_a", cloud });
  assert.equal(shown.preview.phase, "ready");
  const refreshed = run(shown, { type: "result", body: { runId: "run_a", status: "complete", outcome: { kind: "eligible" }, previewUrl: "" } });
  assert.equal(refreshed.preview.phase, "none");
});

test("a model whose points are all unusable is empty, not ready", () => {
  const withResult = run(selected(), { type: "result", body: { runId: "run_a", status: "complete", outcome: { kind: "eligible" } } });
  const s = run(withResult, { type: "preview", runId: "run_a", cloud: { count: 2, kept: 0, positions: new Float32Array(0), generated: null, bounds: null } });
  assert.equal(s.preview.phase, "empty");
});


test("a failed refresh keeps the result already shown", () => {
  const s = run(selected(), { type: "result", body: { runId: "run_a", status: "manual_review", outcome: { kind: "manual_review" } } }, { type: "result-loading" }, { type: "result-error", error: "network error" });
  assert.equal(s.result.phase, "ready");
  assert.equal(s.result.body.outcome.kind, "manual_review");
  assert.equal(s.result.error, "network error");
});

test("a null result refresh keeps the accepted eligible result and shows a refresh error", () => {
  const accepted = run(selected(), { type: "result", body: { runId: "run_a", status: "complete", outcome: { kind: "eligible" } } });
  const refreshed = run(accepted, { type: "result-loading" }, { type: "result", body: null });
  assert.equal(refreshed.result.phase, "ready");
  assert.equal(refreshed.result.body, accepted.result.body);
  assert.equal(refreshed.result.revision, accepted.result.revision);
  assert.equal(refreshed.preview, accepted.preview);
  assert.equal(refreshed.result.error, "Result was not a JSON object");
  assert.equal(run(selected(), { type: "result", body: null }).result.phase, "error");
});

for (const body of [null, [], "invalid"]) {
  test(`a pending result followed by ${JSON.stringify(body)} clears the body so its error can render`, () => {
    const pending = run(selected(), { type: "result", body: { runId: "run_a", status: "processing", outcome: null } });
    assert.equal(pending.result.phase, "pending");
    const failed = run(pending, { type: "result-loading" }, { type: "result", body });
    assert.equal(failed.result.phase, "error");
    assert.equal(failed.result.body, null);
    assert.equal(failed.result.error, "Result was not a JSON object");
    assert.equal(failed.result.revision, pending.result.revision);
  });
}

test("a pending result followed by a fetch failure clears the body so its error can render", () => {
  const pending = run(selected(), { type: "result", body: { runId: "run_a", status: "processing", outcome: null } });
  const failed = run(pending, { type: "result-loading" }, { type: "result-error", error: "network error" });
  assert.equal(failed.result.phase, "error");
  assert.equal(failed.result.body, null);
  assert.equal(failed.result.error, "network error");
});

test("the eligible illustration leaves no check unsure", async () => {
  const { resultBody } = await import("../public/scenario.js");
  const body = resultBody("complete", "complete");
  assert.equal(body.outcome.kind, "eligible");
  assert.ok(body.criteria.every((c) => c.outcome === "pass"));
});

test("stage timing that is zero, tiny or missing is flagged as uninterpretable, not as fake processing", () => {
  // A real run reported durationS 0.0 for every stage; the flag must describe timing only.
  const zero = ["validate", "poses", "scale"].map((n, i) => stage(i + 1, n, "done", { durationS: 0 }));
  assert.ok(stageTimingUninterpretable(run(selected(), events(zero))));
  const missing = ["validate", "poses", "scale"].map((n, i) => stage(i + 1, n, "done"));
  assert.ok(stageTimingUninterpretable(run(selected(), events(missing))));
  const measured = ["validate", "poses", "scale"].map((n, i) => stage(i + 1, n, "done", { durationS: n === "poses" ? 3 : 0 }));
  assert.ok(!stageTimingUninterpretable(run(selected(), events(measured))));
});

test("the timing caution makes no claim about whether real processing ran", async () => {
  const { readFile } = await import("node:fs/promises");
  const html = await readFile(new URL("../public/index.html", import.meta.url), "utf8");
  const caution = /<p class="caution" id="timing-caution"[^>]*>([^<]*)<\/p>/.exec(html)?.[1] ?? "";
  assert.match(caution, /do not identify whether real processing ran/);
  assert.doesNotMatch(caution, /placeholder|not real|stub|fake/i);
});

test("the illustration states no clearance thresholds of its own", async () => {
  const { resultBody } = await import("../public/scenario.js");
  const criteria = resultBody("complete", "complete").criteria;
  assert.ok(criteria.length > 0);
  assert.ok(criteria.every((c) => !("thresholdFt" in c)), "thresholds belong in sourced rules files");
});

describe("run identity: the status names the run shown", () => {
  const status = (runId, st = "processing") => ({ type: "status", body: { captureId: "cap_TEST_1", status: st, runId } });
  const runEvent = (seq, runId, type = "stage", data = {}) => ({ seq, type, at: "x", data: type === "stage" ? { stage: "validate", status: "running", attempt: 1, runId, ...data } : { runId, ...data } });
  const eligible = (runId) => ({ type: "result", body: { runId, status: "complete", outcome: { kind: "eligible" } } });

  test("new run: a status naming a new run switches to it and clears the old run's verdict, retake, result and model", () => {
    const cloud = { count: 3, kept: 3, positions: new Float32Array(9), generated: null, bounds: { min: [0, 0, 0], max: [1, 1, 1] } };
    const a = run(selected(), status("run_a"), events([runEvent(1, "run_a", "verdict_ready", { kind: "eligible" })]), eligible("run_a"), { type: "preview", runId: "run_a", cloud });
    assert.equal(a.preview.phase, "ready");
    const b = run(a, status("run_b"));
    assert.equal(b.currentRunId, "run_b");
    assert.equal(b.verdict, null);
    assert.equal(b.result.phase, "none");
    assert.equal(b.preview.phase, "none");
    assert.ok(Number.isInteger(b.result.revision));
    assert.notEqual(resultKey(a), resultKey(b));
  });

  test("an event naming a run the status has not named asks for a status refresh and switches nothing", () => {
    const a = run(selected(), status("run_a"), eligible("run_a"));
    const b = run(a, events([runEvent(2, "run_b", "retake_request", { viewsNeeded: ["vn1"], memberActions: [] })], 2));
    assert.equal(b.currentRunId, "run_a");
    assert.equal(b.result.phase, "ready");
    assert.equal(b.statusRefreshWanted, true);
    const c = run(b, status("run_b"));
    assert.equal(c.statusRefreshWanted, false);
    assert.equal(c.currentRunId, "run_b");
    assert.deepEqual(viewsToShow(c), [{ id: "vn1", title: null }], "run_b's retake, recorded earlier, shows once run_b is current");
  });

  test("no events: a run the status named without events switches when the status names the next run", () => {
    const s = run(
      selected(),
      status("run_a", "complete"),
      { ...events([committed(1, ["stills/a.jpg"])], 1), catchUp: true },
      { ...events([runEvent(2, "run_b", "retake_request", { viewsNeeded: ["vn1"], memberActions: [] })], 2), catchUp: false },
      status("run_b", "needs_views"),
    );
    assert.equal(s.currentRunId, "run_b");
    assert.deepEqual(viewsToShow(s), [{ id: "vn1", title: null }]);
  });

  test("old retake: a late retake or result from a superseded run changes nothing shown", () => {
    const s = run(selected(), status("run_a"), status("run_b"), eligible("run_b"), events([runEvent(3, "run_a", "retake_request", { viewsNeeded: ["vn_old"], memberActions: [] })], 3), eligible("run_a"));
    assert.equal(s.currentRunId, "run_b");
    assert.equal(s.result.body.runId, "run_b");
    assert.deepEqual(viewsToShow(s), []);
    assert.equal(s.statusRefreshWanted, false);
  });

  test("a delayed status naming a retired run does not bring it back", () => {
    const current = run(selected(), status("run_a"), status("run_b"), eligible("run_b"));
    const s = run(current, { type: "status", body: { runId: "run_a", status: "failed", filesRegistered: 99 } });
    assert.equal(s.currentRunId, "run_b");
    assert.equal(s.result.phase, "ready");
    assert.equal(s, current, "no retired-run status fields are applied");
    const forced = run(current, events([runEvent(2, "run_unknown", "retake_request")], 2));
    assert.equal(forced.statusRefreshWanted, true);
    const refreshed = run(forced, { type: "status", body: { runId: "run_a", status: "failed", filesRegistered: 99 } });
    assert.deepEqual(refreshed, { ...forced, statusRefreshWanted: false }, "the completed refresh clears only the request flag");
  });

  test("a result without a nonempty run identity is deferred", () => {
    const current = run(selected(), status("run_a"), status("run_b"), eligible("run_b"));
    for (const runId of [undefined, null, 42, ""]) {
      const s = run(current, { type: "result", body: { runId, status: "complete", outcome: { kind: "not_eligible" } } });
      assert.equal(s, current);
    }
  });

  test("reproduced: a run the status named without events, then retired, cannot return through a late event", () => {
    // Codex 4114623680: status named A (no events), then B; a delayed event for A made A current again.
    const s = run(selected(), status("run_a"), status("run_b"), eligible("run_b"), events([runEvent(9, "run_a", "retake_request", { viewsNeeded: ["vn_old"], memberActions: [] })], 9));
    assert.equal(s.currentRunId, "run_b");
    assert.equal(s.result.phase, "ready");
    assert.equal(s.result.body.runId, "run_b");
    assert.deepEqual(viewsToShow(s), []);
  });

  test("a result for a run not yet named is held until the status names that run", () => {
    const a = run(selected(), status("run_a"), { type: "result", body: { runId: "run_z", status: "complete", outcome: { kind: "eligible" } } });
    assert.equal(a.currentRunId, "run_a");
    assert.equal(a.result.phase, "none");
    assert.equal(a.statusRefreshWanted, true);
    const b = run(a, status("run_z"), { type: "result", body: { runId: "run_z", status: "complete", outcome: { kind: "eligible" } } });
    assert.equal(b.result.phase, "ready");
  });

  test("an older run's result does not disturb the current run's pending result", () => {
    const s = run(selected(), status("run_a"), status("run_b"), { type: "result", body: { runId: "run_b", status: "processing", outcome: null } }, eligible("run_a"));
    assert.equal(s.result.phase, "pending");
    assert.equal(s.result.body.runId, "run_b");
  });

  test("the status's run decides which stages show", () => {
    const s = run(selected(), status("run_a"), events([runEvent(1, "run_a", "stage", { status: "done" }), runEvent(2, "run_b", "stage", { status: "running" })]));
    assert.equal(stageRows(s).find((r) => r.id === "validate").status, "done");
    const t = run(s, status("run_b"));
    assert.equal(stageRows(t).find((r) => r.id === "validate").status, "running");
  });
});
