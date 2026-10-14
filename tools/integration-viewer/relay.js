// Loopback server for the viewer: serves the page and relays a fixed set of read-only capture
// API calls. The deployed API sends no CORS headers, so a page cannot call it directly.
//
// The relay is deliberately narrow. It answers only on 127.0.0.1, only to a loopback Host header
// (which blocks DNS rebinding), only GET and HEAD, only to named upstreams chosen at startup, and only for
// health, capture status, events, result and the result's preview cloud. The browser never
// supplies a URL. Signed storage URLs in the result stay inside the relay: the page gets a
// placeholder, and the relay fetches the preview itself after checking the URL's host.

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join } from "node:path";

const CAPTURE_ID = /^cap_[A-Za-z0-9_]{4,64}$/;
const STATIC_NAME = /^[a-z0-9-]+\.(html|js|css|svg)$/;
const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml" };
const PREVIEW_MAX_BYTES = 64 * 1024 * 1024;
// JSON replies are small (100 events per page at most); anything past this is a faulty upstream.
const JSON_MAX_BYTES = 8 * 1024 * 1024;
const RUN_ID = /^[A-Za-z0-9_-]{1,80}$/;
const WITHHELD = "withheld-by-viewer-relay";
const PREVIEW_CREDENTIAL_PARAM = /^(?:signature|sig|secret|token|access_token|expires|expiry|expiration|googleaccessid|x-(?:goog|amz)-(?:algorithm|credential|date|expires|signedheaders|signature|security-token))$/i;

function previewArtifactId(url) {
  const withoutFragment = url.split("#", 1)[0];
  const queryAt = withoutFragment.indexOf("?");
  const path = queryAt < 0 ? withoutFragment : withoutFragment.slice(0, queryAt);
  const query = queryAt < 0 ? "" : withoutFragment.slice(queryAt + 1);
  const params = new URLSearchParams(query);
  // Versions such as generation/versionId select the file. Unknown parameters stay too;
  // only known signing fields are removed so renewed credentials keep the retry deadline.
  for (const name of [...params.keys()]) if (PREVIEW_CREDENTIAL_PARAM.test(name)) params.delete(name);
  params.sort();
  const resource = params.size === 0 ? path : `${path}?${params}`;
  return createHash("sha256").update(resource).digest("hex");
}

/**
 * @param {object} options
 * @param {Record<string, { base: string, label: string, kind: "backend" | "synthetic" | "local", previewHosts?: string[] }>} options.upstreams
 *   `base` ends in /v1. `previewHosts` lists extra hosts a signed preview URL may point at; the
 *   upstream's own origin is always allowed.
 * @param {string} options.publicDir
 * @param {(line: string) => void} [options.log]
 */
