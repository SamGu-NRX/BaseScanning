// Runs the browser's live follower in Node against relay + synthetic API, with the scenario clock
// sped up. This is the same code path a page uses, minus the DOM.

import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { followCapture } from "../public/live.js";
import { initialState, reduce, stageRows } from "../public/model.js";
import { createViewerServer } from "../relay.js";
import { createSyntheticApi } from "../synthetic-api.js";

let synthetic;
let viewer;
let origin;

before(async () => {
  synthetic = createSyntheticApi({ speed: 40 });
  const sPort = await synthetic.listen(0);
  viewer = createViewerServer({
    upstreams: { synthetic: { base: `http://127.0.0.1:${sPort}/v1`, label: "Synthetic", kind: "synthetic" } },
    publicDir: join(dirname(fileURLToPath(import.meta.url)), "..", "public"),
  });
  origin = `http://127.0.0.1:${await viewer.listen(0)}`;
});

after(async () => {
  await viewer.close();
  await synthetic.close();
});

function harness(captureId) {
  let state = reduce(initialState(), { type: "select", mode: "live", source: { key: "synthetic", label: "s", kind: "synthetic" }, captureId });
  const actions = [];
  const dispatch = (action) => {
    actions.push(action);
    state = reduce(state, action);
  };
  const follower = followCapture({
    sourceKey: "synthetic",
    captureId,
    session: state.session,
    dispatch,
    getState: () => state,
    fetchImpl: (path, init) => fetch(origin + path, init),
    sleep: (ms, signal) => new Promise((resolve) => {
      const t = setTimeout(resolve, ms / 20);
      signal.addEventListener("abort", () => clearTimeout(t), { once: true });
    }),
    online: () => true,
  });
  return { follower, actions, get state() { return state; }, set state(s) { state = s; } };
}

async function until(check, ms = 8000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (check()) return;
    await new Promise((r) => setTimeout(r, 25));
  }
  throw new Error("timed out");
}

test("follows a synthetic capture to its result and final model", async () => {
  const h = harness("cap_SYNTH_COMPLETE_L1");
  await until(() => h.state.preview.phase === "ready");
  h.follower.stop();
  assert.equal(h.state.status, "complete");
  assert.equal(h.state.counts.acknowledged, 102);
  assert.ok(stageRows(h.state).every((r) => r.status === "done"));
  assert.equal(h.state.result.body.outcome.kind, "eligible");
  assert.ok(h.state.preview.cloud.count > 1000);
  assert.equal(h.state.connection.phase, "stopped");
});

test("rides out a dropped connection without losing or repeating events", async () => {
  const h = harness("cap_SYNTH_DROPOUT_L2");
  await until(() => h.state.preview.phase === "ready", 12_000);
  h.follower.stop();
  assert.ok(h.actions.some((a) => a.type === "contact-failed"), "the outage was reported");
  assert.equal(h.state.counts.acknowledged, 102);
  const fileEvents = h.state.log.filter((e) => e.kind === "files").length;
  const seqs = h.state.log.map((e) => e.seq).filter((q) => q != null);
  assert.equal(new Set(seqs).size, seqs.length, "no event logged twice");
  assert.ok(fileEvents > 0);
});

test("reports a rejected packet as a failure with no result claimed", async () => {
  const h = harness("cap_SYNTH_FAILED_L3");
  await until(() => h.state.status === "failed" && h.state.result.phase === "ready");
  h.follower.stop();
  assert.equal(h.state.failures[0].code, "synthetic_depth_size");
  assert.equal(h.state.result.body.outcome, null);
  assert.equal(h.state.preview.phase, "none");
});

test("an unknown capture ends as gone", async () => {
  const h = harness("cap_SYNTH_UNKNOWN_L4");
  await until(() => h.state.connection.phase === "gone");
  await h.follower.done;
});

test("after a switch, the old follower cannot write into the new capture", async () => {
  const h = harness("cap_SYNTH_COMPLETE_L5");
  await until(() => h.state.counts.acknowledged > 0);
  h.state = reduce(h.state, { type: "select", mode: "live", source: { key: "synthetic", label: "s", kind: "synthetic" }, captureId: "cap_SYNTH_COMPLETE_L6" });
  const switched = h.state;
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(h.state, switched, "no action from the old session changed state");
  assert.ok(h.actions.length > 0);
  h.follower.stop();
});

