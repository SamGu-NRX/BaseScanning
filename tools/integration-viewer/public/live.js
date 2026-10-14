// Follows one capture through the loopback relay: health until it answers, then an events long-poll loop,
// with status refreshes and the result and preview fetched when the events say they exist.
// Every dispatch carries the session it started under; the reducer ignores it after a switch,
// and stop() aborts whatever request is in flight.

import { hasPreview, resultExpected, resultKey } from "./model.js";
import { PlyError, parsePly } from "./ply.js";

const WAIT_S = 20;
// While a result is expected but not yet readable, poll quickly so it appears soon after it is written.
const WAIT_EXPECTING_S = 2;
const STATUS_EVERY_MS = 5_000;
const RESULT_RETRY_MS = 4_000;
// After this many quick reads, keep reading the result, but only every RESULT_SLOW_MS.
const RESULT_QUICK_TRIES = 8;
const RESULT_SLOW_MS = 30_000;
// Short on purpose: a viewer being filmed should pick up within a few seconds of the API
// returning. An API that wants callers to wait longer says so with Retry-After.
const BACKOFF_MS = [1_000, 2_000, 3_000, 5_000];
// A long-poll that comes back empty sooner than this waits out the rest, so an API that ignores
// `wait` cannot turn the loop into a request storm.
const MIN_EMPTY_POLL_MS = 1_000;
const RETRY_AFTER_CAP_MS = 120_000;
const PREVIEW_RETRY_MS = 5_000;
// The API returns at most this many events per read; a full page means more history is waiting.
const PAGE_SIZE = 100;

export class HttpError extends Error {
  constructor(status, code, message, retryAfterS = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfterS = retryAfterS;
  }
}

/**
 * @param {{ sourceKey: string, captureId: string, session: number, dispatch: (a: object) => void,
 *           getState: () => any, fetchImpl?: typeof fetch, now?: () => number, sleep?: (ms: number, signal: AbortSignal) => Promise<void>,
 *           online?: () => boolean }} options
 */
