// axe-core scans over the main states of the placement page, in light mode and — for
// the record — under an emulated `prefers-color-scheme: dark`. The app ships one small
// stylesheet and declares no color scheme, so dark emulation leaves the rendering
// unchanged (a dark screenshot is committed to show that); the scans still run and are
// recorded per mode rather than skipped. The bar for every state and mode: zero
// serious or critical violations. Full JSON results are committed under evidence/axe/.

import { promises as fs } from "node:fs";
import { AxeBuilder } from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";
import { AXE_DIR, prepare, REFUSED_422, screenshot, setBehavior, test } from "./helpers.ts";

async function scanAndRecord(page: Page, stateName: string): Promise<void> {
  await fs.mkdir(AXE_DIR, { recursive: true });

  await page.emulateMedia({ colorScheme: "light" });
  const light = await new AxeBuilder({ page }).analyze();

  await page.emulateMedia({ colorScheme: "dark" });
  const dark = await new AxeBuilder({ page }).analyze();
  await page.emulateMedia({ colorScheme: "light" });

  await fs.writeFile(
    `${AXE_DIR}/${stateName}-light.json`,
    JSON.stringify({ state: stateName, colorScheme: "light", ...light }, null, 2),
  );
  await fs.writeFile(
    `${AXE_DIR}/${stateName}-dark.json`,
    JSON.stringify({ state: stateName, colorScheme: "dark", ...dark }, null, 2),
  );

  for (const [mode, results] of [
    ["light", light],
    ["dark", dark],
  ] as const) {
    const seriousOrCritical = results.violations.filter(
      (violation) => violation.impact === "serious" || violation.impact === "critical",
    );
    expect(seriousOrCritical, `${stateName} (${mode})`).toEqual([]);
  }
}

async function toBoundState(page: Page): Promise<void> {
  await prepare(page);
  await page.getByRole("button", { name: "Run placement" }).click();
  await expect(page.getByRole("status")).toContainText("bound, decision pass");
}

test.describe("axe scans of the main states", () => {
  test("empty state (no scene selected)", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("status")).toHaveText("No scene selected.");
    await screenshot(page, "13-axe-empty.png");
    await scanAndRecord(page, "empty");
  });

  test("bound result", async ({ page }) => {
    await toBoundState(page);
    await screenshot(page, "14-axe-bound.png");
    await scanAndRecord(page, "bound");
  });

  test("refusal (422 with the server's message and path)", async ({ page }) => {
    await prepare(page, REFUSED_422);
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("refused by the server");
    await screenshot(page, "15-axe-refusal.png");
    await scanAndRecord(page, "refusal");
  });

  test("inspector open (bound result with history and witnesses)", async ({ page }) => {
    await prepare(page, REFUSED_422);
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("refused by the server");
    await setBehavior({ kind: "bound" });
    await page.getByRole("button", { name: "Run placement" }).click();
    await expect(page.getByRole("status")).toContainText("bound, decision pass");
    await expect(page.getByRole("heading", { name: "Witnesses" })).toBeVisible();
    await screenshot(page, "16-axe-inspector.png");
    await scanAndRecord(page, "inspector");
  });

  test("dark mode changes nothing (one stylesheet, no color scheme declared)", async ({ page }) => {
    await toBoundState(page);
    await page.emulateMedia({ colorScheme: "dark" });
    await screenshot(page, "17-dark-mode-bound.png");
    await page.emulateMedia({ colorScheme: "light" });
  });
});
