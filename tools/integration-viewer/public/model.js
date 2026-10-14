// Viewer state. A pure reducer: every network callback and every replay step becomes an action,
// and the view renders whatever state results. Nothing here reads the clock or the network.
//
// Each capture selection gets a new `session` number. Every other action carries the session it
// was issued under, and the reducer drops actions from an older session. That is what keeps a
// slow long-poll for capture A from writing into the view after the user switched to capture B.

/** Server stage ids in pipeline order, with the words the viewer shows for each. */
export const STAGES = [
  ["validate", "Checking the packet"],
  ["poses", "Placing photos in 3D"],
  ["objects", "Finding meter and obstacles"],
  ["reads", "Reading the meter"],
  ["scale", "Setting real-world scale"],
  ["dense", "Building the wall surface"],
  ["coverage", "Mapping what was seen"],
  ["scene", "Assembling the wall model"],
  ["objects_3d", "Locating objects in 3D"],
  ["criteria", "Checking placement rules"],
  ["result", "Writing the answer"],
];

const KNOWN_EVENTS = new Set([
  "files_committed",
  "hint",
  "capture_check",
  "stage",
  "verdict_ready",
  "retake_request",
  "review_started",
  "review_completed",
  "failed",
  "confirmation",
]);

/** Statuses after which the server has written, or will not write, a result. */
export const RESULT_STATUSES = new Set(["needs_views", "complete", "manual_review", "failed", "expired"]);

const LOG_LIMIT = 60;
const ARRIVAL_LIMIT = 24;

export function initialState() {
  return {
    session: 0,
    mode: "idle", // "idle" | "live" | "replay"
    source: null, // { key, label, kind: "backend" | "synthetic" | "replay" }
    captureId: null,
    connection: { phase: "idle", lastContactAt: null, lastError: null, failures: 0 },
    identity: null, // health body from the selected source
    cursor: 0,
    seen: new Set(),
    firstBatchDone: false,
    status: null,
    counts: { registered: null, acknowledged: 0, listed: null, backlog: 0 },
    files: new Map(), // path -> { seq, group }
    arrivals: [], // newest last; { id, seq, count, groups, backlog }
    arrivalSerial: 0,
    runs: new Map(), // runId -> Map(stage -> { status, attempt, durationS, errorCode, seq })
    currentRunId: null, // named by the status; see setCurrentRun
    retiredRuns: new Set(), // runs that were current before; nothing brings them back
    statusRefreshWanted: false, // an event or result named a run the status has not; ask again
    verdicts: new Map(), // runId -> verdict_ready event, for every run
    retakes: new Map(), // runId -> retake_request event, for every run
    hints: [],
    captureCheck: null,
    retake: null, // the current run's entry in `retakes`
    failures: [],
    verdict: null, // the current run's entry in `verdicts`
    review: null,
    unknownTypes: [],
    result: { phase: "none", body: null, error: null, revision: 0 },
    preview: { phase: "none", runId: null, cloud: null, error: null },
    log: [],
  };
}

/** A file's display group, from its packet path. Display only; never used for a decision. */
export function fileGroup(path) {
  const top = String(path).split("/")[0] ?? "";
  if (top === "keyframes" || top === "stills" || top === "truedepth") return "photo";
  if (top === "depth" || top === "confidence") return "depth";
  if (top === "mesh") return "mesh";
  return "other";
}

