// Drives the real inspection server in headless Chromium and records what was
// observed, one receipt per case. Every case gets a fresh browser context and
// a fresh server process started with the exact flags the receipt lists.
//
// Simulated cases replace navigator.clipboard or document.execCommand before
// the page's own script runs; their receipts say so. Native cases use the
// browser's real Clipboard API and count as copied only when a readback
// returns the command text.
//
// Usage (Playwright is not a dependency of this repository; point at an
// existing install):
//   PLAYWRIGHT_MODULE=/path/to/node_modules/playwright \
//   node tools/capture-evidence-pack/inspection/capture-browser-evidence.mjs \
//     --tree <repository or exported tree root> --out <empty directory> \
//     --label after|before [--bad-pack <zip>] [--cases a,b,c]

import { spawn, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, index, all) => {
    if (value.startsWith("--")) pairs.push([value.slice(2), all[index + 1]]);
    return pairs;
  }, []),
);
for (const required of ["tree", "out", "label"]) {
  if (!args[required]) throw new Error(`--${required} is required`);
}
if (!process.env.PLAYWRIGHT_MODULE) throw new Error("set PLAYWRIGHT_MODULE to an installed playwright package directory");

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
const playwrightVersion = JSON.parse(fs.readFileSync(path.join(process.env.PLAYWRIGHT_MODULE, "package.json"), "utf8")).version;

const tree = path.resolve(args.tree);
const tool = path.join(tree, "tools/capture-evidence-pack");
const outDir = path.resolve(args.out);
if (fs.existsSync(outDir) && fs.readdirSync(outDir).length) throw new Error(`${outDir} must be empty: receipts are never overwritten`);
fs.mkdirSync(outDir, { recursive: true });

const sha256 = (data) => createHash("sha256").update(data).digest("hex");
const fileHash = (file) => (fs.existsSync(file) ? sha256(fs.readFileSync(file)) : null);
const rel = (file) => path.relative(tree, file);

