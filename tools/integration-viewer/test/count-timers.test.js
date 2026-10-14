import assert from "node:assert/strict";
import { test } from "node:test";
import { createCountTimers } from "../public/count-timers.js";

// The page's side of the counters, on a virtual clock. `dispatch` changes the reducer's state
// at once, as app.js does; `render` is the queued frame that draws it and starts its marks.
// Timers fire at their due time when the clock passes it.
function page() {
  let now = 0;
  let reducer = { acknowledged: 0 };
  let shown = 0;
  const timers = [];
  const counts = createCountTimers({
    write: (from) => {
      shown = from.acknowledged;
    },
    setTimer: (fn, ms) => {
      const t = { due: now + ms, fn };
      timers.push(t);
      return t;
    },
    clearTimer: (t) => timers.splice(timers.indexOf(t), 1),
  });
  return {
    counts,
    dispatch(acknowledged) {
      reducer = { acknowledged };
    },
    /** Draws the reducer's current state; `landsIn` is 0 when the render sends no marks. */
    render(landsIn = 0) {
      counts.rendered(reducer, landsIn);
    },
    at(t) {
      for (;;) {
        const next = timers.filter((x) => x.due <= t).sort((a, b) => a.due - b.due)[0];
        if (!next) break;
        timers.splice(timers.indexOf(next), 1);
        now = next.due;
        next.fn();
      }
      now = t;
    },
    get shown() {
      return shown;
    },
    get pending() {
      return timers.length;
    },
  };
}

test("an arrival the reducer holds but no render has drawn stays off the counters", () => {
  const p = page();
  p.dispatch(6);
  p.render(1450); // six marks, the last landing at 1450 ms
  p.at(1449);
  p.dispatch(7); // arrives; its frame is queued, so its mark has not started
  p.at(1450);
  assert.equal(p.shown, 6, "the counters counted a file whose mark had not started");
  p.at(1456);
  p.render(1100);
  p.at(2555);
  assert.equal(p.shown, 6);
  p.at(2556);
  assert.equal(p.shown, 7);
});

test("a later batch that lands first waits for the earlier batch's marks", () => {
  const p = page();
  p.dispatch(6);
  p.render(1450); // six marks from 0 ms, landing by 1450 ms
  p.at(70);
  p.dispatch(7);
  p.render(1100); // one mark from 70 ms, landing at 1170 ms
  p.at(1170);
  assert.equal(p.shown, 0, "the counters ran ahead of marks still travelling");
  p.at(1450);
  assert.equal(p.shown, 7);
});

test("a batch that drew no marks while older marks travelled shows when they land", () => {
  const p = page();
  p.dispatch(5);
  p.render(1100);
  p.at(300);
  p.dispatch(8);
  p.render(0); // motion paused, clock stopped or mark cap reached
  assert.equal(p.shown, 0);
  p.at(1100);
  assert.equal(p.shown, 8);
});

test("with nothing travelling, a render shows its totals at once", () => {
  const p = page();
  p.dispatch(3);
  p.render(0);
  assert.equal(p.shown, 3);
});

test("clear drops pending updates", () => {
  const p = page();
  p.dispatch(5);
  p.render(1100);
  p.counts.clear();
  assert.equal(p.pending, 0);
  p.at(2000);
  assert.equal(p.shown, 0);
  p.dispatch(2);
  p.render(0);
  assert.equal(p.shown, 2);
});