export function reduce(state, action) {
  if (action.type === "select") return select(state, action);
  if (action.session !== state.session) return state; // stale callback from an earlier selection
  switch (action.type) {
    case "health":
      return { ...state, identity: action.body };
    case "contact-ok":
      return {
        ...state,
        connection: { phase: "live", lastContactAt: action.at, lastError: null, failures: 0 },
      };
    case "contact-failed":
      return {
        ...state,
        connection: {
          ...state.connection,
          phase: action.offline ? "offline" : "reconnecting",
          lastError: action.error,
          failures: state.connection.failures + 1,
        },
      };
    case "gone":
      return {
        ...state,
        connection: { ...state.connection, phase: "gone", lastError: action.error },
      };
    case "stopped":
      return { ...state, connection: { ...state.connection, phase: "stopped" } };
    case "status":
      return applyStatus(state, action.body);
    case "events":
      return applyEvents(state, action.body, action.at, action.catchUp);
    case "result-loading":
      // Refreshing a result already shown keeps showing it until the new one arrives.
      return { ...state, result: { ...state.result, phase: ["ready", "pending"].includes(state.result.phase) ? state.result.phase : "loading", error: null } };
    case "result":
      return applyResult(state, action.body);
    case "result-error":
      // A failed refresh keeps a result already shown; it is still what the server last said.
      return { ...state, result: { ...state.result, phase: state.result.phase === "ready" ? "ready" : "error", error: action.error } };
    case "preview-loading":
      // Re-reading the same run's model keeps the one on screen until the new one arrives.
      if (state.preview.phase === "ready" && state.preview.runId === action.runId) return state;
      return { ...state, preview: { phase: "loading", runId: action.runId, cloud: null, error: null } };
    case "preview":
      if (action.runId !== (state.result.body?.runId ?? null)) return state; // a model for another run
      // Readiness counts drawable points, not the file's declared count.
      return { ...state, preview: { phase: action.cloud.kept > 0 ? "ready" : "empty", runId: action.runId, cloud: action.cloud, error: null } };
    case "preview-error":
      // A failed reload keeps a model already shown for this run; an unreadable file does not.
      if (!action.unsupported && state.preview.phase === "ready" && state.preview.runId === action.runId) {
        return { ...state, preview: { ...state.preview, error: action.error } };
      }
      return {
        ...state,
        preview: { phase: action.unsupported ? "unsupported" : "error", runId: action.runId, cloud: null, error: action.error },
      };
    default:
      return state;
  }
}

function select(state, action) {
  const next = initialState();
  next.session = state.session + 1;
  next.mode = action.mode;
  next.source = action.source;
  next.captureId = action.captureId ?? null;
  next.connection = { ...next.connection, phase: action.mode === "idle" ? "idle" : "connecting" };
  return next;
}

function applyStatus(state, body) {
  if (!isObject(body) || (body.captureId && body.captureId !== state.captureId)) return state;
  if (typeof body.runId === "string" && state.retiredRuns.has(body.runId)) {
    // The requested refresh completed, even though this replica still names an old run.
    return state.statusRefreshWanted ? { ...state, statusRefreshWanted: false } : state;
  }
  const next = {
    ...state,
    status: typeof body.status === "string" ? body.status : state.status,
    counts: {
      ...state.counts,
      registered: toCount(body.filesRegistered) ?? state.counts.registered,
    },
  };
  // The status reports the capture's latest run, so it alone decides which run is shown.
  const named = { ...next, statusRefreshWanted: false };
  if (typeof body.runId !== "string") return named;
  return setCurrentRun(named, body.runId);
}

/**
 * Which run the page shows. In live mode the status names it. Events and results only record
 * evidence for their own run: one that names a run the status has not named asks for a status
 * refresh, and never switches the view. Before any status has named a run, the first run seen is
 * shown, so a page that only has events still shows stages. A run replaced here is retired, so a
 * delayed status, result or event for it cannot bring it back.
 */
function setCurrentRun(state, runId) {
  if (runId === state.currentRunId) return state;
  const retiredRuns = new Set(state.retiredRuns);
  if (state.currentRunId != null) retiredRuns.add(state.currentRunId);
  let next = { ...state, currentRunId: runId, retiredRuns, verdict: state.verdicts.get(runId) ?? null, retake: state.retakes.get(runId) ?? null };
  // Result and model belong to one run. A result read with no body yet belonged to the old run too.
  if (next.result.body ? next.result.body.runId !== runId : next.result.phase !== "none") next.result = { phase: "none", body: null, error: null, revision: next.result.revision };
  if (next.preview.runId && next.preview.runId !== runId) next.preview = { phase: "none", runId: null, cloud: null, error: null };
  if (state.currentRunId == null) return next;
  return log(next, { seq: null, at: null, kind: "run", text: `Showing run ${runId}` });
}