function sourceIdentity() {
  const served = ["inspection/index.html", "inspection/serve.py", "read.py", "evidence/example.zip", "inspection/vendor/axe.min.js"];
  const identity = { tree_label: args.label, files: {} };
  for (const file of served) identity.files[`tools/capture-evidence-pack/${file}`] = fileHash(path.join(tool, file));
  identity.files["capture-browser-evidence.mjs (this harness)"] = fileHash(fileURLToPath(import.meta.url));
  try {
    identity.git_head = execFileSync("git", ["-C", tree, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    const status = execFileSync("git", ["-C", tree, "status", "--porcelain", "--", "tools/capture-evidence-pack"], { encoding: "utf8" });
    identity.git_dirty_paths = status.split("\n").filter(Boolean);
    identity.git_diff_sha256 = sha256(execFileSync("git", ["-C", tree, "diff", "HEAD", "--", "tools/capture-evidence-pack"]));
  } catch {
    identity.git_head = null;
    identity.note = "not a git checkout; identity rests on the file hashes above";
  }
  return identity;
}

function freePort() {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

async function startServer(flags) {
  const port = await freePort();
  const argv = ["tools/capture-evidence-pack/inspection/serve.py", "--port", String(port), ...flags];
  const child = spawn("python3", argv, { cwd: tree, stdio: ["ignore", "pipe", "pipe"] });
  let stderr = "";
  child.stderr.on("data", (chunk) => (stderr += chunk));
  const base = `http://127.0.0.1:${port}`;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`${base}/`);
      if (response.ok) return { child, base, argv: ["python3", ...argv] };
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  child.kill();
  throw new Error(`server did not start: ${stderr}`);
}

const readStates = () => ({
  data: document.body.getAttribute("data-read-data"),
  refusals: document.body.getAttribute("data-read-refusals"),
  verdict: document.getElementById("verdict") ? document.getElementById("verdict").dataset.state : null,
});

// Records each read-state change with a timestamp, from before the page's
// own script runs, so ordering is observed rather than inferred.
const timelineScript = () => {
  window.__timeline = [];
  const record = () => {
    const body = document.body;
    if (!body) return;
    const state = `${body.getAttribute("data-read-data")}|${body.getAttribute("data-read-refusals")}`;
    const last = window.__timeline[window.__timeline.length - 1];
    if (!last || last.state !== state) window.__timeline.push({ t: Math.round(performance.now()), state });
  };
  new MutationObserver(record).observe(document, { subtree: true, attributes: true, attributeFilter: ["data-read-data", "data-read-refusals"] });
  document.addEventListener("DOMContentLoaded", record);
};

async function settled(page, timeout = 15000) {
  await page.waitForFunction(() => {
    const reads = window.__inspectorReads;
    if (reads) return reads.data !== "pending" && reads.refusals !== "pending";
    return document.readyState === "complete";
  }, null, { timeout });
}

async function layout(page) {
  return page.evaluate(() => ({
    viewport: { width: window.innerWidth, height: window.innerHeight },
    document_scroll_width: document.documentElement.scrollWidth,
    document_client_width: document.documentElement.clientWidth,
    horizontal_overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    visible_sections: [...document.querySelectorAll("main > section, main > div")].filter((el) => !el.hidden).map((el) => el.id),
  }));
}

async function textOf(page, selector) {
  return page.evaluate((s) => (document.querySelector(s) ? document.querySelector(s).textContent.trim() : null), selector);
}

const receipts = [];
let browser;

function verdictFor(observed, expected) {
  return observed === expected ? "PASS" : "FAIL";
}

async function runCase(name, options, body) {
  const viewport = options.viewport || { width: 1440, height: 900 };
  const server = await startServer(options.flags || []);
  const contextOptions = { viewport, deviceScaleFactor: 1, ...(options.context || {}) };
  if (options.video) contextOptions.recordVideo = { dir: path.join(outDir, "video-tmp"), size: viewport };
  const context = await browser.newContext(contextOptions);
  if (options.permissions) await context.grantPermissions(options.permissions, { origin: server.base });
  await context.addInitScript(timelineScript);
  if (options.initScript) await context.addInitScript(options.initScript);
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (message) => message.type() === "error" && consoleErrors.push(message.text()));
  page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));
  const url = `${server.base}${options.query || "/"}`;
  const receipt = {
    case: name,
    kind: options.kind || "native",
    url_path: options.query || "/",
    server_argv: server.argv.map((part) => part.replace(tree + "/", "")),
    viewport,
    context_options: { reducedMotion: contextOptions.reducedMotion || null, permissions: options.permissions || [] },
    simulation: options.simulation || null,
    actions: [],
    observed: {},
    assertions: [],
  };
  const assert = (assertion, observed, expected, outcome = verdictFor(observed, expected)) =>
    receipt.assertions.push({ assertion, observed, expected, outcome });
  try {
    await page.goto(url);
    receipt.actions.push(`goto ${receipt.url_path}`);
    await body({ page, context, receipt, assert, server });
  } catch (error) {
    receipt.assertions.push({ assertion: "case ran to completion", observed: String(error), expected: "no error", outcome: "FAIL" });
  }
  receipt.observed.timeline = await page.evaluate(() => window.__timeline || null).catch(() => null);
  receipt.observed.console_errors = consoleErrors;
  const video = page.video();
  await context.close();
  if (video) {
    const target = path.join(outDir, `${name}.webm`);
    await video.saveAs(target);
    receipt.video = { path: path.basename(target), sha256: fileHash(target), bytes: fs.statSync(target).size };
    fs.rmSync(path.join(outDir, "video-tmp"), { recursive: true, force: true });
  }
  server.child.kill();
  receipts.push(receipt);
  const failed = receipt.assertions.filter((a) => a.outcome !== "PASS");
  console.log(`${name}: ${receipt.assertions.length - failed.length}/${receipt.assertions.length} PASS${failed.length ? " | " + failed.map((a) => `${a.outcome} ${a.assertion}`).join("; ") : ""}`);
}

