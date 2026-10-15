import assert from "node:assert/strict";
import { test } from "node:test";
import { Conduit, markGroups } from "../public/conduit.js";

// The rule markGroups must keep, written the way send() used to: list every file, take every step-th.
function expanded(counts, max) {
  const files = Object.entries(counts).flatMap(([group, n]) => Array(n).fill(group));
  const marks = Math.max(0, Math.min(files.length, max));
  const step = files.length / marks;
  return Array.from({ length: marks }, (_, i) => files[Math.floor(i * step)]);
}

test("marks match the evenly spaced picks from the full file list", () => {
  for (let photo = 0; photo <= 9; photo += 1)
    for (let depth = 0; depth <= 7; depth += 1)
      for (const other of [0, 1, 13])
        for (let max = 0; max <= 6; max += 1) {
          const counts = { photo, depth, mesh: 0, other };
          assert.deepEqual(markGroups(counts, max), expanded(counts, max), JSON.stringify({ counts, max }));
        }
});

test("a batch of billions picks its marks without listing the files", () => {
  // Array(5e9) throws at once (the limit is 2 ** 32 - 1), so expanding the batch fails fast here
  // instead of filling memory.
  assert.deepEqual(markGroups({ photo: 5e9, depth: 5e9, mesh: 0, other: 0 }, 6), ["photo", "photo", "photo", "depth", "depth", "depth"]);
  assert.deepEqual(markGroups({ photo: 5, depth: 1 }, -3), []);
});

function conduit() {
  globalThis.ResizeObserver ??= class {
    observe() {}
  };
  const chips = [];
  const c = new Conduit({}, [], { append: (chip) => chips.push(chip) });
  c.samples = Array.from({ length: 25 }, (_, i) => ({ x: i, y: 0 }));
  return { c, chips };
}

test("with motion off, send returns at once without reading the batch", () => {
  const { c, chips } = conduit();
  const arrival = {
    backlog: false,
    get groups() {
      throw new Error("send read the batch with motion off");
    },
  };
  assert.equal(c.send(arrival, { motion: false }), 0);
  assert.equal(chips.length, 0);
});

test("a huge batch sends at most six marks", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  globalThis.document ??= { createElement: () => ({ dataset: {}, animate() {}, remove() {} }) };
  const { c, chips } = conduit();
  const landsIn = c.send({ backlog: false, groups: { photo: 5e9, depth: 1, mesh: 0, other: 0 } }, { motion: true });
  assert.equal(chips.length, 6);
  assert.equal(landsIn, 1100 + 5 * 70);
  assert.deepEqual(
    chips.map((chip) => chip.dataset.group),
    Array(6).fill("photo"),
  );
  t.mock.timers.tick(landsIn + 50);
  assert.equal(c.inFlight, 0);
});