test("a finished capture is polled at a bounded rate, even when the API ignores wait", async () => {
  // The synthetic API answers at once after its timeline ends, like an API that ignores `wait`.
  const h = harness("cap_SYNTH_COMPLETE_L7");
  await until(() => h.state.preview.phase === "ready");
  const before = h.actions.filter((a) => a.type === "events").length;
  await new Promise((r) => setTimeout(r, 500));
  const polls = h.actions.filter((a) => a.type === "events").length - before;
  h.follower.stop();
  // The harness divides sleeps by 20, so the 1 s floor becomes 50 ms: about 10 polls, not thousands.
  assert.ok(polls <= 15, `polled ${polls} times in 500 ms`);
});

test("an empty previewUrl fetches no preview, and long polls end by the next status refresh", async () => {
  const waits = [];
  let previewCalls = 0;
  const json = (body) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body });
  const fetchImpl = async (path) => {
    if (path.includes("/preview")) previewCalls += 1;
    if (path.endsWith("/healthz")) return json({ status: "ok", version: "stub" });
    if (path.includes("/events")) {
      const params = new URL(path, "http://x").searchParams;
      waits.push(Number(params.get("wait")));
      const events = params.get("after") === "0" ? [{ seq: 1, type: "verdict_ready", at: "x", data: { runId: "run_s", kind: "manual_review" } }] : [];
      return json({ status: "manual_review", next: 1, events });
    }
    if (path.endsWith("/result")) return json({ runId: "run_s", status: "manual_review", outcome: { kind: "manual_review", message: "m" }, previewUrl: "" });
    return json({ captureId: "cap_STUB_1", status: "manual_review", filesRegistered: 0, runId: "run_s" });
  };
  let state = reduce(initialState(), { type: "select", mode: "live", source: { key: "s", label: "s", kind: "synthetic" }, captureId: "cap_STUB_1" });
  const follower = followCapture({
    sourceKey: "s",
    captureId: "cap_STUB_1",
    session: state.session,
    dispatch: (a) => { state = reduce(state, a); },
    getState: () => state,
    fetchImpl,
    sleep: () => new Promise((r) => setTimeout(r, 5)),
    online: () => true,
  });
  await until(() => state.result.phase === "ready" && waits.length > 3);
  follower.stop();
  assert.equal(previewCalls, 0);
  assert.equal(state.preview.phase, "none");
  assert.ok(waits.every((w) => w <= 5), `waits ${waits.join(",")}`);
});

test("health and a failed preview are read again after a transient failure", async () => {
  let healthCalls = 0;
  let previewCalls = 0;
  const json = (body, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, arrayBuffer: async () => new ArrayBuffer(0) });
  const fetchImpl = async (path) => {
    if (path.endsWith("/healthz")) {
      healthCalls += 1;
      return healthCalls === 1 ? json({ errors: [{ code: "upstream_unreachable", message: "x" }] }, 502) : json({ status: "ok", version: "stub" });
    }
    if (path.includes("/preview")) {
      previewCalls += 1;
      if (previewCalls === 1) return json({ errors: [{ code: "preview_unreachable", message: "storage hiccup" }] }, 502);
      const ply = new TextEncoder().encode("ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nend_header\n1 2 3\n");
      return { ok: true, status: 200, headers: new Headers(), arrayBuffer: async () => ply.buffer };
    }
    if (path.includes("/events")) {
      const first = new URL(path, "http://x").searchParams.get("after") === "0";
      return json({ status: "complete", next: 1, events: first ? [{ seq: 1, type: "verdict_ready", at: "x", data: { runId: "run_s", kind: "eligible" } }] : [] });
    }
    if (path.endsWith("/result")) return json({ runId: "run_s", status: "complete", outcome: { kind: "eligible", message: "m" }, previewUrl: "withheld-by-viewer-relay" });
    return json({ captureId: "cap_STUB_2", status: "complete", filesRegistered: 0, runId: "run_s" });
  };
  let clock = 0;
  let state = reduce(initialState(), { type: "select", mode: "live", source: { key: "s", label: "s", kind: "synthetic" }, captureId: "cap_STUB_2" });
  const follower = followCapture({
    sourceKey: "s",
    captureId: "cap_STUB_2",
    session: state.session,
    dispatch: (a) => { state = reduce(state, a); },
    getState: () => state,
    fetchImpl,
    now: () => clock,
    // Each sleep advances the fake clock, so 5 s retry intervals pass without real waiting.
    sleep: (ms) => new Promise((r) => { clock += ms; setTimeout(r, 1); }),
    online: () => true,
  });
  const tick = setInterval(() => { clock += 1000; }, 5);
  try {
    await until(() => state.preview.phase === "ready" && state.identity != null, 5000);
  } finally {
    clearInterval(tick);
    follower.stop();
  }
  assert.ok(healthCalls >= 2);
  assert.equal(previewCalls, 2);
});