async function shot(page, receipt, name, fullPage = false) {
  const file = path.join(outDir, `${name}.png`);
  await page.screenshot({ path: file, fullPage });
  receipt.screenshots = receipt.screenshots || [];
  receipt.screenshots.push({ path: path.basename(file), sha256: fileHash(file), full_page: fullPage });
}

async function tabTo(page, selector, limit = 60) {
  const stops = [];
  for (let press = 1; press <= limit; press += 1) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate((s) => {
      const el = document.activeElement;
      const style = getComputedStyle(el);
      return {
        element: el.id ? `#${el.id}` : el.tagName.toLowerCase() + (el.className ? `.${el.className}` : ""),
        focus_visible: el.matches(":focus-visible"),
        outline: `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`,
        is_target: el.matches(s),
      };
    }, selector);
    stops.push(stop);
    if (stop.is_target) return { reached: true, presses: press, stops };
  }
  return { reached: false, presses: limit, stops };
}

const COMMAND_SELECTOR = "#repro-command";

async function copyOutcome(page) {
  await page.waitForFunction(() => document.getElementById("copy-feedback").textContent.trim() !== "", null, { timeout: 5000 }).catch(() => {});
  return page.evaluate(() => {
    const feedback = document.getElementById("copy-feedback");
    return { feedback: feedback.textContent.trim(), copy_path: feedback.dataset.copyPath || null };
  });
}

async function readback(page) {
  return page.evaluate(async () => {
    try {
      return { ok: true, text: await navigator.clipboard.readText() };
    } catch (error) {
      return { ok: false, error: `${error.name}: ${error.message}` };
    }
  });
}

// Stubs. Each runs before the page script in a fresh context.
const stubs = {
  reject_exec_true: () => {
    window.__execCalls = [];
    Object.defineProperty(Navigator.prototype, "clipboard", { configurable: true, get: () => ({ writeText: () => Promise.reject(new DOMException("denied by stub", "NotAllowedError")) }) });
    document.execCommand = (command) => { window.__execCalls.push({ command, selected: document.activeElement && document.activeElement.value }); return true; };
  },
  reject_exec_false: () => {
    window.__execCalls = [];
    Object.defineProperty(Navigator.prototype, "clipboard", { configurable: true, get: () => ({ writeText: () => Promise.reject(new DOMException("denied by stub", "NotAllowedError")) }) });
    document.execCommand = (command) => { window.__execCalls.push({ command, selected: document.activeElement && document.activeElement.value }); return false; };
  },
  throw_exec_true: () => {
    window.__execCalls = [];
    Object.defineProperty(Navigator.prototype, "clipboard", { configurable: true, get: () => ({ writeText: () => { throw new DOMException("thrown synchronously by stub", "NotAllowedError"); } }) });
    document.execCommand = (command) => { window.__execCalls.push({ command, selected: document.activeElement && document.activeElement.value }); return true; };
  },
  absent_exec_true: () => {
    window.__execCalls = [];
    Object.defineProperty(Navigator.prototype, "clipboard", { configurable: true, get: () => undefined });
    document.execCommand = (command) => { window.__execCalls.push({ command, selected: document.activeElement && document.activeElement.value }); return true; };
  },
  reject_exec_throws: () => {
    window.__execCalls = [];
    Object.defineProperty(Navigator.prototype, "clipboard", { configurable: true, get: () => ({ writeText: () => Promise.reject(new DOMException("denied by stub", "NotAllowedError")) }) });
    document.execCommand = (command) => { window.__execCalls.push({ command }); throw new Error("execCommand thrown by stub"); };
  },
  resolve: () => {
    window.__execCalls = [];
    window.__writes = [];
    Object.defineProperty(Navigator.prototype, "clipboard", { configurable: true, get: () => ({ writeText: (text) => { window.__writes.push(text); return Promise.resolve(); } }) });
    document.execCommand = (command) => { window.__execCalls.push({ command }); return true; };
  },
};

