// Exercises the relay through its HTTP boundary, with the synthetic API and a hostile stub as upstreams.

import assert from "node:assert/strict";
import { request } from "node:http";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { parsePly } from "../public/ply.js";
import { allowedHost, createViewerServer } from "../relay.js";
import { createSyntheticApi } from "../synthetic-api.js";

const publicDir = join(dirname(fileURLToPath(import.meta.url)), "..", "public");
let synthetic;
let hostile;
let hostileBody;
let hostileHandler = null;
let viewer;
let port;

/** Sends a raw request so the path is not normalized by a URL parser first. */
function raw(path, { method = "GET", host } = {}) {
  return new Promise((resolve, reject) => {
    const req = request({ host: "127.0.0.1", port, path, method, headers: { host: host ?? `127.0.0.1:${port}` } }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        const body = Buffer.concat(chunks);
        let json = null;
        try {
          json = JSON.parse(body.toString("utf8"));
        } catch {}
        resolve({ status: res.statusCode, headers: res.headers, body, json });
      });
    });
    req.on("error", reject);
    req.end();
  });
}

before(async () => {
  synthetic = createSyntheticApi({ speed: 40 });
  const sPort = await synthetic.listen(0);
  hostile = createServer((req, res) => {
    if (hostileHandler) return hostileHandler(req, res);
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(hostileBody));
  });
  await new Promise((r) => hostile.listen(0, "127.0.0.1", r));
  viewer = createViewerServer({
    upstreams: {
      synthetic: { base: `http://127.0.0.1:${sPort}/v1`, label: "Synthetic", kind: "synthetic" },
      hostile: { base: `http://127.0.0.1:${hostile.address().port}/v1`, label: "Hostile", kind: "local" },
      dead: { base: "http://127.0.0.1:9/v1", label: "Nothing listening", kind: "local" },
    },
    publicDir,
  });
  port = await viewer.listen(0);
});

after(async () => {
  await viewer.close();
  await synthetic.close();
  hostile.closeAllConnections();
  await new Promise((r) => hostile.close(r));
});

describe("allowed reads", () => {
  test("serves the page and the source list", async () => {
    assert.equal((await raw("/")).status, 200);
    assert.equal((await raw("/app.js")).headers["content-type"], "text/javascript; charset=utf-8");
    const config = await raw("/api/config");
    assert.deepEqual(config.json.upstreams.map((u) => u.key), ["synthetic", "hostile", "dead"]);
  });

  test("relays health, status and events", async () => {
    assert.equal((await raw("/relay/synthetic/healthz")).json.storage, "synthetic");
    const status = await raw("/relay/synthetic/captures/cap_SYNTH_COMPLETE_R1");
    assert.equal(status.status, 200);
    assert.equal(status.json.captureId, "cap_SYNTH_COMPLETE_R1");
    const events = await raw("/relay/synthetic/captures/cap_SYNTH_COMPLETE_R1/events?after=0&wait=0");
    assert.equal(events.status, 200);
    assert.ok(Array.isArray(events.json.events));
  });

  test("passes the API's own 404 through", async () => {
    const missing = await raw("/relay/synthetic/captures/cap_SYNTH_NOPE_1");
    assert.equal(missing.status, 404);
    assert.equal(missing.json.errors[0].code, "capture_not_found");
  });

  test("withholds signed URLs from the page and fetches the preview itself", async () => {
    const id = "cap_SYNTH_COMPLETE_R2";
    await raw(`/relay/synthetic/captures/${id}`);
    let result;
    for (let i = 0; i < 60; i += 1) {
      result = await raw(`/relay/synthetic/captures/${id}/result`);
      if (result.json.outcome) break;
      await new Promise((r) => setTimeout(r, 50));
    }
    assert.equal(result.json.outcome.kind, "eligible");
    assert.equal(result.json.previewUrl, "withheld-by-viewer-relay");
    const preview = await raw(`/relay/synthetic/captures/${id}/preview?run=${result.json.runId}`);
    assert.equal(preview.status, 200);
    const cloud = parsePly(preview.body.buffer.slice(preview.body.byteOffset, preview.body.byteOffset + preview.body.byteLength));
    assert.ok(cloud.count > 1000);
  });

  test("preview identity survives renewed credentials but changes for another file", async () => {
    const read = async (previewUrl) => {
      hostileBody = { runId: "run_s", previewUrl, verdictUrl: "https://storage.example/verdict?secret=verdict" };
      const reply = await raw("/relay/hostile/captures/cap_ARTIFACT/result");
      assert.equal(reply.status, 200);
      assert.equal(reply.json.previewUrl, "withheld-by-viewer-relay");
      assert.equal(reply.json.verdictUrl, "withheld-by-viewer-relay");
      assert.match(reply.json.previewArtifactId, /^[a-f0-9]{64}$/);
      assert.ok(!reply.body.toString().includes("secret="));
      return reply.json.previewArtifactId;
    };
    const first = await read("https://storage.example/model-a.ply?secret=first");
    assert.equal(await read("https://storage.example/model-a.ply?secret=renewed"), first);
    assert.notEqual(await read("https://storage.example/model-b.ply?secret=renewed"), first);
    hostileBody = { runId: "run_s", previewUrl: "" };
    assert.equal((await raw("/relay/hostile/captures/cap_ARTIFACT/result")).json.previewArtifactId, null);
  });
});