export function createViewerServer({ upstreams, publicDir, log = () => {} }) {
  for (const [key, up] of Object.entries(upstreams)) {
    if (!/^[a-z0-9-]{1,24}$/.test(key)) throw new Error(`upstream key ${key} must be lowercase letters, digits or dashes`);
    const url = new URL(up.base);
    if (!url.pathname.endsWith("/v1")) throw new Error(`upstream ${key} base must end in /v1`);
    if (url.protocol !== "https:" && !isLoopbackHost(url.hostname)) throw new Error(`upstream ${key} must be https or loopback`);
  }

  let port = 0;
  const server = createServer(async (req, res) => {
    const route = await handle(req, res).catch((error) => {
      send(res, 500, { errors: [{ code: "viewer_internal", message: String(error?.message ?? error) }] });
      return "error";
    });
    log(`${req.method} ${route} ${res.statusCode}`);
  });

  async function handle(req, res) {
    if (!allowedHost(req.headers.host, port)) return refuse(res, 421, "host_not_loopback", "request Host must be 127.0.0.1 or localhost");
    if (req.method !== "GET" && req.method !== "HEAD") return refuse(res, 405, "method_not_allowed", "the viewer is read-only");
    const raw = req.url ?? "/";
    const [path, query = ""] = raw.split("?", 2);
    if (path.includes("%") || path.includes("..") || path.includes("//") || path.includes("\\")) {
      return refuse(res, 400, "path_refused", "encoded, relative or doubled path segments are refused");
    }
    if (path === "/") return serveStatic(res, "index.html");
    if (path === "/api/config") {
      send(res, 200, { upstreams: Object.entries(upstreams).map(([key, u]) => ({ key, label: u.label, kind: u.kind, origin: new URL(u.base).origin })) });
      return "config";
    }
    const staticMatch = /^\/([^/]+)$/.exec(path);
    if (staticMatch) return serveStatic(res, staticMatch[1]);

    const m = /^\/relay\/([a-z0-9-]+)\/(healthz|captures\/([^/]+)(?:\/(events|result|preview))?)$/.exec(path);
    if (!m) return refuse(res, 404, "route_not_relayed", "only health, capture status, events, result and preview are relayed");
    const [, key, , captureId, leaf] = m;
    const upstream = Object.hasOwn(upstreams, key) ? upstreams[key] : null;
    if (!upstream) return refuse(res, 404, "upstream_unknown", "no upstream with that name");
    if (captureId != null && !CAPTURE_ID.test(captureId)) return refuse(res, 400, "capture_id_invalid", "capture ids look like cap_ followed by letters and digits");

    const params = new URLSearchParams(query);
    let upstreamPath = captureId == null ? "/healthz" : `/captures/${captureId}${leaf && leaf !== "preview" ? `/${leaf}` : ""}`;
    let timeoutMs = 15_000;
    if (leaf === "events") {
      const after = params.get("after") ?? "0";
      const wait = params.get("wait") ?? "0";
      if (!/^\d{1,9}$/.test(after) || !/^\d{1,2}$/.test(wait) || Number(wait) > 25) {
        return refuse(res, 400, "query_invalid", "events takes after=<non-negative integer> and wait=<0-25>");
      }
      if ([...params.keys()].some((k) => k !== "after" && k !== "wait")) return refuse(res, 400, "query_invalid", "events takes only after and wait");
      upstreamPath += `?after=${Number(after)}&wait=${Number(wait)}`;
      timeoutMs = Number(wait) * 1000 + 15_000;
    } else if (leaf === "preview") {
      const run = params.get("run");
      const artifact = params.get("artifact");
      if (run == null || !RUN_ID.test(run) || params.getAll("run").length !== 1 ||
          (artifact != null && (!/^[a-f0-9]{64}$/.test(artifact) || params.getAll("artifact").length !== 1)) ||
          [...params.keys()].some((key) => key !== "run" && key !== "artifact")) {
        return refuse(res, 400, "query_invalid", "preview takes run=<run id> and optional artifact=<preview artifact id>");
      }
    } else if (query !== "") {
      return refuse(res, 400, "query_invalid", "this route takes no query");
    }

    const abort = new AbortController();
    res.on("close", () => abort.abort());
    const signal = AbortSignal.any([abort.signal, AbortSignal.timeout(timeoutMs)]);

    if (leaf === "preview") return relayPreview(res, upstream, upstreamPath, params.get("run"), params.get("artifact"), signal);

    const reply = await upstreamGet(upstream.base + upstreamPath, signal);
    if (reply.failure) return refuse(res, reply.failure.status, reply.failure.code, reply.failure.message);
    let body = reply.json;
    if (leaf === "result" && body && typeof body === "object") {
      body = { ...body };
      // The digest distinguishes resource versions without exposing the signed URL.
      body.previewArtifactId = typeof body.previewUrl === "string" && body.previewUrl !== ""
        ? previewArtifactId(body.previewUrl) : null;
      // Only a real URL is replaced; an empty value still means "no preview" to the page.
      for (const field of ["previewUrl", "verdictUrl"]) if (typeof body[field] === "string" && body[field] !== "") body[field] = WITHHELD;
    }
    const headers = {};
    if (reply.etag) headers.etag = reply.etag;
    if (reply.retryAfter) headers["retry-after"] = reply.retryAfter;
    send(res, reply.status, body, headers);
    return `relay ${key} ${leaf ?? (captureId ? "status" : "healthz")}`;
  }

  async function relayPreview(res, upstream, resultPath, runId, artifactId, signal) {
    const result = await upstreamGet(`${upstream.base}${resultPath}/result`, signal);
    if (result.failure) return refuse(res, result.failure.status, result.failure.code, result.failure.message);
    if (result.status !== 200) return refuse(res, result.status, "result_unavailable", "the result could not be read", result.retryAfter ? { "retry-after": result.retryAfter } : {});
    if (result.json?.runId !== runId) return refuse(res, 409, "preview_run_changed", "the latest result belongs to another run");
    const signed = result.json?.previewUrl;
    if (typeof signed !== "string" || signed === "") return refuse(res, 404, "preview_not_available", "the result has no preview");
    // A same-run result may move to another file after the page read it. Refuse before
    // fetching so a storage Retry-After cannot be attributed to the page's older file.
    if (artifactId != null && artifactId !== previewArtifactId(signed)) return refuse(res, 409, "preview_artifact_changed", "the latest result references another preview artifact");
    let target;
    try {
      target = new URL(signed, new URL(upstream.base).origin);
    } catch {
      return refuse(res, 502, "preview_url_invalid", "the result's preview URL did not parse");
    }
    const allowed = new Set([new URL(upstream.base).host, ...(upstream.previewHosts ?? [])]);
    const secure = target.protocol === "https:" || (target.protocol === "http:" && isLoopbackHost(target.hostname));
    if (!secure || !allowed.has(target.host)) return refuse(res, 502, "preview_host_refused", "the preview URL points at a host the viewer does not fetch from");
    let reply;
    try {
      reply = await fetch(target, { signal, redirect: "error" });
    } catch (error) {
      return refuse(res, 502, "preview_unreachable", error?.name === "TimeoutError" ? "preview fetch timed out" : "preview fetch failed");
    }
    if (!reply.ok) {
      const retryAfter = reply.headers.get("retry-after");
      await reply.body?.cancel().catch(() => {});
      return refuse(res, 502, "preview_fetch_failed", `storage answered ${reply.status}`, retryAfter ? { "retry-after": retryAfter } : {});
    }
    const declared = Number(reply.headers.get("content-length") ?? "0");
    if (declared > PREVIEW_MAX_BYTES) return refuse(res, 502, "preview_too_large", "the preview is larger than the viewer reads");
    const chunks = [];
    let size = 0;
    for await (const chunk of reply.body) {
      size += chunk.length;
      if (size > PREVIEW_MAX_BYTES) return refuse(res, 502, "preview_too_large", "the preview is larger than the viewer reads");
      chunks.push(chunk);
    }
    res.writeHead(200, { "content-type": "application/octet-stream", "cache-control": "no-store", "x-content-type-options": "nosniff" });
    res.end(Buffer.concat(chunks));
    return "relay preview";
  }

  async function serveStatic(res, name) {
    if (!STATIC_NAME.test(name)) return refuse(res, 404, "not_found", "no such file");
    let bytes;
    try {
      bytes = await readFile(join(publicDir, name));
    } catch {
      return refuse(res, 404, "not_found", "no such file");
    }
    res.writeHead(200, {
      "content-type": MIME[extname(name)],
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "content-security-policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
    });
    res.end(bytes);
    return `static ${name}`;
  }

  return {
    server,
    /** Listens on 127.0.0.1 only. Resolves to the bound port. */
    listen(requested = 0) {
      return new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(requested, "127.0.0.1", () => {
          port = server.address().port;
          resolve(port);
        });
      });
    },
    close() {
      server.closeAllConnections?.();
      return new Promise((resolve) => server.close(() => resolve()));
    },
  };
}