const SIMULATED = [
  ["sim-reject-exec-true", "reject_exec_true", "writeText rejects (Promise.reject NotAllowedError); execCommand returns true", { feedback: "Copied.", path: "exec-command", exec: 1 }],
  ["sim-reject-exec-false", "reject_exec_false", "writeText rejects; execCommand returns false", { feedback: "Copy failed; select the command text instead.", path: "failed", exec: 1 }],
  ["sim-throw-exec-true", "throw_exec_true", "writeText throws synchronously; execCommand returns true", { feedback: "Copied.", path: "exec-command", exec: 1 }],
  ["sim-absent-exec-true", "absent_exec_true", "navigator.clipboard is undefined; execCommand returns true", { feedback: "Copied.", path: "exec-command", exec: 1 }],
  ["sim-reject-exec-throws", "reject_exec_throws", "writeText rejects; execCommand throws", { feedback: "Copy failed; select the command text instead.", path: "failed", exec: 1 }],
  ["sim-resolve", "resolve", "writeText resolves; execCommand must not be called", { feedback: "Copied.", path: "clipboard-api", exec: 0 }],
];

const MOBILE = { width: 390, height: 844 };
const cases = {};

cases["desktop-verified"] = () => runCase("desktop-verified", {}, async ({ page, receipt, assert }) => {
  await settled(page);
  receipt.observed.read_states = await page.evaluate(readStates);
  receipt.observed.layout = await layout(page);
  receipt.observed.verdict_text = await textOf(page, "#verdict");
  assert("data read state", receipt.observed.read_states.data, "verified");
  assert("refusal read state", receipt.observed.read_states.refusals, "ok");
  assert("no horizontal page overflow", receipt.observed.layout.horizontal_overflow, false);
  await shot(page, receipt, "desktop-verified");
  await shot(page, receipt, "desktop-verified-full", true);
});

cases["mobile-verified"] = () => runCase("mobile-verified", { viewport: MOBILE }, async ({ page, receipt, assert }) => {
  await settled(page);
  receipt.observed.read_states = await page.evaluate(readStates);
  receipt.observed.layout = await layout(page);
  assert("data read state", receipt.observed.read_states.data, "verified");
  assert("no horizontal page overflow at 390px", receipt.observed.layout.horizontal_overflow, false);
  await shot(page, receipt, "mobile-verified");
});

cases["desktop-refused-pack"] = () => {
  if (!args["bad-pack"]) throw new Error("--bad-pack is required for desktop-refused-pack");
  const flags = args.label === "before" ? [] : ["--pack", path.resolve(args["bad-pack"])];
  return runCase("desktop-refused-pack", { flags, kind: "native", simulation: args.label === "before" ? "the bad pack was copied over evidence/example.zip in the exported tree, because this tree's server has no --pack flag" : null }, async ({ page, receipt, assert }) => {
    await settled(page);
    receipt.observed.bad_pack_sha256 = fileHash(path.resolve(args["bad-pack"]));
    receipt.observed.read_states = await page.evaluate(readStates);
    receipt.observed.identity_section_visible = await page.evaluate(() => !document.getElementById("identity").hidden);
    receipt.observed.identity_text = await textOf(page, "#identity");
    receipt.observed.refused_text = await textOf(page, "#refused");
    assert("identity section stays hidden for a refused pack", receipt.observed.identity_section_visible, false);
    assert("data read state", receipt.observed.read_states.data, "refused");
    await shot(page, receipt, "desktop-refused-pack");
  });
};

for (const [fault, expectData, expectRefusals] of [
  ["data", "failed", "ok"],
  ["refusals", "verified", "failed"],
  ["both", "failed", "failed"],
  ["data-malformed", "failed", "ok"],
  ["refusals-malformed", "verified", "failed"],
  ["data-incomplete", "failed", "ok"],
  ["refusals-incomplete", "verified", "failed"],
]) {
  cases[`fault-${fault}`] = () => runCase(`fault-${fault}`, { flags: ["--fault", fault] }, async ({ page, receipt, assert }) => {
    await settled(page);
    receipt.observed.read_states = await page.evaluate(readStates);
    receipt.observed.error_detail = await textOf(page, "#error-detail");
    receipt.observed.refusals_text = await textOf(page, "#refusals-list");
    receipt.observed.identity_section_visible = await page.evaluate(() => !document.getElementById("identity").hidden);
    assert("data read state", receipt.observed.read_states.data, expectData);
    assert("refusal read state", receipt.observed.read_states.refusals, expectRefusals);
    assert("identity shown only when verified", receipt.observed.identity_section_visible, expectData === "verified");
    await shot(page, receipt, `fault-${fault}`);
  });
}