describe("refusals", () => {
  const cases = [
    ["dot segments", "/relay/synthetic/captures/../healthz", 400, "path_refused"],
    ["encoded dot segments", "/relay/synthetic/captures/%2e%2e/healthz", 400, "path_refused"],
    ["encoded slash", "/relay/synthetic/captures/cap_A%2FB", 400, "path_refused"],
    ["doubled slash", "/relay/synthetic//healthz", 400, "path_refused"],
    ["static traversal", "/../relay.js", 400, "path_refused"],
    ["server source", "/relay.js", 404, "not_found"],
    ["unknown upstream", "/relay/elsewhere/healthz", 404, "upstream_unknown"],
    ["inherited key", "/relay/constructor/healthz", 404, "upstream_unknown"],
    ["unrelayed route", "/relay/synthetic/captures/cap_SYNTH_COMPLETE_R1/finalize", 404, "route_not_relayed"],
    ["file routes", "/relay/synthetic/captures/cap_SYNTH_COMPLETE_R1/files", 404, "route_not_relayed"],
    ["a URL as a path", "/relay/https://example.com/v1/healthz", 400, "path_refused"],
    ["bad capture id", "/relay/synthetic/captures/not-a-capture", 400, "capture_id_invalid"],
    ["wait over 25", "/relay/synthetic/captures/cap_SYNTH_COMPLETE_R1/events?after=0&wait=26", 400, "query_invalid"],
    ["negative cursor", "/relay/synthetic/captures/cap_SYNTH_COMPLETE_R1/events?after=-1&wait=0", 400, "query_invalid"],
    ["extra parameter", "/relay/synthetic/captures/cap_SYNTH_COMPLETE_R1/events?after=0&wait=0&url=x", 400, "query_invalid"],
    ["query on status", "/relay/synthetic/captures/cap_SYNTH_COMPLETE_R1?x=1", 400, "query_invalid"],
  ];
  for (const [name, path, status, code] of cases) {
    test(name, async () => {
      const reply = await raw(path);
      assert.equal(reply.status, status);
      assert.equal(reply.json.errors[0].code, code);
    });
  }

  test("writes are refused", async () => {
    for (const method of ["POST", "PUT", "DELETE", "PATCH"]) {
      const reply = await raw("/relay/synthetic/captures/cap_SYNTH_COMPLETE_R1", { method });
      assert.equal(reply.status, 405, method);
    }
  });

  test("a non-loopback Host header is refused, which blocks DNS rebinding", async () => {
    const reply = await raw("/relay/synthetic/healthz", { host: "attacker.example" });
    assert.equal(reply.status, 421);
    assert.equal((await raw("/", { host: `localhost:${port}` })).status, 200);
  });

  test("a preview URL on another host is not fetched", async () => {
    hostileBody = { runId: "r", status: "complete", outcome: null, previewUrl: "https://attacker.example/steal.ply" };
    const reply = await raw("/relay/hostile/captures/cap_HOSTILE_1/preview?run=r");
    assert.equal(reply.status, 502);
    assert.equal(reply.json.errors[0].code, "preview_host_refused");
    hostileBody = { runId: "r", status: "complete", outcome: null, previewUrl: "file:///etc/passwd" };
    assert.equal((await raw("/relay/hostile/captures/cap_HOSTILE_1/preview?run=r")).json.errors[0].code, "preview_host_refused");
  });

  test("a result without a preview says so", async () => {
    hostileBody = { runId: "r", status: "complete", outcome: null };
    const reply = await raw("/relay/hostile/captures/cap_HOSTILE_1/preview?run=r");
    assert.equal(reply.status, 404);
    assert.equal(reply.json.errors[0].code, "preview_not_available");
  });

  test("port 80 accepts the portless loopback Host a browser sends; other ports do not", () => {
    assert.ok(allowedHost("127.0.0.1", 80));
    assert.ok(allowedHost("localhost", 80));
    assert.ok(!allowedHost("localhost", 4317));
    assert.ok(!allowedHost("attacker.example", 80));
  });

  test("an empty preview URL stays empty, so the page shows no preview", async () => {
    hostileBody = { runId: "r", status: "complete", outcome: { kind: "manual_review" }, previewUrl: "", verdictUrl: "" };
    const reply = await raw("/relay/hostile/captures/cap_HOSTILE_1/result");
    assert.equal(reply.json.previewUrl, "");
    assert.equal(reply.json.verdictUrl, "");
  });

  test("a preview is refused when the latest result belongs to another run", async () => {
    hostileBody = { runId: "run_new", status: "complete", outcome: null, previewUrl: "/v1/p.ply" };
    const reply = await raw("/relay/hostile/captures/cap_HOSTILE_1/preview?run=run_old");
    assert.equal(reply.status, 409);
    assert.equal(reply.json.errors[0].code, "preview_run_changed");
    assert.equal((await raw("/relay/hostile/captures/cap_HOSTILE_1/preview")).json.errors[0].code, "query_invalid");
  });

  test("a same-run preview file change is refused before storage is fetched", async () => {
    let storageReads = 0;
    let previewUrl = "/v1/file-a.ply";
    hostileHandler = (req, res) => {
      if (req.url.endsWith("/result")) {
        res.writeHead(200, { "content-type": "application/json" });
        return res.end(JSON.stringify({ runId: "r", previewUrl }));
      }
      storageReads += 1;
      res.writeHead(503, { "retry-after": "120" });
      res.end("busy");
    };
    try {
      const path = "/relay/hostile/captures/cap_HOSTILE_1";
      const first = await raw(`${path}/result`);
      previewUrl = "/v1/file-b.ply";
      const stale = await raw(`${path}/preview?run=r&artifact=${first.json.previewArtifactId}`);
      assert.equal(stale.status, 409);
      assert.equal(stale.json.errors[0].code, "preview_artifact_changed");
      assert.equal(storageReads, 0);
      assert.equal(stale.headers["retry-after"], undefined);
      const current = await raw(`${path}/result`);
      const failed = await raw(`${path}/preview?run=r&artifact=${current.json.previewArtifactId}`);
      assert.equal(failed.status, 502);
      assert.equal(failed.headers["retry-after"], "120");
      assert.equal(storageReads, 1);
      for (const query of ["run=r&artifact=not-a-digest", `run=r&artifact=${current.json.previewArtifactId}&artifact=${current.json.previewArtifactId}`, "run=r&run=r"]) {
        assert.equal((await raw(`${path}/preview?${query}`)).status, 400);
      }
      assert.equal(storageReads, 1);
    } finally { hostileHandler = null; }
  });

  test("a preview URL that redirects is not followed", async () => {
    hostileHandler = (req, res) => {
      if (req.url.endsWith("/result")) {
        res.writeHead(200, { "content-type": "application/json" });
        return res.end(JSON.stringify({ runId: "r", status: "complete", outcome: null, previewUrl: "/v1/moved.ply" }));
      }
      res.writeHead(302, { location: "https://attacker.example/x.ply" });
      res.end();
    };
    try {
      const reply = await raw("/relay/hostile/captures/cap_HOSTILE_1/preview?run=r");
      assert.equal(reply.status, 502);
      assert.equal(reply.json.errors[0].code, "preview_unreachable");
    } finally {
      hostileHandler = null;
    }
  });

  test("the API's Retry-After reaches the page", async () => {
    hostileHandler = (req, res) => {
      res.writeHead(429, { "content-type": "application/json", "retry-after": "7" });
      res.end(JSON.stringify({ errors: [{ code: "too_many_polls", message: "slow down" }] }));
    };
    try {
      const reply = await raw("/relay/hostile/captures/cap_HOSTILE_1/events?after=0&wait=0");
      assert.equal(reply.status, 429);
      assert.equal(reply.headers["retry-after"], "7");
    } finally {
      hostileHandler = null;
    }
  });

  test("preview result and storage failures preserve Retry-After", async () => {
    for (const failingPath of ["/result", "/v1/throttled.ply"]) {
      hostileHandler = (req, res) => {
        if (req.url.endsWith(failingPath)) {
          res.writeHead(503, { "content-type": "application/json", "retry-after": "19" });
          return res.end(JSON.stringify({ errors: [{ code: "busy" }] }));
        }
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ runId: "r", previewUrl: "/v1/throttled.ply" }));
      };
      try {
        const reply = await raw("/relay/hostile/captures/cap_HOSTILE_1/preview?run=r");
        assert.equal(reply.status, failingPath === "/result" ? 503 : 502);
        assert.equal(reply.headers["retry-after"], "19");
      } finally {
        hostileHandler = null;
      }
    }
  });

  test("an errored storage body cannot replace the preview 502 or Retry-After", async (t) => {
    const storageUrl = `http://127.0.0.1:${hostile.address().port}/v1/errored.ply`;
    hostileBody = { runId: "r", previewUrl: storageUrl };
    const fetchImpl = globalThis.fetch;
    let storageReads = 0;
    t.mock.method(globalThis, "fetch", async (url, init) => {
      if (String(url) !== storageUrl) return fetchImpl(url, init);
      storageReads += 1;
      const body = new ReadableStream({ start(controller) { controller.error(new Error("storage stream failed")); } });
      return new Response(body, { status: 503, headers: { "retry-after": "19" } });
    });
    const reply = await raw("/relay/hostile/captures/cap_HOSTILE_1/preview?run=r");
    assert.equal(storageReads, 1);
    assert.equal(reply.status, 502);
    assert.equal(reply.json.errors[0].code, "preview_fetch_failed");
    assert.equal(reply.headers["retry-after"], "19");
  });

  test("an oversized API reply is refused, not buffered", async () => {
    hostileHandler = (req, res) => {
      res.writeHead(200, { "content-type": "application/json" });
      const chunk = Buffer.alloc(1024 * 1024, 32);
      let sent = 0;
      const pump = () => {
        while (sent < 12 && res.write(chunk)) sent += 1;
        if (sent < 12) res.once("drain", () => { sent += 1; pump(); });
        else res.end();
      };
      pump();
    };
    try {
      const reply = await raw("/relay/hostile/captures/cap_HOSTILE_1");
      assert.equal(reply.status, 502);
      assert.equal(reply.json.errors[0].code, "upstream_too_large");
    } finally {
      hostileHandler = null;
    }
  });

  test("an unreachable API is a 502, not a crash", async () => {
    const reply = await raw("/relay/dead/healthz");
    assert.equal(reply.status, 502);
    assert.equal(reply.json.errors[0].code, "upstream_unreachable");
  });

  test("startup refuses a plain-http upstream that is not loopback", () => {
    assert.throws(() => createViewerServer({ upstreams: { x: { base: "http://example.com/v1", label: "x", kind: "local" } }, publicDir }), /https or loopback/);
  });
});