/** An event or result named `runId`. Returns the state with the run shown unchanged unless none was shown yet. */
function sawRun(state, runId) {
  if (!runId || runId === state.currentRunId || state.retiredRuns.has(runId)) return state;
  if (state.currentRunId == null) return setCurrentRun(state, runId);
  return state.statusRefreshWanted ? state : { ...state, statusRefreshWanted: true };
}

function applyEvents(state, body, at, catchUp) {
  if (!isObject(body)) return state;
  const events = Array.isArray(body.events) ? body.events : [];
  // Events read while catching up on history arrived before the viewer connected.
  const backlog = catchUp ?? !state.firstBatchDone;
  let next = {
    ...state,
    firstBatchDone: true,
    status: typeof body.status === "string" ? body.status : state.status,
  };
  const fresh = events
    .filter((e) => isObject(e) && Number.isInteger(e.seq) && e.seq > 0 && !state.seen.has(e.seq))
    .sort((a, b) => a.seq - b.seq);
  if (fresh.length > 0) {
    next.seen = new Set(state.seen);
    for (const event of fresh) {
      next.seen.add(event.seq);
      next = applyEvent(next, event, backlog);
    }
  }
  const maxSeq = fresh.reduce((m, e) => Math.max(m, e.seq), 0);
  const reported = Number.isInteger(body.next) ? body.next : 0;
  next.cursor = Math.max(state.cursor, reported, maxSeq);
  if (at != null) next.connection = { phase: "live", lastContactAt: at, lastError: null, failures: 0 };
  return next;
}

function applyEvent(state, event, backlog) {
  const data = isObject(event.data) ? event.data : {};
  const base = { seq: event.seq, at: typeof event.at === "string" ? event.at : null };
  if (!KNOWN_EVENTS.has(event.type)) {
    const type = String(event.type).slice(0, 40);
    const unknownTypes = state.unknownTypes.includes(type) ? state.unknownTypes : [...state.unknownTypes, type];
    return log({ ...state, unknownTypes }, { ...base, kind: "unknown", text: `Unrecognized event “${type}”` });
  }
  switch (event.type) {
    case "files_committed":
      return filesCommitted(state, data, base, backlog);
    case "stage":
      return stage(state, data, base);
    case "hint": {
      const hint = { ...base, code: str(data.code), message: str(data.message), retake: str(data.retake) };
      return log({ ...state, hints: [...state.hints, hint].slice(-6) }, { ...base, kind: "hint", text: `Server hint: ${hint.message || hint.code || "no text"}` });
    }
    case "capture_check": {
      const missing = Array.isArray(data.missing) ? data.missing.map(String) : [];
      const check = { ...base, complete: data.complete === true, missing, meterRead: str(data.meterRead) };
      const text = check.complete ? "Coverage check: nothing missing" : `Coverage check: missing ${missing.join(", ") || "unspecified views"}`;
      return log({ ...state, captureCheck: check }, { ...base, kind: "check", text });
    }
    case "retake_request": {
      const views = Array.isArray(data.viewsNeeded) ? data.viewsNeeded.map(String) : [];
      state = sawRun(state, str(data.runId));
      const runId = str(data.runId) ?? state.currentRunId;
      const retake = { ...base, views, runId };
      const named = views.length ? `: ${views.join(", ")}` : "";
      state = { ...state, retakes: new Map(state.retakes).set(runId, retake), retake: runId === state.currentRunId ? retake : state.retake };
      const text = runId === state.currentRunId ? `Server asked for more views${named}` : `Run ${runId} asked for more views${named}`;
      return log(state, { ...base, kind: "retake", text });
    }
    case "verdict_ready": {
      state = sawRun(state, str(data.runId));
      const runId = str(data.runId) ?? state.currentRunId;
      const verdict = { ...base, runId, kind: str(data.kind) };
      state = { ...state, verdicts: new Map(state.verdicts).set(runId, verdict), verdict: runId === state.currentRunId ? verdict : state.verdict };
      const text = runId === state.currentRunId ? `Server reported a result (${verdict.kind || "kind not given"})` : `Run ${runId} reported a result (${verdict.kind || "kind not given"})`;
      return log(state, { ...base, kind: "verdict", text });
    }
    case "failed": {
      const failure = { ...base, code: str(data.code), message: str(data.message), next: str(data.next) };
      const text = `Server reported a failure: ${failure.message || failure.code || "no detail"}`;
      return log({ ...state, failures: [...state.failures, failure].slice(-6) }, { ...base, kind: "failed", text });
    }
    case "review_started":
    case "review_completed":
      return log({ ...state, review: { ...base, type: event.type } }, { ...base, kind: "review", text: event.type === "review_started" ? "A reviewer opened the capture" : "A reviewer finished the capture" });
    case "confirmation":
      return log(state, { ...base, kind: "confirmation", text: `Homeowner answered a ${str(data.fieldId) || "field"} question` });
    default:
      return state;
  }
}