for (const [flag, slow, fast] of [["--delay-data", "data", "refusals"], ["--delay-refusals", "refusals", "data"]]) {
  const name = `delay-${slow}`;
  cases[name] = () => runCase(name, { flags: [flag, "2"] }, async ({ page, receipt, assert }) => {
    await page.waitForFunction((f) => window.__inspectorReads && window.__inspectorReads[f] !== "pending", fast, { timeout: 10000 });
    receipt.observed.while_pending = await page.evaluate(readStates);
    receipt.observed.verdict_text_while_pending = await textOf(page, "#verdict-status");
    receipt.observed.refusals_text_while_pending = await textOf(page, "#refusals-list");
    assert(`${slow} still pending after ${fast} settled`, receipt.observed.while_pending[slow], "pending");
    await shot(page, receipt, `${name}-pending`);
    await settled(page);
    receipt.observed.settled = await page.evaluate(readStates);
    const timeline = await page.evaluate(() => window.__timeline);
    const firstSettled = timeline.find((entry) => !entry.state.split("|").every((s) => s === "pending"));
    receipt.observed.first_settled_state = firstSettled ? firstSettled.state : null;
    assert("final data read state", receipt.observed.settled.data, "verified");
    assert("final refusal read state", receipt.observed.settled.refusals, "ok");
    await shot(page, receipt, `${name}-settled`);
  });
}

for (const [name, viewport] of [["keyboard-desktop", null], ["keyboard-mobile", MOBILE]]) {
  cases[name] = () => runCase(name, { viewport: viewport || undefined }, async ({ page, receipt, assert }) => {
    await settled(page);
    const result = await tabTo(page, "#copy-command");
    receipt.actions.push(`press Tab until #copy-command is focused (${result.presses} presses)`);
    receipt.observed.tab_stops = result.stops;
    assert("copy button reachable with Tab", result.reached, true);
    const last = result.stops[result.stops.length - 1];
    assert("copy button shows :focus-visible", last.focus_visible, true);
    assert("copy button outline", last.outline.startsWith("solid 3px"), true);
    assert("first Tab stop is the skip link", result.stops[0].element, "a.skip-link");
    await shot(page, receipt, name);
  });
}

async function nativeCopy(name, permissions, trigger) {
  return runCase(name, { permissions }, async ({ page, receipt, assert }) => {
    await settled(page);
    const command = await textOf(page, COMMAND_SELECTOR);
    receipt.observed.command = command;
    if (permissions.length) {
      await page.evaluate(() => navigator.clipboard.writeText("sentinel-before-copy"));
      receipt.actions.push("clipboard.writeText('sentinel-before-copy') to reset the clipboard");
    }
    if (trigger === "click") {
      await page.click("#copy-command");
      receipt.actions.push("mouse click #copy-command");
    } else {
      const result = await tabTo(page, "#copy-command");
      receipt.actions.push(`press Tab until #copy-command is focused (${result.presses} presses)`);
      assert("copy button reachable with Tab", result.reached, true);
      await page.keyboard.press(trigger);
      receipt.actions.push(`press ${trigger}`);
    }
    receipt.observed.dispatch = "completed";
    receipt.observed.copy = await copyOutcome(page);
    receipt.observed.readback = await readback(page);
    assert("feedback shown", receipt.observed.copy.feedback, "Copied.");
    if (receipt.observed.readback.ok) {
      assert("clipboard readback equals the command", receipt.observed.readback.text, command);
      assert("copy path", receipt.observed.copy.copy_path, "clipboard-api");
    } else {
      receipt.assertions.push({
        assertion: "clipboard readback equals the command",
        observed: receipt.observed.readback.error,
        expected: command,
        outcome: "BLOCKED",
        note: "readback was refused, so the clipboard contents are unverified",
      });
    }
    await shot(page, receipt, name);
  });
}

