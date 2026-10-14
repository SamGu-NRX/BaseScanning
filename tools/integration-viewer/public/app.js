// Wires the page: mode switch, replay and live controls, and a render pass that reads the reducer
// state. Rendering is idempotent; animation happens only for arrivals the view has not shown yet.

import { CloudView } from "./cloud.js";
import { Conduit } from "./conduit.js";
import { createCountTimers } from "./count-timers.js";
import { followCapture } from "./live.js";
import { connectionView, hasPreview, initialState, reduce, stageRows, stageTimingUninterpretable, viewsToShow } from "./model.js";
import { REPLAY_CAPTURE_ID, ReplayPlayer } from "./replay.js";
import { SCENARIOS } from "./scenario.js";

const $ = (id) => document.getElementById(id);
const CAPTURE_ID = /^cap_[A-Za-z0-9_]{4,64}$/;

const STATUS_WORDS = {
  uploading: ["accepting files", ""],
  awaiting_files: ["waiting for the last files", ""],
  processing: ["working", ""],
  needs_views: ["needs more views", "bad"],
  complete: ["finished", "good"],
  manual_review: ["sent to manual review", ""],
  failed: ["failed", "bad"],
  expired: ["expired", "bad"],
};
const PHONE_WORDS = {
  uploading: "The server is accepting this capture's files.",
  awaiting_files: "The packet arrived. The server is waiting for its remaining files.",
  processing: "Everything the server needs has arrived.",
  needs_views: "The server asked for more views.",
  complete: "Upload finished and processed.",
  manual_review: "Upload finished; a person will review it.",
  failed: "The server stopped this capture.",
  expired: "The capture expired before it was finished.",
};
const OUTCOME_WORDS = {
  eligible: "A battery fits",
  needs_more_photos: "Needs more photos",
  not_eligible: "Doesn't fit",
  manual_review: "Manual review",
};
const NEXT_WORDS = {
  retry_finalize: "The server says the packet can be sent again.",
  manual_review: "The server passed it to manual review.",
  none: "The server will not retry this capture.",
};

let state = initialState();
let sources = [];
let player = null;
let follower = null;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
let motionPaused = reducedMotion.matches;
let shownArrival = 0;
const countTimers = createCountTimers({ write: writeCounts });
let shownCounts = { session: -1, value: 0 };
let clockStalled = false;
let renderQueued = false;

const cloud = new CloudView($("cloud"));
const conduit = new Conduit($("conduit"), [$("line-base"), $("line-ticks")], $("chips"));

function dispatch(action) {
  const next = reduce(state, action);
  if (next === state) return;
  state = next;
  if (!renderQueued) {
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      render();
    });
  }
}

// ---- modes ----

function stopAll() {
  countTimers.clear();
  player?.stop();
  player = null;
  follower?.stop();
  follower = null;
  conduit.clear();
  shownArrival = 0;
}

function startReplay(scenario) {
  stopAll();
  document.body.dataset.mode = "replay";
  document.body.dataset.sourceKind = "replay";
  dispatch({ type: "select", mode: "replay", source: { key: "replay", label: "Illustrative replay (in this page)", kind: "replay" }, captureId: REPLAY_CAPTURE_ID });
  player = new ReplayPlayer({ scenario, session: state.session, dispatch, onEnd: () => render() });
  player.play();
  setUrl({ mode: "replay", scenario });
  render();
}

function showLive() {
  stopAll();
  document.body.dataset.mode = "live";
  document.body.dataset.sourceKind = sourceFor($("source").value)?.kind ?? "backend";
  dispatch({ type: "select", mode: "idle", source: sourceFor($("source").value) ?? null });
  setUrl({ mode: "live", source: $("source").value });
  render();
}

function connect(key, captureId) {
  const source = sourceFor(key);
  if (!source || !CAPTURE_ID.test(captureId)) return;
  stopAll();
  document.body.dataset.mode = "live";
  document.body.dataset.sourceKind = source.kind;
  dispatch({ type: "select", mode: "live", source, captureId });
  follower = followCapture({ sourceKey: key, captureId, session: state.session, dispatch, getState: () => state });
  setUrl({ mode: "live", source: key, capture: captureId });
  render();
}

function sourceFor(key) {
  return sources.find((s) => s.key === key);
}

function setUrl(params) {
  const url = new URL(location.href);
  url.search = new URLSearchParams(params).toString();
  history.replaceState(null, "", url);
}

// ---- rendering ----

