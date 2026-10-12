// Keyboard focus around the copy button, in headless Chromium against the real
// inspection server. Each case starts a fresh server and browser context,
// presses Tab until the copy button is focused, presses Enter, then Tab once
// more, and records where focus is at each step.
//
// Simulated cases replace navigator.clipboard and document.execCommand before
// the page script runs; their receipts say so and record the stub's calls.
// The native case uses Chromium's own Clipboard API with a permission grant
// and counts as copied only when readText returns the command.
//
// Usage: PLAYWRIGHT_MODULE=/path/to/node_modules/playwright \
//   node tools/capture-evidence-pack/inspection/capture-copy-focus-evidence.mjs \
//     --tree <repository or exported tree root> --out <new directory> [--video]

import { spawn } from "node:child_process";
import { once } from "node:events";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, index, all) => {
    if (value.startsWith("--")) pairs.push([value.slice(2), all[index + 1] && !all[index + 1].startsWith("--") ? all[index + 1] : true]);
    return pairs;
  }, []),
);
if (!args.tree || !args.out) throw new Error("--tree and --out are required");
if (!process.env.PLAYWRIGHT_MODULE) throw new Error("set PLAYWRIGHT_MODULE to an installed playwright package directory");
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE);
const playwrightVersion = JSON.parse(fs.readFileSync(path.join(process.env.PLAYWRIGHT_MODULE, "package.json"), "utf8")).version;

const tree = path.resolve(args.tree);
const outDir = path.resolve(args.out);
if (fs.existsSync(outDir)) throw new Error(`${outDir} already exists: receipts are never overwritten`);
fs.mkdirSync(outDir, { recursive: true });

const sha256 = (data) => createHash("sha256").update(data).digest("hex");
const served = {};
for (const file of ["inspection/index.html", "inspection/serve.py", "read.py", "evidence/example.zip"]) {
  served[`tools/capture-evidence-pack/${file}`] = sha256(fs.readFileSync(path.join(tree, "tools/capture-evidence-pack", file)));
}
served["capture-copy-focus-evidence.mjs (this driver)"] = sha256(fs.readFileSync(fileURLToPath(import.meta.url)));

const STUBS = {
  "reject-exec-false": { description: "writeText rejects; execCommand returns false", writeText: "reject", exec: "false" },
  "reject-exec-true": { description: "writeText rejects; execCommand returns true", writeText: "reject", exec: "true" },
  "throw-exec-true": { description: "writeText throws synchronously; execCommand returns true", writeText: "throw", exec: "true" },
  "absent-exec-false": { description: "navigator.clipboard is undefined; execCommand returns false", writeText: "absent", exec: "false" },
  "reject-exec-throws": { description: "writeText rejects; execCommand throws", writeText: "reject", exec: "throw" },
  "resolve": { description: "writeText resolves; the fallback must not run", writeText: "resolve", exec: "true" },
  "slow-reject-user-moves": { description: "writeText rejects after 600 ms; the user presses Tab before that; execCommand returns false", writeText: "slow", exec: "false" },
};

function installStub(stub) {
  window.__calls = [];
  const writeText = {
    reject: () => Promise.reject(new DOMException("simulated denial", "NotAllowedError")),
    slow: () => new Promise((_, fail) => setTimeout(() => fail(new DOMException("simulated denial", "NotAllowedError")), 600)),
    throw: () => { throw new DOMException("simulated synchronous throw", "NotAllowedError"); },
    resolve: () => Promise.resolve(),
  }[stub.writeText];
  Object.defineProperty(Navigator.prototype, "clipboard", {
    configurable: true,
    get: () => (stub.writeText === "absent" ? undefined : { writeText: (text) => { window.__calls.push({ name: "writeText", text }); return writeText(); } }),
  });
  document.execCommand = (name) => {
    window.__calls.push({ name: "execCommand", command: name, focused: document.activeElement && document.activeElement.tagName });
    if (stub.exec === "throw") throw new Error("simulated execCommand failure");
    return stub.exec === "true";
  };
}

