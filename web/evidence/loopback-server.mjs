// The loopback harness server: the contract fake (src/lib/contract/contract-fake.ts, the
// same test double every unit test uses) served over real HTTP on 127.0.0.1, so a real
// browser can run the placement session with no network beyond this machine and no real
// placement server. The page's own fetch is never touched; the browser talks to this
// server exactly as it would talk to the production Starlette app, including its CORS
// posture: server/api.py wraps the API in CORSMiddleware with allow_origins=["*"] (and
// refusals must carry CORS headers a browser can read), so this server answers OPTIONS
// preflights and stamps every response the same way. Nothing here can reach, or be
// reached from, anything but the local machine: the listener binds 127.0.0.1.
//
// Control surface (the double's behavior is switched per test from Playwright):
//   POST /__behavior  {"behavior": <FakeBehavior>, "applyTo": ""}  — replace the fake
//   GET  /__behavior  — the current behavior
//   POST /__log/clear — forget the request log
//   GET  /__log       — every non-control request this server has served (the offline
//                       proof: evidence runs assert on what was actually requested)
//
// Run: node evidence/loopback-server.mjs [--port 8907]
// The contract fake is imported as TypeScript; Node strips types natively (Node >= 24,
// the version web/ pins). Only erasable syntax survives that path — the fake uses none.

import { createServer } from "node:http";

const CORS_HEADERS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-headers": "*",
  "access-control-max-age": "600",
};

function parsePort(argv) {
  const flagIndex = argv.indexOf("--port");
  if (flagIndex !== -1 && argv[flagIndex + 1] !== undefined) {
    return Number.parseInt(argv[flagIndex + 1], 10);
  }
  return 8907;
}

async function main() {
  const { createContractFake } = await import("../src/lib/contract/contract-fake.ts");
  const port = parsePort(process.argv);

  let fake = createContractFake({ behavior: { kind: "bound" } });
  let currentBehavior = { kind: "bound" };
  let currentApplyTo = "";
  const served = [];

  const json = (res, status, payload) => {
    const body = JSON.stringify(payload);
    res.writeHead(status, {
      "content-type": "application/json",
      ...CORS_HEADERS,
    });
    res.end(body);
  };

  const server = createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("error", () => {});
    req.on("end", () => {
      const url = new URL(req.url ?? "/", `http://127.0.0.1:${port}`);
      const method = (req.method ?? "GET").toUpperCase();

      if (url.pathname === "/__behavior" && method === "POST") {
        const text = Buffer.concat(chunks).toString("utf8");
        try {
          const parsed = JSON.parse(text);
          if (parsed === null || typeof parsed !== "object") throw new Error("not an object");
          currentBehavior = parsed.behavior;
          currentApplyTo = typeof parsed.applyTo === "string" ? parsed.applyTo : "";
          fake = createContractFake({ behavior: currentBehavior, applyTo: currentApplyTo });
          json(res, 200, { ok: true, behavior: currentBehavior, applyTo: currentApplyTo });
        } catch (error) {
          json(res, 400, { ok: false, error: String(error) });
        }
        return;
      }
      if (url.pathname === "/__behavior" && method === "GET") {
        json(res, 200, { behavior: currentBehavior, applyTo: currentApplyTo });
        return;
      }
      if (url.pathname === "/__log/clear" && method === "POST") {
        served.length = 0;
        res.writeHead(204, CORS_HEADERS);
        res.end();
        return;
      }
      if (url.pathname === "/__log" && method === "GET") {
        json(res, 200, { served });
        return;
      }

      // CORS preflight: answered before the contract surface, exactly as Starlette's
      // CORSMiddleware would for allow_origins=["*"].
      if (method === "OPTIONS") {
        res.writeHead(204, CORS_HEADERS);
        res.end();
        return;
      }

      served.push({ method, url: url.href });
      // Hand the request to the contract fake on its own fetch boundary, then write
      // whatever Response it produced back onto the real socket.
      const headers = {};
      for (const [key, value] of Object.entries(req.headers)) {
        if (typeof value === "string" && key !== "host" && key !== "connection") {
          headers[key] = value;
        }
      }
      const body = Buffer.concat(chunks);
      const init = { method, headers };
      if (body.length > 0) init.body = new Uint8Array(body);

      fake
        .fetch(url.href, init)
        .then(async (response) => {
          const outHeaders = { ...CORS_HEADERS };
          response.headers.forEach((value, key) => {
            outHeaders[key] = value;
          });
          const outBody = Buffer.from(await response.arrayBuffer());
          res.writeHead(response.status, outHeaders);
          res.end(outBody);
        })
        .catch((error) => {
          // The fake should never throw for these behaviors; surface it as a 500 so a
          // harness bug is loud instead of a hanging request.
          json(res, 500, { error: { code: "harness_fault", message: String(error), path: null } });
        });
    });
  });

  await new Promise((resolve) => server.listen(port, "127.0.0.1", resolve));
  console.log(`loopback contract server on http://127.0.0.1:${port} (bound to 127.0.0.1 only)`);

  const shutdown = () => server.close(() => process.exit(0));
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

await main();