function render() {
  const live = state.mode === "live" || document.body.dataset.mode === "live";
  for (const b of document.querySelectorAll("[data-set-mode]")) b.setAttribute("aria-pressed", String(b.dataset.setMode === (live ? "live" : "replay")));
  document.body.dataset.motion = motionOn() ? "on" : "off";
  document.body.dataset.clock = clockStalled ? "stalled" : "running";
  $("motion").setAttribute("aria-pressed", String(motionPaused));
  $("motion").textContent = motionPaused ? "Resume motion" : "Pause motion";
  $("replay-toggle").textContent = player?.playing ? "Pause" : player?.ended ? "Play again" : "Play";
  $("live-connect").textContent = follower ? "Switch" : "Connect";
  $("live-stop").hidden = !follower;

  renderBand(live);
  renderIdentity();
  renderPhone();
  renderArrivals();
  renderServer();
  renderResult();
  renderLog();
}

function renderBand(live) {
  const s = state.source;
  let text = "Illustrative replay · synthetic events, not a real capture";
  if (live) {
    if (!s) text = "Live · no source available";
    else if (s.kind === "synthetic") text = "Live from the synthetic local API · self-authored events, not the backend";
    else if (s.kind === "local") text = `Live from a local API at ${host(s.origin)} · not the deployed backend`;
    else if (!state.captureId) text = `Live · read-only · enter a capture id to follow it on ${host(s.origin)}`;
    else text = `Live · read-only view of ${state.captureId} on ${host(s.origin)}`;
  }
  $("band-text").textContent = text;
}

function renderIdentity() {
  const s = state.source;
  $("id-source").textContent = s ? (s.origin ? `${s.label} · ${host(s.origin)}` : s.label) : "—";
  const id = state.identity;
  $("id-build").textContent = id ? [id.version, id.storage && `storage ${id.storage}`, id.state && `state ${id.state}`].filter(Boolean).join(" · ") : "—";
  $("id-capture").textContent = state.captureId ?? "—";
  renderConnection();
}

function renderConnection() {
  const el = $("conn");
  if (state.mode === "replay") {
    const outage = state.connection.phase === "reconnecting";
    el.dataset.phase = outage ? "reconnecting" : "replay";
    $("conn-text").textContent = outage ? "Replay · illustrating a dropped connection" : player?.playing ? "Replay running" : player?.ended ? "Replay finished" : "Replay paused";
    return;
  }
  const view = connectionView(state, Date.now());
  view.lastError = plainError(view.lastError);
  el.dataset.phase = view.phase;
  const age = view.age == null ? null : view.age < 2000 ? "just now" : `${Math.round(view.age / 1000)} s ago`;
  const words = {
    idle: "Not connected",
    connecting: "Connecting…",
    live: `Live · last reply ${age}`,
    stale: `Stale · last reply ${age}`,
    reconnecting: `Reconnecting · ${view.lastError ?? "no reply"}${age ? ` · last reply ${age}` : ""}`,
    offline: `Offline · this computer has no network${age ? ` · last reply ${age}` : ""}`,
    gone: view.lastError ?? "Capture unavailable",
    stopped: "Disconnected · showing the last evidence",
  };
  $("conn-text").textContent = words[view.phase] ?? view.phase;
}

/** Relay error codes in words; anything unrecognized is shown as it came. */
function plainError(detail) {
  if (!detail) return detail;
  const words = {
    upstream_unreachable: "the API did not answer",
    upstream_timeout: "the API timed out",
    upstream_not_json: "the API sent an unexpected reply",
    "network error": "the viewer's local server did not answer",
  };
  const code = detail.split(" ")[0];
  return words[code] ?? words[detail] ?? detail;
}

function renderPhone() {
  $("phone-status").textContent = state.status ? (PHONE_WORDS[state.status] ?? `Server status: ${state.status}`) : state.captureId ? "Waiting for the first reply" : "Waiting for a capture";
  const hints = $("hints");
  const shown = state.hints.slice(-3);
  // Keys carry the session: a new capture restarts seq numbers, and its hints must not reuse the old list.
  const hintKey = `${state.session}:${shown.map((h) => h.seq).join(",")}`;
  if (hints.dataset.key !== hintKey) {
    hints.dataset.key = hintKey;
    hints.replaceChildren(...shown.map((h) => el("li", {}, h.message || h.code || "Hint without text")));
  }
}