async function startServer() {
  const child = spawn("python3", ["tools/capture-evidence-pack/inspection/serve.py", "--port", "0"], {
    cwd: tree,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  child.stdout.on("data", (chunk) => (stdout += chunk));
  const started = Date.now();
  while (!/http:\/\/127\.0\.0\.1:[1-9]\d*/.test(stdout)) {
    if (Date.now() - started > 10000 || child.exitCode !== null) throw new Error("server did not start");
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  return { child, base: stdout.match(/http:\/\/127\.0\.0\.1:\d+/)[0] };
}

async function stopServer(server) {
  if (server.child.exitCode === null && server.child.signalCode === null) {
    const exited = once(server.child, "exit");
    server.child.kill("SIGTERM");
    await exited;
  }
  return { exit: server.child.exitCode, signal: server.child.signalCode };
}

const focused = () => {
  const el = document.activeElement;
  return el ? el.id || el.tagName : null;
};

async function tabToCopy(page) {
  for (let presses = 1; presses <= 40; presses += 1) {
    await page.keyboard.press("Tab");
    if ((await page.evaluate(focused)) === "copy-command") return presses;
  }
  throw new Error("Tab never reached #copy-command");
}

const receipts = [];
const browser = await chromium.launch({ headless: true });
const browserVersion = browser.version();
const viewports = [{ width: 1440, height: 900 }, { width: 390, height: 844 }];
const caseNames = [...Object.keys(STUBS), "native-granted"];

try {
  for (const viewport of viewports) {
    for (const name of caseNames) {
      const native = name === "native-granted";
      const server = await startServer();
      const recordVideo = args.video && name === "reject-exec-false" && viewport.width === 1440;
      const context = await browser.newContext({
        viewport,
        deviceScaleFactor: 1,
        reducedMotion: "reduce",
        ...(recordVideo ? { recordVideo: { dir: path.join(outDir, "video-tmp"), size: viewport } } : {}),
      });
      const receipt = {
        case: name,
        kind: native ? "native" : "simulated",
        simulation: native ? null : STUBS[name].description,
        viewport,
        server_argv: ["python3", "tools/capture-evidence-pack/inspection/serve.py", "--port", "0"],
        assertions: [],
      };
      const check = (assertion, observed, expected) =>
        receipt.assertions.push({ assertion, observed, expected, outcome: JSON.stringify(observed) === JSON.stringify(expected) ? "PASS" : "FAIL" });
      try {
        if (native) await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: server.base });
        else await context.addInitScript(installStub, STUBS[name]);
        const page = await context.newPage();
        await page.goto(server.base + "/");
        await page.waitForFunction(() => window.__inspectorReads && window.__inspectorReads.data === "verified" && window.__inspectorReads.refusals === "ok");
        const command = await page.evaluate(() => document.getElementById("repro-command").textContent);
        if (native) await page.evaluate(() => navigator.clipboard.writeText("sentinel-before-copy"));
        if (recordVideo) await page.waitForTimeout(500);
        receipt.tab_presses_to_copy = await tabToCopy(page);
        const box = await page.evaluate(() => {
          const rect = document.getElementById("copy-command").getBoundingClientRect();
          return { height: rect.height, width: rect.width };
        });
        receipt.copy_button = box;
        check("copy button is at least 44 CSS px tall", box.height >= 44, true);
        if (recordVideo) await page.waitForTimeout(600);
        await page.keyboard.press("Enter");
        if (name === "slow-reject-user-moves") {
          await page.keyboard.press("Tab");
          receipt.focus_after_user_tab = await page.evaluate(focused);
        }
        await page.waitForFunction(() => document.getElementById("copy-feedback").textContent !== "", null, { timeout: 5000 });
        if (recordVideo) await page.waitForTimeout(900);
        receipt.after_enter = await page.evaluate(() => ({
          focus: document.activeElement ? document.activeElement.id || document.activeElement.tagName : null,
          focus_visible: document.activeElement ? document.activeElement.matches(":focus-visible") : false,
          outline: (() => { const s = getComputedStyle(document.activeElement); return `${s.outlineStyle} ${s.outlineWidth}`; })(),
          feedback: document.getElementById("copy-feedback").textContent,
          copy_path: document.getElementById("copy-feedback").dataset.copyPath || null,
          textareas_left: document.querySelectorAll("textarea").length,
          calls: window.__calls || null,
          horizontal_overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        }));
        const expectedFocus = name === "slow-reject-user-moves" ? "report-text" : "copy-command";
        check("focus after the copy settles", receipt.after_enter.focus, expectedFocus);
        check("no temporary textarea left", receipt.after_enter.textareas_left, 0);
        check("no horizontal page overflow", receipt.after_enter.horizontal_overflow, false);
        if (expectedFocus === "copy-command") {
          check("restored focus shows the focus ring", [receipt.after_enter.focus_visible, receipt.after_enter.outline], [true, "solid 3px"]);
        }
        if (native) {
          receipt.readback = await page.evaluate(async () => {
            try { return { ok: true, text: await navigator.clipboard.readText() }; } catch (error) { return { ok: false, error: `${error.name}: ${error.message}` }; }
          });
          check("native clipboard readback equals the command", receipt.readback.ok ? receipt.readback.text : receipt.readback.error, command);
          check("feedback", [receipt.after_enter.feedback, receipt.after_enter.copy_path], ["Copied.", "clipboard-api"]);
        }
        const shot = path.join(outDir, `${name}-${viewport.width}.png`);
        await page.screenshot({ path: shot });
        receipt.screenshot = { path: path.basename(shot), sha256: sha256(fs.readFileSync(shot)) };
        await page.keyboard.press("Tab");
        receipt.next_tab = await page.evaluate(focused);
        if (expectedFocus === "copy-command") check("next Tab continues to the report text, not the skip link", receipt.next_tab, "report-text");
        if (recordVideo) await page.waitForTimeout(900);
        const video = page.video();
        await context.close();
        if (video) {
          const target = path.join(outDir, `keyboard-denial-${viewport.width}.webm`);
          await video.saveAs(target);
          receipt.video = { path: path.basename(target), sha256: sha256(fs.readFileSync(target)) };
          fs.rmSync(path.join(outDir, "video-tmp"), { recursive: true, force: true });
        }
      } catch (error) {
        receipt.assertions.push({ assertion: "case ran to completion", observed: String(error), expected: "no error", outcome: "FAIL" });
        await context.close().catch(() => {});
      } finally {
        receipt.server_stopped = await stopServer(server);
      }
      receipts.push(receipt);
      const failed = receipt.assertions.filter((a) => a.outcome !== "PASS");
      console.log(`${viewport.width} ${name}: ${receipt.assertions.length - failed.length}/${receipt.assertions.length} PASS${failed.length ? " | " + failed.map((a) => a.assertion).join("; ") : ""}`);
    }
  }
} finally {
  await browser.close();
}

const totals = receipts.flatMap((r) => r.assertions).reduce((acc, a) => ((acc[a.outcome] = (acc[a.outcome] || 0) + 1), acc), {});
fs.writeFileSync(
  path.join(outDir, "receipts.json"),
  JSON.stringify({ browser: { engine: "chromium headless", version: browserVersion, playwright: playwrightVersion }, served_sha256: served, totals, receipts }, null, 2) + "\n",
);
console.log(`assertions: ${JSON.stringify(totals)}`);
