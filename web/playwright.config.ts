// Playwright evidence config: the real app (the production build, served by `vite
// preview`) driven against the loopback contract server (`evidence/loopback-server.mjs`,
// the contract fake behind HTTP). Two local servers, both bound to 127.0.0.1; nothing a
// test does can reach a real network endpoint — tests additionally assert that every
// page request stayed on loopback (see tests/helpers.ts). Workers stay at one because
// the fake's behavior is switched per test through its control endpoint; serializing
// keeps every run deterministic and reproducible offline.
//
// Run: `pnpm test:e2e` (builds the production bundle first, so evidence always reflects
// a fresh production build). Screenshots and axe results are written into `evidence/`
// and committed; Playwright's own run artifacts go to `test-results/` (gitignored).
//
// Ports 4301 and 8907 are chosen for this sandbox and reused nowhere else; servers are
// never reused across runs (reuseExistingServer: false) so a port collision fails
// loudly instead of silently testing against someone else's server.

import { defineConfig } from "@playwright/test";

const APP_PORT = 4301;
const FAKE_PORT = 8907;

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 30_000,
  outputDir: "test-results",
  use: {
    baseURL: `http://127.0.0.1:${APP_PORT}`,
    viewport: { width: 1280, height: 800 },
    trace: "off",
  },
  webServer: [
    {
      // `exec` makes the preview server replace the shell, so Playwright's teardown
      // SIGTERM reaches vite itself and the port is actually released.
      command: `node node_modules/vite/bin/vite.js build && exec node node_modules/vite/bin/vite.js preview --port ${APP_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${APP_PORT}/`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `node evidence/loopback-server.mjs --port ${FAKE_PORT}`,
      url: `http://127.0.0.1:${FAKE_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