function renderArrivals() {
  const { counts } = state;
  const fresh = state.arrivals.filter((a) => a.id > shownArrival);
  let landsIn = 0;
  if (fresh.length > 0) {
    shownArrival = fresh.at(-1).id;
    landsIn = Math.max(...fresh.map((a) => conduit.send(a, { motion: motionOn() })));
  }
  // The counts change as the marks land.
  countTimers.rendered(state, landsIn);
  const backlog = counts.backlog;
  const parts = [];
  if (counts.registered != null) parts.push(`of ${counts.registered} registered`);
  if (counts.listed != null) parts.push(`packet lists ${counts.listed}`);
  if (backlog > 0) parts.push(`${backlog} before you connected`);
  $("ack-sub").textContent = parts.length ? parts.join(" · ") : counts.acknowledged ? "" : "No files acknowledged yet";
}

/** Shows the acknowledged-file totals from `from`; never goes back to a smaller total in a session. */
function writeCounts(from) {
  if (from.session !== state.session) return;
  if (shownCounts.session === from.session && from.counts.acknowledged < shownCounts.value) return;
  shownCounts = { session: from.session, value: from.counts.acknowledged };
  const groups = { photo: 0, depth: 0, other: 0 };
  for (const f of from.files.values()) groups[f.group === "mesh" ? "other" : f.group] += 1;
  $("k-photo").textContent = groups.photo;
  $("k-depth").textContent = groups.depth;
  $("k-other").textContent = groups.other;
  const ack = $("ack");
  const value = String(from.counts.acknowledged);
  if (ack.textContent === value) return;
  ack.textContent = value;
  if (motionOn()) {
    ack.removeAttribute("data-bump");
    void ack.offsetWidth;
    ack.setAttribute("data-bump", "");
  }
}

function renderServer() {
  const [word, tone] = STATUS_WORDS[state.status] ?? [state.status ?? "idle", ""];
  $("server-state").textContent = word;
  $("server-state").dataset.tone = tone;
  const list = $("stages");
  const rows = stageRows(state);
  const ids = rows.map((r) => r.id).join(",");
  if (list.dataset.key !== ids) {
    list.dataset.key = ids;
    list.replaceChildren(...rows.map((r) => el("li", { class: "st", "data-id": r.id }, el("span", { class: "st-mark", "aria-hidden": "true" }), el("span", { class: "st-name" }, r.label, el("span", { class: "st-id" }, r.id)), el("span", { class: "st-state" }))));
  }
  rows.forEach((r, i) => {
    const li = list.children[i];
    li.dataset.status = r.status;
    li.querySelector(".st-state").textContent = stageText(r);
  });
  $("timing-caution").hidden = !stageTimingUninterpretable(state);
  const unknown = $("unknown-caution");
  unknown.hidden = state.unknownTypes.length === 0;
  unknown.textContent = `The server sent event types this viewer does not know (${state.unknownTypes.join(", ")}). They appear in the log and change nothing else.`;
  const failures = $("failures");
  const key = `${state.session}:${state.failures.map((f) => f.seq).join(",")}`;
  if (failures.dataset.key !== key) {
    failures.dataset.key = key;
    failures.replaceChildren(
      ...state.failures.map((f) => el("li", {}, el("span", { class: "code" }, f.code ?? "failed"), f.message || "No message", f.next && NEXT_WORDS[f.next] ? ` ${NEXT_WORDS[f.next]}` : "")),
    );
  }
}

function stageText(r) {
  if (r.status === "unreported") return "—";
  if (r.status === "done") return r.durationS != null ? formatSeconds(r.durationS) : "done";
  if (r.status === "running") return r.attempt > 1 ? `running · try ${r.attempt}` : "running";
  if (r.status === "failed") return r.errorCode ? `failed · ${r.errorCode}` : "failed";
  if (r.status === "unended") return "no end reported";
  return r.status;
}

