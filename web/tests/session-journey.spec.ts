// Real-browser evidence for the placement session, driven through the production build
// served on loopback with the contract fake behind HTTP (see evidence/loopback-server.mjs).
// Every interaction here is reproducible offline: the offline guard in helpers.ts aborts
// and records any page request that is not loopback, and the loopback server's own log
// (/__log) records what the app actually sent. Screenshots are committed under
// evidence/screenshots/ with the names below, so a reviewer can see each claimed state.

import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect } from "@playwright/test";
import { sceneText } from "../src/lib/contract/contract-fake.ts";
import {
  LOOPBACK_ORIGIN,
  prepare,
  REFUSED_422,
  STORAGE_KEY,
  SYNTHETIC_TOKEN,
  screenshot,
  servedRequests,
  setBehavior,
  test,
} from "./helpers.ts";

test.describe("the placement session journey", () => {
  test("picks a scene, submits, and shows a result bound to the upload hash", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("status")).toHaveText("No scene selected.");
    await screenshot(page, "01-empty-desktop.png");

    const sha256 = await prepare(page);
    await expect(page.getByRole("status")).toHaveText("Scene ready. No attempts yet.");

    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("bound, decision pass");
    await expect(page.getByRole("heading", { name: "Result", exact: true })).toBeVisible();
    await expect(page.getByText("Decision: pass", { exact: true })).toBeVisible();
    await expect(page.getByText("Bound to upload").first()).toBeVisible();
    await expect(page.locator("code").filter({ hasText: sha256 }).first()).toBeVisible();
    await screenshot(page, "02-bound-result.png");

    // The upload reached the loopback fake, and only it: POST /v1/placements, no
    // other host, no other route.
    const served = await servedRequests();
    expect(served.at(-1)).toEqual({
      method: "POST",
      url: `${LOOPBACK_ORIGIN}/v1/placements`,
    });
  });

  test("cancels a mid-flight attempt and a retry binds", async ({ page }) => {
    await prepare(page, { kind: "delayed_then_abort", delayMs: 5000 });
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("submitting");

    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("status")).toContainText("cancelled");
    await expect(page.getByRole("heading", { name: "Cancelled" })).toBeVisible();
    await expect(
      page.getByText("The attempt was cancelled before an answer arrived."),
    ).toBeVisible();
    await screenshot(page, "03-cancelled-midflight.png");

    await setBehavior({ kind: "bound" });
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("bound, decision pass");
  });

  test("shows a 422 refusal with the server's message and path", async ({ page }) => {
    await prepare(page, REFUSED_422);
    await page.getByRole("button", { name: "Run placement" }).click();

    await expect(page.getByRole("status")).toContainText("refused by the server");
    await expect(page.getByRole("alert")).toContainText(
      "The server refused the scene (invalid_scene).",
    );
    // The refusal detail also appears in the session history, so scope to the panel's
    // own detail paragraph.
    const refusalDetail = page.locator("p").filter({ hasText: "The server said (" });
    await expect(refusalDetail).toContainText("invalid_scene, HTTP 422");
    await expect(refusalDetail).toContainText(
      "No wall carries the meter within the observed evidence.",
    );
    await expect(refusalDetail).toContainText("/meter/wall_id");
    await screenshot(page, "04-refusal-422.png");
  });

  test("records a mismatched answer as a witness, never as the answer", async ({ page }) => {
    await prepare(page, { kind: "mismatched_sha" });
    await page.getByRole("button", { name: "Run placement" }).click();

    await expect(page.getByRole("status")).toContainText(
      "the answer names different bytes; recorded as a witness, not displayed",
    );
    await expect(page.getByRole("heading", { name: "Mismatched answer" })).toBeVisible();
    // The witness detail also appears in the session history, so scope to the panel's
    // own detail paragraph.
    await expect(page.locator("p").filter({ hasText: "The request sent" })).toContainText(
      "the answer claims",
    );
    // Nothing is displayed as the answer — the word "Decision" cannot appear.
    await expect(page.getByText("Decision:")).toHaveCount(0);
    await screenshot(page, "05-mismatch-witness.png");
  });

  test("the inspector shows the current bound result plus history and witnesses", async ({
    page,
  }) => {
    await prepare(page, REFUSED_422);
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("refused by the server");

    await setBehavior({ kind: "bound" });
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("bound, decision pass");

    await expect(page.getByRole("heading", { name: "Placement inspector" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Current result" })).toBeVisible();
    await expect(page.getByText("the decision word is the result's own claim")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Attempt history" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Witnesses" })).toBeVisible();
    await expect(page.getByText("refusal (attempt")).toBeVisible();
    const historyEntries = page.locator("ol > li");
    await expect(historyEntries).toHaveCount(2);
    await screenshot(page, "06-inspector-open.png");
  });

  test("reopens the persisted session with history intact and no token", async ({ page }) => {
    const sha256 = await prepare(page);
    await page.fill("#server-token", SYNTHETIC_TOKEN);
    await expect(page.getByText("A token is set.")).toBeVisible();

    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("bound, decision pass");

    // The token is in memory only: the browser-local record must not contain it.
    const stored = await page.evaluate((key) => localStorage.getItem(key) ?? "", STORAGE_KEY);
    expect(stored).toContain("scene.json");
    expect(stored).not.toContain(SYNTHETIC_TOKEN);
    expect(stored.toLowerCase()).not.toContain("authorization");

    await page.reload();
    await expect(page.getByText("A saved session exists:")).toBeVisible();
    await expect(page.getByText("1 attempt", { exact: false })).toBeVisible();
    await screenshot(page, "07-reopen-offer.png");

    await page.getByRole("button", { name: "Reopen saved session" }).click();
    await expect(page.getByRole("status")).toContainText("bound, decision pass");
    // The reopened session keeps the server it was talking to — derived from its own
    // receipts — instead of falling back to the built-in default base URL.
    await expect(page.locator("#server-base-url")).toHaveValue(LOOPBACK_ORIGIN);
    await expect(page.getByRole("heading", { name: "Attempt history" })).toBeVisible();
    await expect(page.locator("ol > li")).toHaveCount(1);
    // Reopening never restores a token: the memory-only notice is back, the field is empty.
    await expect(page.getByText("A token is set.")).toHaveCount(0);
    await expect(page.getByText("Optional: only servers that loaded private rules")).toBeVisible();
    await screenshot(page, "08-reopened-history.png");

    // The reopened session can run a fresh attempt immediately.
    await setBehavior({ kind: "bound" });
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("bound, decision pass");
    await expect(page.getByText(`Bound to upload`).first()).toBeVisible();
    await expect(page.locator("code").filter({ hasText: sha256 }).first()).toBeVisible();
  });

  test("exports the redacted record and re-imports it into a fresh browser", async ({ page }) => {
    await prepare(page, REFUSED_422);
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("refused by the server");

    await setBehavior({ kind: "bound" });
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("bound, decision pass");

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Export session (redacted JSON)" }).click(),
    ]);
    const exportPath = path.join(os.tmpdir(), `basescanning-export-${Date.now()}.json`);
    await download.saveAs(exportPath);
    const raw = await fs.readFile(exportPath, "utf8");
    const parsed = JSON.parse(raw) as {
      format: string;
      version: number;
      savedAt: number;
      scene: { fileName: string; text: string };
      session: { attempts: unknown[] };
    };
    expect(parsed.format).toBe("basescanning-session-record");
    expect(parsed.version).toBe(1);
    expect(parsed.savedAt).toBeGreaterThan(0);
    expect(parsed.scene.fileName).toBe("scene.json");
    expect(parsed.scene.text).toBe(sceneText());
    expect(parsed.session.attempts).toHaveLength(2);
    expect(raw).not.toContain(SYNTHETIC_TOKEN);
    expect(raw.toLowerCase()).not.toContain("authorization");

    // A brand-new browser context with empty storage restores the file standalone.
    const browser = page.context().browser();
    if (browser === null) {
      throw new Error("the page has no browser handle");
    }
    const context = await browser.newContext();
    const page2 = await context.newPage();
    const offLoopback: string[] = [];
    await page2.route("**/*", (route) => {
      const url = new URL(route.request().url());
      if (["127.0.0.1", "localhost", "::1", "[::1]"].includes(url.hostname)) {
        return route.continue();
      }
      offLoopback.push(url.href);
      return route.abort("blocked");
    });
    await page2.goto("/");
    await page2.setInputFiles("#session-import", exportPath);
    await expect(page2.getByRole("status")).toContainText("bound, decision pass");
    await expect(page2.getByText("Loaded scene.json")).toBeVisible();
    await expect(page2.getByRole("heading", { name: "Attempt history" })).toBeVisible();
    await expect(page2.locator("ol > li")).toHaveCount(2);
    await screenshot(page2, "09-imported-session.png");
    await context.close();
    expect(offLoopback).toEqual([]);
  });

  test("renders the whole journey at 390x844 with no horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await prepare(page);
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("bound, decision pass");

    const metrics = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      viewportWidth: window.innerWidth,
    }));
    expect(metrics.documentWidth).toBeLessThanOrEqual(390);
    expect(metrics.bodyWidth).toBeLessThanOrEqual(390);
    expect(metrics.viewportWidth).toBe(390);
    await screenshot(page, "10-mobile-390x844.png");
  });

  test("every control is operable from the keyboard alone", async ({ page }) => {
    await prepare(page);

    // prepare() fills the base-url field, which leaves keyboard focus there — the first
    // control of the page. Walk the tab order from that starting point and record each
    // stop. (Blurring first does not restart the walk from the top: the browser's
    // sequential-focus starting point stays at the blurred element.)
    const record = () =>
      page.evaluate(() => {
        const active = document.activeElement;
        if (active === null || active === document.body) return "body";
        if (active instanceof HTMLInputElement) return active.id || `input:${active.type}`;
        if (active instanceof HTMLButtonElement) return `button:${active.textContent ?? ""}`;
        return active.tagName.toLowerCase();
      });
    const walk: string[] = [await record()];
    for (let i = 0; i < 5; i += 1) {
      await page.keyboard.press("Tab");
      walk.push(await record());
    }
    expect(walk).toEqual([
      "server-base-url",
      "server-token",
      "scene-file",
      "button:Export session (redacted JSON)",
      "session-import",
      "button:Run placement",
    ]);

    // Enter activates the focused Run button: the whole flow runs without a mouse.
    await page.keyboard.press("Enter");
    await expect(page.getByRole("status")).toContainText("bound, decision pass");
    await screenshot(page, "11-keyboard-run-focus.png");
  });

  test.describe("reduced motion", () => {
    test.use({ reducedMotion: "reduce" });

    test("the flow completes with no animation to reduce", async ({ page }) => {
      await prepare(page);
      await page.getByRole("button", { name: "Run placement" }).click();
      await expect(page.getByRole("status")).toContainText("bound, decision pass");

      const motion = await page.evaluate(() => {
        const animatedRules: string[] = [];
        for (const sheet of document.styleSheets) {
          for (const rule of sheet.cssRules) {
            const text = rule.cssText.toLowerCase();
            if (text.includes("animation") || text.includes("transition")) {
              animatedRules.push(text);
            }
          }
        }
        return { runningAnimations: document.getAnimations().length, animatedRules };
      });
      // The app ships one small stylesheet that declares no animation or transition
      // rules, so honoring reduced motion is trivially true — recorded, not assumed.
      expect(motion.runningAnimations).toBe(0);
      expect(motion.animatedRules).toEqual([]);
      await screenshot(page, "12-reduced-motion-bound.png");
    });
  });
});
