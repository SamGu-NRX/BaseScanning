// Decides when the acknowledged-file counters change. Each render hands over the state it drew
// and when that render's marks land. A render's totals show once its own marks and the marks of
// every earlier render have landed, so the counters never run ahead of a mark still on the line,
// even when a later, shorter batch lands first. Totals reach the counters only from a render: the
// reducer's state can already hold an arrival whose marks have not started yet.
// Timers do the work, never an animation finishing, because an animation never finishes where
// the animation clock is stopped.

/**
 * @template S
 * @param {{ write: (from: S) => void, setTimer?: typeof setTimeout, clearTimer?: typeof clearTimeout }} options
 *   `write` shows the totals in a state.
 */
export function createCountTimers({ write, setTimer = setTimeout, clearTimer = clearTimeout }) {
  /** Renders not shown yet, oldest first: { from, landed, timer }. */
  const queue = [];

  function release() {
    let latest = null;
    while (queue.length > 0 && queue[0].landed) latest = queue.shift();
    if (latest) write(latest.from);
  }

  return {
    /**
     * Records the state a render drew. `landsIn` is the milliseconds until its last mark lands,
     * or 0 when it sent no marks (motion paused, clock stopped, mark cap reached, nothing new).
     */
    rendered(from, landsIn) {
      const tail = queue.at(-1);
      if (landsIn > 0) {
        const entry = { from, landed: false, timer: null };
        entry.timer = setTimer(() => {
          entry.landed = true;
          release();
        }, landsIn);
        queue.push(entry);
      } else if (tail?.landed) {
        // Waiting behind the same marks as the previous render, so only the newest totals matter.
        tail.from = from;
      } else {
        queue.push({ from, landed: true, timer: null });
      }
      release();
    },
    /** Drops pending updates, for a capture switch. */
    clear() {
      for (const entry of queue) if (!entry.landed) clearTimer(entry.timer);
      queue.length = 0;
    },
  };
}
