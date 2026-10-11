// Shared helpers for the evidence specs. Every page runs behind an offline guard: any
// request whose host is not loopback is aborted and recorded, and the guard asserts the
// record is empty when the test ends — so a run that touched anything but 127.0.0.1
// would fail, not merely pass quietly. The scene fixture is the contract fake's own
// synthetic scene (the same text the unit tests intake), and its SHA-256 is computed
// here with node:crypto — an oracle independent of the app's WebCrypto path.

import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test as base, expect, type Page } from "@playwright/test";
import type { FakeBehavior } from "../src/lib/contract/contract-fake.ts";
import { sceneText } from "../src/lib/contract/contract-fake.ts";

/** Where the loopback contract server listens (see evidence/loopback-server.mjs). */
export const LOOPBACK_ORIGIN = "http://127.0.0.1:8907";

/** Committed evidence roots. */
export const EVIDENCE_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "evidence",
);
export const SHOTS_DIR = path.join(EVIDENCE_DIR, "screenshots");
export const AXE_DIR = path.join(EVIDENCE_DIR, "axe");

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

export const test = base.extend({
  page: async ({ page }, use) => {
    const offLoopback: string[] = [];
    await page.route("**/*", (route) => {
      const url = new URL(route.request().url());
      if (LOOPBACK_HOSTS.has(url.hostname)) {
        return route.continue();
      }
      offLoopback.push(url.href);
      return route.abort("blocked");
    });
    await use(page);
    expect(offLoopback, "every page request must stay on loopback (offline evidence)").toEqual([]);
  },
});

/** Switch the contract fake's behavior for subsequent requests (loopback control call). */
export async function setBehavior(behavior: FakeBehavior): Promise<void> {
  const response = await fetch(`${LOOPBACK_ORIGIN}/__behavior`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ behavior, applyTo: "" }),
  });
  if (!response.ok) {
    throw new Error(`the behavior control call failed with ${response.status}`);
  }
}

/** The loopback server's log of every contract request it has served. */
export async function servedRequests(): Promise<readonly { method: string; url: string }[]> {
  const response = await fetch(`${LOOPBACK_ORIGIN}/__log`);
  if (!response.ok) {
    throw new Error(`the log control call failed with ${response.status}`);
  }
  const payload = (await response.json()) as { served: { method: string; url: string }[] };
  return payload.served;
}

/** Writes the synthetic scene to a temp file, hashing the exact bytes with node:crypto. */
export async function writeSceneFile(): Promise<{
  filePath: string;
  sha256: string;
  text: string;
}> {
  const text = sceneText();
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "basescanning-scene-"));
  const filePath = path.join(dir, "scene.json");
  await fs.writeFile(filePath, text, "utf8");
  const sha256 = createHash("sha256").update(Buffer.from(text, "utf8")).digest("hex");
  return { filePath, sha256, text };
}

export async function screenshot(page: Page, fileName: string): Promise<void> {
  await fs.mkdir(SHOTS_DIR, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS_DIR, fileName), fullPage: true });
}

/**
 * Points the page at the loopback fake, selects the synthetic scene file, and waits for
 * intake. Returns the upload's SHA-256 as computed by the independent oracle.
 */
export async function prepare(
  page: Page,
  behavior: FakeBehavior = { kind: "bound" },
): Promise<string> {
  await setBehavior(behavior);
  const { filePath, sha256 } = await writeSceneFile();
  await page.goto("/");
  await page.fill("#server-base-url", LOOPBACK_ORIGIN);
  await page.setInputFiles("#scene-file", filePath);
  await expect(page.getByText("Loaded scene.json")).toBeVisible();
  return sha256;
}

/**
 * The refusal behavior the refusal-state evidence uses: a 422 in the contract's uniform
 * envelope, with a message and a JSON-pointer path (the shape the real server refuses
 * scenes with, per server/api.py's module docstring).
 */
export const REFUSED_422 = {
  kind: "refused",
  status: 422,
  code: "invalid_scene",
  message: "No wall carries the meter within the observed evidence.",
  path: "/meter/wall_id",
} as const satisfies FakeBehavior;

/**
 * The bearer token the token-related evidence runs use. Synthetic — like everything
 * else in these fixtures, it stands for a credential and must never reach storage.
 */
export const SYNTHETIC_TOKEN = "synthetic-bearer-token-9f2c";

/**
 * The browser-local storage key the persisted record lives under. The constant is
 * defined in src/lib/session/persist.ts (SESSION_STORAGE_KEY); it is restated here
 * because the spec must not import that module into the Node tsconfig project —
 * persist.ts touches DOM-only globals. persist.test.ts pins the same literal.
 */
export const STORAGE_KEY = "basescanning-web.session.v1";
