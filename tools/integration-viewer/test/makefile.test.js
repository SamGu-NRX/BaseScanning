import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

function tree(t) {
  const root = mkdtempSync(join(tmpdir(), "viewer-make-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  writeFileSync(join(root, "Makefile"), readFileSync(new URL("../../../Makefile", import.meta.url)));
  const bin = join(root, "bin");
  mkdirSync(bin);
  const log = join(root, "commands");
  writeFileSync(log, "");
  for (const tool of ["uv", "node", "git", "pnpm", "xcodebuild"]) {
    writeFileSync(join(bin, tool), `#!/bin/sh\nprintf '%s %s\\n' '${tool}' "$*" >> "$LOG"\n[ "$FAIL_TOOL" != '${tool}' ]\n`, { mode: 0o755 });
  }
  const suite = (path, text) => {
    mkdirSync(join(root, path), { recursive: true });
    writeFileSync(join(root, path, "Makefile"), text);
  };
  const run = (target, failTool = "") => spawnSync("make", [target], { cwd: root, encoding: "utf8", env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, LOG: log, FAIL_TOOL: failTool } });
  return { root, suite, run, commands: () => readFileSync(log, "utf8") };
}

test("meter-closeup refuses a stale lock before running its checks", (t) => {
  const h = tree(t);
  h.suite("experiments/meter-closeup", 'check:\n\t@echo checks >> "$$LOG"\n');
  assert.notEqual(h.run("meter-closeup", "uv").status, 0);
  assert.equal(h.commands(), "uv sync --locked\n");
});

test("measure-lab detects project drift before its tests or build", (t) => {
  const h = tree(t);
  h.suite("experiments/measure-lab", 'project:\n\t@echo project >> "$$LOG"\ncheck:\n\t@echo checks >> "$$LOG"\n');
  assert.notEqual(h.run("measure-lab", "git").status, 0);
  assert.equal(h.commands(), "project\ngit diff --exit-code -- experiments/measure-lab/MeasureLab.xcodeproj\n");
});

test("make check includes the viewer only when its suite exists", (t) => {
  const h = tree(t);
  mkdirSync(join(h.root, "server"));
  mkdirSync(join(h.root, "web"));
  assert.equal(h.run("check").status, 0);
  assert.ok(!h.commands().includes("node"));
  h.suite("tools/integration-viewer", "");
  writeFileSync(join(h.root, "tools/integration-viewer/package.json"), "{}");
  assert.equal(h.run("check").status, 0);
  assert.ok(h.commands().includes("node --test test/*.test.js\n"));
  assert.notEqual(h.run("check", "node").status, 0);
});
