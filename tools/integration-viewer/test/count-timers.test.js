import assert from "node:assert/strict";
import { test } from "node:test";
import { createCountTimers } from "../public/count-timers.js";

// Timers fire only when the test says so, in the order they were set.
function harness() {
  let state = { acknowledged: 0 };
  const shown = [];
  const queue = new Map();
  let id = 0;
  const timers = createCountTimers({
    write: (from) => shown.push(from.acknowledged),
    current: () => state,
    setTimer: (fn) => {
      id += 1;
      queue.set(id, fn);
      return id;
    },
    clearTimer: (t) => queue.delete(t),
  });
  return {
    timers,
    shown,
    arrive(acknowledged) {
      state = { acknowledged };
    },
    fire(t) {
      const fn = queue.get(t);
      queue.delete(t);
      fn();
    },
    get pending() {
      return [...queue.keys()];
    },
  };
}

test("a batch that drew no marks while older marks travelled reaches the counters when they land", () => {
  const h = harness();
  h.arrive(5);
  h.timers.afterMarks(1100);
  h.timers.settle();
  // Motion is paused (or the mark cap is hit) for the next batch, so it schedules nothing.
  h.arrive(8);
  h.timers.settle();
  assert.deepEqual(h.shown, [], "counters moved while marks were still travelling");
  h.fire(h.pending[0]);
  assert.deepEqual(h.shown, [8]);
});

test("an earlier timer shows only its own batch while a later batch's marks still travel", () => {
  const h = harness();
  h.arrive(5);
  h.timers.afterMarks(1100);
  h.arrive(8);
  h.timers.afterMarks(1100);
  const [first, second] = h.pending;
  h.fire(first);
  assert.deepEqual(h.shown, [5]);
  h.arrive(9); // drew no marks
  h.timers.settle();
  assert.deepEqual(h.shown, [5]);
  h.fire(second);
  assert.deepEqual(h.shown, [5, 9]);
});

test("with nothing travelling, the counters update at once", () => {
  const h = harness();
  h.arrive(3);
  h.timers.settle();
  assert.deepEqual(h.shown, [3]);
});

test("clear drops pending updates", () => {
  const h = harness();
  h.arrive(5);
  h.timers.afterMarks(1100);
  h.timers.clear();
  assert.deepEqual(h.pending, []);
  h.arrive(0);
  h.timers.settle();
  assert.deepEqual(h.shown, [0]);
});