async function upstreamGet(url, signal) {
  let reply;
  try {
    reply = await fetch(url, { signal, redirect: "error", headers: { accept: "application/json" } });
  } catch (error) {
    const timedOut = error?.name === "TimeoutError";
    return { failure: { status: timedOut ? 504 : 502, code: timedOut ? "upstream_timeout" : "upstream_unreachable", message: timedOut ? "the API did not answer in time" : "the API could not be reached" } };
  }
  const declared = Number(reply.headers.get("content-length") ?? "0");
  if (declared > JSON_MAX_BYTES) {
    await reply.body?.cancel().catch(() => {});
    return { failure: { status: 502, code: "upstream_too_large", message: "the API reply is larger than the viewer reads" } };
  }
  let text = "";
  try {
    const chunks = [];
    let size = 0;
    for await (const chunk of reply.body ?? []) {
      size += chunk.length;
      if (size > JSON_MAX_BYTES) {
        await reply.body.cancel().catch(() => {});
        return { failure: { status: 502, code: "upstream_too_large", message: "the API reply is larger than the viewer reads" } };
      }
      chunks.push(chunk);
    }
    text = Buffer.concat(chunks).toString("utf8");
  } catch {
    return { failure: { status: 502, code: "upstream_unreachable", message: "the API reply was cut off" } };
  }
  let json;
  try {
    json = text === "" ? null : JSON.parse(text);
  } catch {
    return { failure: { status: 502, code: "upstream_not_json", message: `the API answered ${reply.status} with a body that is not JSON` } };
  }
  return { status: reply.status, json, etag: reply.headers.get("etag"), retryAfter: reply.headers.get("retry-after") };
}

function refuse(res, status, code, message, headers = {}) {
  send(res, status, { errors: [{ code, message }] }, headers);
  return `refused ${code}`;
}

function send(res, status, body, headers = {}) {
  if (res.headersSent) {
    res.end();
    return;
  }
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", "x-content-type-options": "nosniff", ...headers });
  res.end(JSON.stringify(body));
}

/** Browsers leave the port out of Host when it is 80, so port 80 also accepts the bare names. */
export function allowedHost(host, port) {
  if (typeof host !== "string") return false;
  const names = ["127.0.0.1", "localhost", "[::1]"];
  const allowed = names.map((n) => `${n}:${port}`);
  if (port === 80) allowed.push(...names);
  return allowed.includes(host.toLowerCase());
}

function isLoopbackHost(hostname) {
  return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "[::1]" || hostname === "::1";
}