function stubFollower(fetchReply) {
  let clock = 0;
  let state = reduce(initialState(), { type: "select", mode: "live", source: { key: "s", kind: "synthetic" }, captureId: "cap_STUB_3" });
  const follower = followCapture({
    sourceKey: "s", captureId: "cap_STUB_3", session: state.session,
    dispatch: (a) => { state = reduce(state, a); }, getState: () => state,
    fetchImpl: (path) => fetchReply(path, clock), now: () => clock,
    sleep: (ms) => new Promise((resolve) => { clock += ms; setTimeout(resolve, 1); }),
    online: () => true,
  });
  return { follower, get state() { return state; } };
}

const stubJson = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
const stubStatus = () => stubJson({ runId: "run_s", status: "complete" });
const stubEvents = () => stubJson({ events: [], next: 0, status: "complete" });
const stubResult = (previewUrl = "") => stubJson({ runId: "run_s", status: "complete", outcome: { kind: "eligible" }, previewUrl });

test("terminal results are read even while every events request fails", async () => {
  let resultReads = 0;
  const h = stubFollower((path) => {
    if (path.includes("/events")) return stubJson({ errors: [{ code: "busy" }] }, 503);
    if (path.endsWith("/result")) { resultReads += 1; return stubResult(); }
    return stubStatus();
  });
  try {
    await until(() => h.state.result.phase === "ready");
    assert.equal(resultReads, 1);
    assert.equal(h.state.connection.phase, "reconnecting");
  } finally { h.follower.stop(); await h.follower.done; }
});

test("preview retries wait for Retry-After", async () => {
  const previewAt = [];
  const ply = "ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nend_header\n1 2 3\n";
  const h = stubFollower((path, clock) => {
    if (path.includes("/events")) return stubEvents();
    if (path.endsWith("/result")) return stubResult("withheld");
    if (path.includes("/preview")) {
      previewAt.push(clock);
      return previewAt.length === 1 ? stubJson({ errors: [{ code: "busy" }] }, 503, { "retry-after": "19" }) : new Response(ply);
    }
    return stubStatus();
  });
  try {
    await until(() => h.state.preview.phase === "ready");
    assert.equal(previewAt.length, 2);
    assert.ok(previewAt[1] - previewAt[0] >= 19_000);
  } finally { h.follower.stop(); await h.follower.done; }
});

test("a preview removed by a result revision triggers a fresh result read", async () => {
  let resultReads = 0;
  let previewReads = 0;
  const h = stubFollower((path) => {
    if (path.includes("/events")) return stubEvents();
    if (path.endsWith("/result")) { resultReads += 1; return stubResult(resultReads === 1 ? "withheld" : ""); }
    if (path.includes("/preview")) {
      previewReads += 1;
      return stubJson({ errors: [{ code: "preview_not_available" }] }, 404);
    }
    return stubStatus();
  });
  try {
    await until(() => resultReads >= 2 && h.state.result.body.previewUrl === "");
    assert.equal(resultReads, 2);
    assert.equal(previewReads, 1);
    assert.equal(h.state.preview.phase, "none");
  } finally { h.follower.stop(); await h.follower.done; }
});