function filesCommitted(state, data, base, backlog) {
  const paths = Array.isArray(data.paths) ? data.paths.filter((p) => typeof p === "string") : [];
  const files = new Map(state.files);
  const groups = { photo: 0, depth: 0, mesh: 0, other: 0 };
  let added = 0;
  for (const path of paths) {
    if (files.has(path)) continue; // the same path acknowledged twice is one file
    const group = fileGroup(path);
    files.set(path, { seq: base.seq, group });
    groups[group] += 1;
    added += 1;
  }
  const listed = toCount(data.listed) ?? state.counts.listed;
  // The backlog total is counted here, not from `arrivals`, which keeps only recent batches.
  const backlogCount = state.counts.backlog + (backlog ? added : 0);
  let next = { ...state, files, counts: { ...state.counts, acknowledged: files.size, listed, backlog: backlogCount } };
  if (added === 0) return next;
  const arrival = { id: state.arrivalSerial + 1, seq: base.seq, count: added, groups, backlog };
  next = { ...next, arrivalSerial: arrival.id, arrivals: [...state.arrivals, arrival].slice(-ARRIVAL_LIMIT) };
  return log(next, { ...base, kind: "files", text: `Server acknowledged ${added} file${added === 1 ? "" : "s"}` });
}

function stage(state, data, base) {
  const name = str(data.stage);
  const runId = str(data.runId) || state.currentRunId || "unknown-run";
  if (!name) return log(state, { ...base, kind: "unknown", text: "Stage event without a stage name" });
  state = sawRun(state, runId);
  const runs = new Map(state.runs);
  const stages = new Map(runs.get(runId) ?? []);
  const prior = stages.get(name);
  if (prior && prior.seq > base.seq) return state;
  stages.set(name, {
    status: str(data.status) || "unknown",
    attempt: Number.isInteger(data.attempt) ? data.attempt : null,
    durationS: typeof data.durationS === "number" ? data.durationS : null,
    errorCode: str(data.errorCode),
    seq: base.seq,
  });
  runs.set(runId, stages);
  const label = STAGES.find(([id]) => id === name)?.[1] ?? name;
  return log(
    { ...state, runs },
    { ...base, kind: "stage", text: `${label}: ${str(data.status) || "unknown"}` },
  );
}

/** Statuses after which no outcome will come, so a result without one is final. */
const NO_OUTCOME_STATUSES = new Set(["failed", "expired"]);

function applyResult(state, body) {
  if (!isObject(body)) return { ...state, result: { ...state.result, phase: "error", body: null, error: "Result was not a JSON object" } };
  // An outcome without run identity cannot be attributed to the capture's current run.
  if (typeof body.runId !== "string" || body.runId === "") return state;
  // A result for a run other than the one shown is not accepted: a retired run's is dropped, and
  // an unknown run's asks for a status refresh first. The result read is retried either way.
  if (state.retiredRuns.has(body.runId)) return state;
  state = sawRun(state, body.runId);
  if (body.runId !== state.currentRunId) return state;
  const ready = body.outcome != null || NO_OUTCOME_STATUSES.has(body.status);
  // A refreshed result that no longer offers a preview takes the earlier model with it.
  const preview = !hasPreview(body) && state.preview.runId === body.runId ? { phase: "none", runId: null, cloud: null, error: null } : state.preview;
  // Each accepted result is a new revision; the preview is read again for a revision that offers one.
  return { ...state, preview, result: { phase: ready ? "ready" : "pending", body, error: null, revision: state.result.revision + 1 } };
}