cases["native-copy-click"] = () => nativeCopy("native-copy-click", ["clipboard-read", "clipboard-write"], "click");
cases["native-copy-enter"] = () => nativeCopy("native-copy-enter", ["clipboard-read", "clipboard-write"], "Enter");
cases["native-copy-space"] = () => nativeCopy("native-copy-space", ["clipboard-read", "clipboard-write"], " ");
cases["native-copy-no-permission"] = () => nativeCopy("native-copy-no-permission", [], "click");

for (const [name, stub, description, expected] of SIMULATED) {
  cases[name] = () => runCase(name, { kind: "simulated", simulation: description, initScript: stubs[stub] }, async ({ page, receipt, assert }) => {
    await settled(page);
    const command = await textOf(page, COMMAND_SELECTOR);
    await page.click("#copy-command");
    receipt.actions.push("mouse click #copy-command");
    receipt.observed.copy = await copyOutcome(page);
    receipt.observed.exec_calls = await page.evaluate(() => window.__execCalls);
    receipt.observed.writes = await page.evaluate(() => window.__writes || null);
    assert("feedback text", receipt.observed.copy.feedback, expected.feedback);
    assert("copy path", receipt.observed.copy.copy_path, expected.path);
    assert("execCommand calls", (receipt.observed.exec_calls || []).length, expected.exec);
    if (expected.exec) {
      assert("execCommand called with copy", receipt.observed.exec_calls[0] && receipt.observed.exec_calls[0].command, "copy");
      if (stub !== "reject_exec_throws") assert("fallback textarea held the command", receipt.observed.exec_calls[0] && receipt.observed.exec_calls[0].selected, command);
    } else {
      assert("writeText received the command", (receipt.observed.writes || [])[0], command);
    }
    await shot(page, receipt, name);
  });
}

async function contrastFor(page, selector) {
  return page.evaluate((s) => {
    const parse = (c) => (c.match(/[\d.]+/g) || []).map(Number);
    const lum = ([r, g, b]) => {
      const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const el = document.querySelector(s);
    if (!el) return null;
    const fg = parse(getComputedStyle(el).color);
    let node = el;
    let bg = null;
    while (node && node.nodeType === 1) {
      const c = parse(getComputedStyle(node).backgroundColor);
      if (c.length >= 3 && (c.length === 3 || c[3] > 0)) { bg = c; break; }
      node = node.parentElement;
    }
    bg = bg || [255, 255, 255];
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    return { foreground: `rgb(${fg.slice(0, 3).join(",")})`, background: `rgb(${bg.slice(0, 3).join(",")})`, ratio: Math.round(((a + 0.05) / (b + 0.05)) * 100) / 100, background_from: node ? node.tagName.toLowerCase() + (node.id ? `#${node.id}` : "") : "default white" };
  }, selector);
}

for (const [name, flags, viewport] of [
  ["axe-desktop-verified", [], null],
  ["axe-mobile-verified", [], MOBILE],
  ["axe-desktop-refused-pack", "bad-pack", null],
  ["axe-fault-data", ["--fault", "data"], null],
  ["axe-fault-refusals", ["--fault", "refusals"], null],
  ["axe-data-malformed", ["--fault", "data-malformed"], null],
]) {
  cases[name] = () => runCase(name, {
    query: "/?axe=1",
    viewport: viewport || undefined,
    flags: flags === "bad-pack" ? ["--pack", path.resolve(args["bad-pack"])] : flags,
  }, async ({ page, receipt, assert }) => {
    await page.waitForFunction(() => window.__axeResults || /axe failed/.test(document.getElementById("axe-output").textContent), null, { timeout: 30000 });
    const results = await page.evaluate(() => {
      const r = window.__axeResults;
      if (!r) return { error: document.getElementById("axe-output").textContent };
      const nodes = (list) => list.map((n) => ({ target: n.target, data: (n.any[0] && n.any[0].data) || null, message: (n.any[0] && n.any[0].message) || null }));
      return {
        axe_version: r.testEngine.version,
        run_after_read_states: { data: document.body.getAttribute("data-read-data"), refusals: document.body.getAttribute("data-read-refusals") },
        violations: r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: nodes(v.nodes) })),
        incomplete: r.incomplete.map((v) => ({ id: v.id, nodes: nodes(v.nodes) })),
        passes: r.passes.map((p) => p.id),
      };
    });
    receipt.observed.axe = results;
    if (results.incomplete) {
      for (const item of results.incomplete) {
        if (item.id !== "color-contrast") continue;
        for (const node of item.nodes) node.manual_contrast = await contrastFor(page, node.target[0]);
      }
    }
    assert("axe ran", Boolean(results.axe_version), true);
    assert("axe ran after both reads settled", results.run_after_read_states && results.run_after_read_states.data !== "pending" && results.run_after_read_states.refusals !== "pending", true);
    assert("axe violations", (results.violations || []).length, 0);
    const unresolved = (results.incomplete || []).flatMap((i) => i.nodes.filter((n) => !n.manual_contrast || n.manual_contrast.ratio < 4.5).map((n) => `${i.id} ${n.target}`));
    assert("every incomplete node has a manual contrast check at or above 4.5:1", unresolved, [], unresolved.length ? "FAIL" : "PASS");
  });
}

