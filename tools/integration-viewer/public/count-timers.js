// Decides when the acknowledged-file counters change. A batch that draws marks shows its totals
// when its last mark lands; a batch that draws none shows them at once, unless older marks are
// still travelling, in which case it waits for them. Timers do the work, never an animation
// finishing, because an animation never finishes where the animation clock is stopped.

/**
 * @template S
 * @param {{ write: (from: S) => void, current: () => S, setTimer?: typeof setTimeout, clearTimer?: typeof clearTimeout }} options
 *   `write` shows the totals in a state; `current` returns the latest state.
 */
export function createCountTimers({ write, current, setTimer = setTimeout, clearTimer = clearTimeout }) {
  const pending = new Set();
  return {
    /** Shows the current totals once marks that land in `landsIn` ms have arrived. */
    afterMarks(landsIn) {
      // Each timer shows the totals as of its own batch, so a counter never runs ahead of its marks.
      const snapshot = current();
      const timer = setTimer(() => {
        pending.delete(timer);
        // The last timer shows the latest totals: batches that drew no marks while it waited (motion
        // paused, clock stopped, too many marks in flight) skipped their own update. Every batch
        // that drew marks has its own timer, so with none pending, no mark is still on its way.
        write(pending.size === 0 ? current() : snapshot);
      }, landsIn);
      pending.add(timer);
    },
    /** Shows the current totals now, unless marks are still travelling. */
    settle() {
      if (pending.size === 0) write(current());
    },
    /** Drops pending updates, for a capture switch. */
    clear() {
      for (const timer of pending) clearTimer(timer);
      pending.clear();
    },
  };
}