function log(state, entry) {
  return { ...state, log: [...state.log, entry].slice(-LOG_LIMIT) };
}

// ---- Selectors: derived views the renderer reads. ----

/** Connection health as the viewer shows it; `now` comes from the caller so this stays pure. */
export function connectionView(state, now, staleAfterMs = 40_000) {
  const { phase, lastContactAt, lastError } = state.connection;
  const age = lastContactAt == null ? null : Math.max(0, now - lastContactAt);
  if (phase === "live" && age != null && age > staleAfterMs) return { phase: "stale", age, lastError };
  return { phase, age, lastError };
}

/** Capture statuses after which no stage is still working. */
const SETTLED_STATUSES = new Set(["complete", "manual_review", "needs_views", "failed", "expired"]);

/**
 * Stage rows for the current run, in pipeline order, plus any stage names this viewer does not
 * know. Once the capture has settled, a stage whose last event was "running" shows as
 * "no end reported": the server never said it finished, and it is no longer working on it.
 */
export function stageRows(state) {
  const stages = state.runs.get(state.currentRunId) ?? new Map();
  const settled = SETTLED_STATUSES.has(state.status);
  const row = (id, label, value) => {
    const r = { id, label, ...(value ?? { status: "unreported" }) };
    return settled && r.status === "running" ? { ...r, status: "unended" } : r;
  };
  const rows = STAGES.map(([id, label]) => row(id, label, stages.get(id)));
  for (const [id, value] of stages) {
    if (!STAGES.some(([known]) => known === id)) rows.push(row(id, id, value));
  }
  return rows;
}

/**
 * True when no finished stage reported a usable duration: each one is missing or under 0.05 s.
 * This is about the telemetry only. A real run can report durations of 0 (one live run did, after
 * 11 minutes of processing), so the page says the timing cannot be read and never infers from it
 * whether real processing ran.
 */
export function stageTimingUninterpretable(state) {
  const done = stageRows(state).filter((r) => r.status === "done");
  return done.length >= 3 && done.every((r) => r.durationS == null || r.durationS < 0.05);
}

/**
 * The views the server asked for, for the current run: from the result when it lists any,
 * otherwise from a retake request. Each has the view id and the server's prompt title, if given.
 */
export function viewsToShow(state) {
  const body = state.result.body;
  const listed = [...(Array.isArray(body?.viewsNeeded) ? body.viewsNeeded : []), ...(Array.isArray(body?.outcome?.viewsNeeded) ? body.outcome.viewsNeeded : [])];
  const seen = new Set();
  const fromResult = listed
    .filter((v) => isObject(v) && typeof v.id === "string" && !seen.has(v.id) && seen.add(v.id))
    .map((v) => ({ id: v.id, title: typeof v.prompt?.title === "string" ? v.prompt.title : null }));
  // A ready result's own list, even an empty one, is the server's answer for this run.
  const hasList = Array.isArray(body?.viewsNeeded) || Array.isArray(body?.outcome?.viewsNeeded);
  if (fromResult.length > 0 || (hasList && state.result.phase === "ready" && body.runId === state.currentRunId)) return fromResult;
  return state.retake ? state.retake.views.map((id) => ({ id, title: null })) : [];
}

/** A non-empty preview URL; an empty string means the result has no preview. */
export function hasPreview(body) {
  return typeof body?.previewUrl === "string" && body.previewUrl !== "";
}

/** Whether the events say a result exists, or that none will come, so the viewer should read it. */
export function resultExpected(state) {
  return state.verdict != null || RESULT_STATUSES.has(state.status);
}

/** Identifies what the result was read for; a new verdict or status means read it again. */
export function resultKey(state) {
  return `${state.currentRunId ?? "-"}|${state.verdict?.seq ?? "-"}|${state.status}`;
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function str(value) {
  return typeof value === "string" ? value : null;
}

function toCount(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}