cases["reduced-motion"] = () => runCase("reduced-motion", { context: { reducedMotion: "reduce" } }, async ({ page, receipt, assert }) => {
  await settled(page);
  receipt.observed.button_transition = await page.evaluate(() => getComputedStyle(document.getElementById("copy-command")).transitionDuration);
  assert("button transition under prefers-reduced-motion: reduce", receipt.observed.button_transition, "0s");
});

cases["recording-copy"] = () => runCase("recording-copy", { video: true, viewport: { width: 1280, height: 800 }, permissions: ["clipboard-read", "clipboard-write"] }, async ({ page, receipt, assert }) => {
  await settled(page);
  await page.waitForTimeout(600);
  await page.evaluate(() => document.getElementById("reproduction").scrollIntoView({ block: "center" }));
  receipt.actions.push("scroll the reproduction section into view");
  await page.waitForTimeout(600);
  const result = await tabTo(page, "#copy-command");
  receipt.actions.push(`press Tab until #copy-command is focused (${result.presses} presses)`);
  await page.waitForTimeout(700);
  await page.keyboard.press("Enter");
  receipt.actions.push("press Enter");
  receipt.observed.copy = await copyOutcome(page);
  receipt.observed.readback = await readback(page);
  await page.waitForTimeout(1500);
  assert("feedback shown", receipt.observed.copy.feedback, "Copied.");
  assert("clipboard readback equals the command", receipt.observed.readback.ok ? receipt.observed.readback.text : receipt.observed.readback.error, await textOf(page, COMMAND_SELECTOR), receipt.observed.readback.ok ? undefined : "BLOCKED");
});

const selected = args.cases ? args.cases.split(",") : Object.keys(cases);
browser = await chromium.launch({ headless: true });
const identity = sourceIdentity();
const startedAt = new Date().toISOString();
for (const name of selected) {
  if (!cases[name]) throw new Error(`unknown case ${name}`);
  await cases[name]();
}
const browserVersion = browser.version();
await browser.close();

const doc = {
  harness: "tools/capture-evidence-pack/inspection/capture-browser-evidence.mjs",
  started_at: startedAt,
  browser: { engine: "chromium headless", version: browserVersion, playwright: playwrightVersion },
  source: identity,
  invocation: ["node", "capture-browser-evidence.mjs", ...process.argv.slice(2).map((part) => (part.startsWith("/") ? path.basename(part) : part))],
  receipts,
};
fs.writeFileSync(path.join(outDir, "receipts.json"), JSON.stringify(doc, null, 2) + "\n");
const totals = receipts.flatMap((r) => r.assertions).reduce((acc, a) => ((acc[a.outcome] = (acc[a.outcome] || 0) + 1), acc), {});
console.log(`assertions: ${JSON.stringify(totals)}`);