function formatSeconds(s) {
  if (s < 0.1) return `${s.toFixed(2)} s`;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

function renderResult() {
  // Rendered before any early return: a retake can name views before a result exists.
  renderViews();
  const replay = state.mode === "replay";
  const { result, preview } = state;
  const figure = $("model");
  let phase = preview.phase;
  let caption = replay ? "The synthetic model appears when the replay reaches its result." : "No model yet. The server's model appears here if it returns one.";
  if (preview.phase === "loading") caption = replay ? "Loading the synthetic model…" : "Loading the server's final model…";
  else if (preview.phase === "ready") {
    const c = preview.cloud;
    const shown = c.kept < c.count ? ` (drawing ${c.kept.toLocaleString()})` : "";
    const from = replay ? "Synthetic model for this illustration" : state.source?.kind === "backend" ? "Final model from the server" : state.source?.kind === "synthetic" ? "Final model from the synthetic API (self-authored)" : "Final model from the local API";
    caption = `${from} · ${c.count.toLocaleString()} points${shown}`;
    if (preview.error) caption += ` · Reloading it failed (${preview.error}); this is the last model received.`;
  } else if (preview.phase === "empty") caption = "The server returned a model with no points.";
  else if (preview.phase === "unsupported") caption = `Preview unavailable: the file is not a point cloud this viewer reads (${preview.error}).`;
  else if (preview.phase === "error") caption = `Could not load the model: ${preview.error}.`;
  else if (result.phase === "ready" && !hasPreview(result.body)) {
    phase = "none";
    caption = state.status === "failed" ? "No model: the run failed before one was built." : "The result came back without a model preview.";
  } else if (state.verdict || result.phase === "loading") caption = "The server reported a result. Reading it…";
  figure.dataset.phase = phase;
  $("model-caption").textContent = caption;
  cloud.setMotion(motionOn());
  cloud.setCloud(preview.phase === "ready" ? preview.cloud : null, { reveal: true });
  $("legend").hidden = !(preview.phase === "ready" && preview.cloud.generated);

  const box = $("outcome");
  const body = result.body;
  if (result.phase === "error" && !body) {
    box.hidden = false;
    setOutcome("", "Could not read the result", `${result.error}. The viewer will try again while connected.`);
    $("criteria").replaceChildren();
    $("review-link").hidden = true;
    return;
  }
  if (!body || (result.phase !== "ready" && result.phase !== "loading")) {
    box.hidden = true;
    $("criteria").replaceChildren();
    $("criteria").dataset.key = "";
    return;
  }
  box.hidden = false;
  $("outcome-note").hidden = !result.error;
  $("outcome-note").textContent = result.error ? `Refreshing the result failed (${plainError(result.error)}). This is the last result received.` : "";
  const outcome = body.outcome;
  if (outcome && typeof outcome.kind === "string") {
    setOutcome(outcome.kind, OUTCOME_WORDS[outcome.kind] ?? outcome.kind, typeof outcome.message === "string" ? outcome.message : "");
  } else {
    const status = body.status ?? state.status;
    if (status === "failed") setOutcome("not_eligible", "Stopped before a result", "The server reported a failure. The server column shows what it said.");
    else setOutcome("", "No outcome", `The result reports status “${status}” and no outcome.`);
  }
  const criteria = Array.isArray(body.criteria) ? body.criteria.filter((c) => c && typeof c.id === "string") : [];
  const ckey = `${state.session}:${JSON.stringify(criteria)}`;
  if ($("criteria").dataset.key !== ckey) {
    $("criteria").dataset.key = ckey;
    $("criteria").replaceChildren(
      ...criteria.map((c) =>
        el("li", {}, el("span", { class: "crit-outcome", "data-o": String(c.outcome) }, String(c.outcome ?? "?")), el("span", { class: "crit-id", title: c.id }, c.id), el("span", { class: "crit-num" }, measure(c))),
      ),
    );
  }
  const link = $("review-link");
  const reviewPath = typeof body.reviewUrl === "string" && /^\/v1\/captures\/cap_[A-Za-z0-9_]{4,64}\/review$/.test(body.reviewUrl) ? body.reviewUrl : null;
  link.hidden = !(reviewPath && state.source?.kind === "backend");
  if (!link.hidden) link.href = new URL(reviewPath, state.source.origin).href;
}

/** The views the server asked for: its own prompt title, verbatim, with the view id. */
function renderViews() {
  const list = viewsToShow(state);
  $("views").hidden = list.length === 0;
  $("views-list").replaceChildren(...list.map((v) => el("li", {}, v.title ?? "View", el("span", { class: "view-id" }, v.id))));
}

function setOutcome(kind, title, message) {
  $("outcome-kind").dataset.kind = kind;
  $("outcome-kind").textContent = title;
  $("outcome-msg").textContent = message;
}

function measure(c) {
  if (typeof c.measuredFt !== "number") return c.coverage ? `coverage ${c.coverage}` : "";
  const threshold = typeof c.thresholdFt === "number" ? ` / needs ${c.thresholdFt} ft` : "";
  return `${c.measuredFt} ft${threshold}`;
}

function renderLog() {
  const list = $("log");
  const entries = state.log.slice(-5);
  const key = `${state.session}:${state.log.length}:${entries.map((e) => e.seq ?? e.text).join(",")}`;
  if (list.dataset.key === key) return;
  list.dataset.key = key;
  if (entries.length === 0) {
    list.replaceChildren(el("li", {}, el("span", { class: "t" }, ""), el("span", { class: "s" }, ""), state.mode === "live" && !state.captureId ? "Enter a capture id and connect. The viewer only reads; it never changes a capture." : "Nothing reported yet."));
    return;
  }
  list.replaceChildren(...entries.map((e) => el("li", { "data-kind": e.kind }, el("span", { class: "t" }, clock(e.at)), el("span", { class: "s" }, e.seq == null ? "" : `#${e.seq}`), e.text)));
}

function clock(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleTimeString([], { hour12: false }) : "";
}

function host(origin) {
  try {
    return new URL(origin).host;
  } catch {
    return origin ?? "";
  }
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.append(...children.filter((c) => c !== "" && c != null));
  return node;
}

function motionOn() {
  return !motionPaused && !document.hidden && !clockStalled;
}

/**
 * Some embedded browser panels keep running scripts and timers but stop the animation clock.
 * There, transitions never leave their start values and the page would show stale styling. A
 * short probe animation detects that; while the clock is stopped the page drops motion and CSS
 * transitions so every state change shows at once.
 */
const probe = document.createElement("span");
probe.setAttribute("aria-hidden", "true");
probe.className = "clock-probe";
document.body.append(probe);
function probeClock() {
  if (document.hidden) return;
  const anim = probe.animate([{ opacity: 0 }, { opacity: 0 }], { duration: 300 });
  setTimeout(() => {
    const stalled = anim.playState !== "finished" && (Number(anim.currentTime) || 0) < 150;
    anim.cancel();
    if (stalled === clockStalled) return;
    clockStalled = stalled;
    if (stalled) conduit.clear();
    render();
  }, 900);
}
setInterval(probeClock, 3000);
probeClock();

// ---- controls ----

for (const [key, s] of Object.entries(SCENARIOS)) $("scenario").append(new Option(s.label, key));

for (const b of document.querySelectorAll("[data-set-mode]")) {
  b.addEventListener("click", () => {
    if (b.dataset.setMode === "replay") startReplay($("scenario").value);
    else showLive();
  });
}
$("scenario").addEventListener("change", () => startReplay($("scenario").value));
$("replay-toggle").addEventListener("click", () => {
  if (!player || player.ended) return startReplay($("scenario").value);
  if (player.playing) player.pause();
  else player.play();
  render();
});
$("replay-restart").addEventListener("click", () => startReplay($("scenario").value));
$("live-form").addEventListener("submit", (e) => {
  e.preventDefault();
  connect($("source").value, $("capture").value.trim());
});
$("live-stop").addEventListener("click", () => {
  follower?.stop();
  follower = null;
  render();
});
$("source").addEventListener("change", () => {
  document.body.dataset.sourceKind = sourceFor($("source").value)?.kind ?? "backend";
  if (!follower) showLive();
});
function setMotionPaused(paused) {
  motionPaused = paused;
  // Pausing stops movement already under way, not only movement still to come.
  if (paused) conduit.clear();
  render();
}
$("motion").addEventListener("click", () => setMotionPaused(!motionPaused));
reducedMotion.addEventListener("change", () => setMotionPaused(reducedMotion.matches));
document.addEventListener("visibilitychange", () => render());
setInterval(renderConnection, 1000);

// ---- start ----

async function start() {
  try {
    const reply = await fetch("/api/config");
    sources = (await reply.json()).upstreams ?? [];
  } catch {
    sources = [];
  }
  for (const s of sources) $("source").append(new Option(`${s.label} (${host(s.origin)})`, s.key));
  if (sources.length === 0) {
    $("source").append(new Option("No live source: start server.js with --api or --synthetic", ""));
    $("live-connect").disabled = true;
  }
  const params = new URLSearchParams(location.search);
  if (params.get("mode") === "live") {
    if (sources.some((s) => s.key === params.get("source"))) $("source").value = params.get("source");
    const capture = params.get("capture") ?? "";
    $("capture").value = capture;
    if (CAPTURE_ID.test(capture) && sourceFor($("source").value)) connect($("source").value, capture);
    else showLive();
  } else {
    const scenario = params.get("scenario");
    if (scenario && scenario in SCENARIOS) $("scenario").value = scenario;
    startReplay($("scenario").value);
  }
}

start();