export function followCapture({ sourceKey, captureId, session, dispatch, getState, fetchImpl = fetch, now = Date.now, sleep = abortableSleep, online = () => navigator.onLine }) {
  const abort = new AbortController();
  const { signal } = abort;
  const base = `/relay/${sourceKey}`;
  const artifactKeyFor = (body) => JSON.stringify([body?.runId ?? null, body?.previewArtifactId ?? body?.previewUrl]);
  const send = (action) => {
    if (!signal.aborted) dispatch({ ...action, session });
  };

  async function get(path, as = "json") {
    const reply = await fetchImpl(base + path, { signal, headers: { accept: as === "json" ? "application/json" : "*/*" } });
    if (!reply.ok) {
      const body = await reply.json().catch(() => null);
      const error = body?.errors?.[0];
      const retryHeader = reply.headers.get("retry-after") ?? "";
      let retryAfter = Number(retryHeader);
      if (!/^\d+$/.test(retryHeader)) retryAfter = /[A-Za-z]/.test(retryHeader) ? (Date.parse(retryHeader) - now()) / 1000 : NaN;
      throw new HttpError(reply.status, error?.code ?? `http_${reply.status}`, error?.message ?? `HTTP ${reply.status}`, Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null);
    }
    return as === "json" ? reply.json() : reply.arrayBuffer();
  }

  async function run() {
    let failures = 0;
    let statusAt = -Infinity;
    const resultPoll = { key: null, readyKey: null, tries: 0, at: -Infinity };
    // Server deadlines are independent of polling cadence and survive result refreshes.
    // Result reads obey the later of cadence and result delay. Preview reads obey both
    // endpoint delays because the relay reads /result before fetching the artifact.
    let resultNotBefore = -Infinity;
    const previewNotBefore = new Map();
    let previewKey = null;
    let invalidatedArtifactKey = null;
    // Reads start without waiting and repeat while pages come back full. They return what happened
    // before the viewer connected, which the reducer marks as backlog so it does not animate.
    let catchUp = true;
    let waitS = 0;

    while (!signal.aborted) {
      let contactRetryMs = 0;
      try {
        // An event or result named a run the status has not named yet: ask the status now.
        if (getState().statusRefreshWanted) statusAt = -Infinity;
        if (now() - statusAt >= STATUS_EVERY_MS) {
          // Health is read until it answers once, so the build shown survives a failed first read.
          if (!getState().identity) get("/healthz").then((body) => send({ type: "health", body }), () => {});
          send({ type: "status", body: await get(`/captures/${captureId}`) });
          statusAt = now();
          send({ type: "contact-ok", at: now() });
        }
        const cursor = getState().cursor;
        const asked = now();
        // The wait ends by the next status refresh, so a status change with no event still shows.
        const untilStatusS = Math.ceil(Math.max(0, STATUS_EVERY_MS - (now() - statusAt)) / 1000);
        const body = await get(`/captures/${captureId}/events?after=${cursor}&wait=${catchUp ? 0 : Math.min(waitS, untilStatusS)}`);
        const count = Array.isArray(body?.events) ? body.events.length : 0;
        send({ type: "events", body, at: now(), catchUp });
        failures = 0;
        if (catchUp && count >= PAGE_SIZE) continue;
        catchUp = false;
        if (count === 0 && now() - asked < MIN_EMPTY_POLL_MS) await sleep(MIN_EMPTY_POLL_MS - (now() - asked), signal).catch(() => {});
      } catch (error) {
        if (signal.aborted) return;
        if (error instanceof HttpError && (error.status === 404 || error.status === 410)) {
          send({ type: "gone", error: error.status === 410 ? "The capture was deleted (410)." : "The API has no capture with this id (404)." });
          return;
        }
        const detail = error instanceof HttpError ? `${error.code} (${error.status})` : "network error";
        send({ type: "contact-failed", error: detail, offline: !online() });
        const backoff = BACKOFF_MS[Math.min(failures, BACKOFF_MS.length - 1)];
        const asked = error instanceof HttpError && error.retryAfterS ? Math.min(error.retryAfterS * 1000, RETRY_AFTER_CAP_MS) : 0;
        contactRetryMs = Math.max(backoff, asked);
        failures += 1;
      }

      const state = getState();
      if (state.session !== session) return;
      const key = resultKey(state);
      if (key !== resultPoll.key) {
        resultPoll.key = key;
        resultPoll.tries = 0;
      }
      const pending = resultExpected(state) && resultPoll.readyKey !== key;
      const interval = resultPoll.tries < RESULT_QUICK_TRIES ? RESULT_RETRY_MS : RESULT_SLOW_MS;
      if (pending && now() >= Math.max(resultPoll.at + interval, resultNotBefore)) {
        resultPoll.tries += 1;
        resultPoll.at = now();
        send({ type: "result-loading" });
        try {
          const body = await get(`/captures/${captureId}/result`);
          send({ type: "result", body });
          // Only a response the reducer accepted settles this key; a discarded one is read again.
          if (getState().result.body === body && getState().result.phase === "ready") {
            // Keep refreshing an invalidated artifact at the normal result cadence.
            if (!hasPreview(body) || artifactKeyFor(body) !== invalidatedArtifactKey) {
              invalidatedArtifactKey = null;
              resultPoll.readyKey = key;
            }
          }
        } catch (error) {
          if (signal.aborted) return;
          if (error instanceof HttpError && error.retryAfterS) resultNotBefore = now() + Math.min(error.retryAfterS * 1000, RETRY_AFTER_CAP_MS);
          send({ type: "result-error", error: error instanceof HttpError ? `${error.code} (${error.status})` : "network error" });
        }
      }
      waitS = resultExpected(getState()) && resultPoll.readyKey !== key && resultPoll.tries < RESULT_QUICK_TRIES ? WAIT_EXPECTING_S : WAIT_S;

      const result = getState().result;
      const runId = result.body?.runId ?? null;
      const revisionKey = `${runId}|${result.revision}`;
      const artifactKey = artifactKeyFor(result.body);
      for (const [artifact, deadline] of previewNotBefore) if (deadline <= now()) previewNotBefore.delete(artifact);
      const previewDue = Math.max(resultNotBefore, previewNotBefore.get(artifactKey) ?? -Infinity);
      if (result.phase === "ready" && hasPreview(result.body) && typeof runId === "string" && previewKey !== revisionKey && artifactKey !== invalidatedArtifactKey && now() >= previewDue) {
        previewKey = revisionKey;
        send({ type: "preview-loading", runId });
        try {
          // The relay refuses with 409 if the latest result is no longer this run's.
          const cloud = parsePly(await get(`/captures/${captureId}/preview?run=${encodeURIComponent(runId)}`, "bytes"));
          send({ type: "preview", runId, cloud });
        } catch (error) {
          if (signal.aborted) return;
          if (error instanceof HttpError && (error.status === 409 || error.code === "preview_not_available")) {
            invalidatedArtifactKey = artifactKey; // a new read revision does not mean the artifact changed
            resultPoll.readyKey = null;
            // This result is stale; do not carry over its slow polling schedule.
            resultPoll.tries = 0;
            resultPoll.at = -Infinity;
            waitS = 0;
          } else if (error instanceof PlyError) send({ type: "preview-error", runId, unsupported: true, error: error.message });
          else {
            // A fetch failure may pass; an unreadable file will not. Only the first is retried.
            send({ type: "preview-error", runId, error: error instanceof HttpError ? error.message : "network error" });
            previewKey = null;
            const asked = error instanceof HttpError && error.retryAfterS ? Math.min(error.retryAfterS * 1000, RETRY_AFTER_CAP_MS) : 0;
            // The relay identifies failures of its prerequisite result read separately.
            if (error instanceof HttpError && error.code === "result_unavailable") resultNotBefore = Math.max(resultNotBefore, now() + asked);
            previewNotBefore.set(artifactKey, now() + Math.max(PREVIEW_RETRY_MS, asked));
          }
        }
      }
      if (contactRetryMs > 0) await sleep(contactRetryMs, signal).catch(() => {});
    }
  }

  const done = run();
  return {
    done,
    stop() {
      abort.abort();
      dispatch({ type: "stopped", session });
    },
  };
}

function abortableSleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
