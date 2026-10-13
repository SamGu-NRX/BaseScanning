// A loopback stand-in for the capture API's read-only routes, playing the self-authored scenarios
// in public/scenario.js. It exists so the viewer's live path (relay, long-poll, reconnect, result,
// preview) can be run end to end without a phone or the deployed backend. Health reports
// storage "synthetic" so the viewer labels it; it proves nothing about the real server.
//
// Capture ids pick the scenario and start its clock on first request:
//   cap_SYNTH_COMPLETE_<anything>   full run to an eligible result with a preview cloud
//   cap_SYNTH_FAILED_<anything>     rejected after upload
//   cap_SYNTH_DROPOUT_<anything>    full run; the API drops connections between 11 s and 19 s

import { createServer } from "node:http";
import { DROPOUT, RUN_ID, encodePly, resultBody, syntheticCloud, timeline } from "./public/scenario.js";

const ID = /^cap_SYNTH_(COMPLETE|FAILED|DROPOUT)_[A-Za-z0-9_]{1,40}$/;

/** @param {{ speed?: number, clock?: () => number }} [options] speed > 1 plays scenarios faster; tests use it. */
export function createSyntheticApi({ speed = 1, clock = Date.now } = {}) {
  const t0 = clock();
  const now = () => t0 + (clock() - t0) * speed;
  const captures = new Map();
  let ply = null;
  let origin = "";

  function capture(id) {
    const m = ID.exec(id);
    if (!m) return null;
    let entry = captures.get(id);
    if (!entry) {
      const scenario = m[1].toLowerCase();
      const steps = timeline(scenario === "dropout" ? "complete" : scenario);
      let seq = 0;
      for (const step of steps) if (step.event) step.seq = ++seq;
      entry = { scenario, steps, startedAt: now() };
      captures.set(id, entry);
    }
    return entry;
  }

  function stateAt(entry) {
    const elapsed = now() - entry.startedAt;
    let status = "uploading";
    let registered = 0;
    const committed = new Set();
    const events = [];
    for (const step of entry.steps) {
      if (step.t > elapsed) break;
      if (step.status) status = step.status;
      if (step.registered != null) registered = step.registered;
      if (step.event) {
        events.push({ seq: step.seq, type: step.event.type, at: new Date(entry.startedAt + step.t).toISOString(), data: step.event.data });
        if (step.event.type === "files_committed") for (const p of step.event.data.paths) committed.add(p);
      }
    }
    const nextStep = entry.steps.find((s) => s.t > elapsed);
    return { elapsed, status, registered, committed: committed.size, events, nextAt: nextStep ? entry.startedAt + nextStep.t : null };
  }

  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://placeholder");
    const send = (status, body) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(body));
    };
    if (req.method !== "GET") return send(405, { errors: [{ code: "method_not_allowed", message: "read-only" }] });
    if (url.pathname === "/v1/healthz") return send(200, { status: "ok", storage: "synthetic", state: "memory", version: "synthetic-fixture" });

    const preview = /^\/v1\/synthetic\/preview\/([^/]+)\.ply$/.exec(url.pathname);
    if (preview) {
      ply ??= encodePly(syntheticCloud());
      res.writeHead(200, { "content-type": "application/octet-stream", "content-length": ply.length });
      return res.end(ply);
    }

    const m = /^\/v1\/captures\/([^/]+)(?:\/(events|result))?$/.exec(url.pathname);
    const entry = m ? capture(m[1]) : null;
    if (!entry) return send(404, { errors: [{ code: "capture_not_found", pointer: "/", message: "capture not found" }] });

    const inOutage = () => {
      const elapsed = now() - entry.startedAt;
      return entry.scenario === "dropout" && elapsed >= DROPOUT.from && elapsed < DROPOUT.to;
    };
    if (inOutage()) {
      req.socket.destroy(); // a dropped connection, not an HTTP error
      return;
    }

    const [, id, leaf] = m;
    if (leaf === "events") {
      const after = Number(url.searchParams.get("after") ?? "0");
      const waitS = Math.min(25, Number(url.searchParams.get("wait") ?? "0"));
      const deadline = now() + waitS * 1000;
      let state = stateAt(entry);
      while (!state.events.some((e) => e.seq > after) && now() < deadline && state.nextAt != null) {
        const outageAt = entry.scenario === "dropout" && state.elapsed < DROPOUT.from ? entry.startedAt + DROPOUT.from : Infinity;
        const pause = Math.max(1, (Math.min(deadline, state.nextAt, outageAt) - now()) / speed);
        await new Promise((resolve) => setTimeout(resolve, pause));
        if (res.destroyed) return;
        if (inOutage()) {
          req.socket.destroy();
          return;
        }
        state = stateAt(entry);
      }
      const fresh = state.events.filter((e) => e.seq > after).slice(0, 100);
      return send(200, { status: state.status, next: fresh.at(-1)?.seq ?? after, events: fresh });
    }
    const state = stateAt(entry);
    if (leaf === "result") {
      const body = resultBody(entry.scenario === "dropout" ? "complete" : entry.scenario, state.status);
      if (body.previewUrl) body.previewUrl = `${origin}/v1/synthetic/preview/${id}.ply`;
      return send(200, body);
    }
    return send(200, {
      captureId: id,
      packetId: "00000000-0000-4000-8000-000000000000",
      status: state.status,
      tier: "arkit",
      flow: "guided",
      createdAt: new Date(entry.startedAt).toISOString(),
      finalizeBy: new Date(entry.startedAt + 86_400_000).toISOString(),
      filesRegistered: state.registered,
      filesCommitted: state.committed,
      runId: state.status === "uploading" ? null : RUN_ID,
    });
  });

  return {
    server,
    listen(port = 0) {
      return new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(port, "127.0.0.1", () => {
          const bound = server.address().port;
          origin = `http://127.0.0.1:${bound}`;
          resolve(bound);
        });
      });
    },
    close() {
      server.closeAllConnections?.();
      return new Promise((resolve) => server.close(() => resolve()));
    },
  };
}
