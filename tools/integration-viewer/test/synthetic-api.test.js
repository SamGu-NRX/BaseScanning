import assert from "node:assert/strict";
import { test } from "node:test";
import { createSyntheticApi } from "../synthetic-api.js";

test("a long poll started before the outage drops at its boundary", async () => {
  let clock = 0;
  let onRead = () => {};
  const api = createSyntheticApi({ clock: () => { onRead(); return clock; } });
  const origin = `http://127.0.0.1:${await api.listen()}`;
  const path = `${origin}/v1/captures/cap_SYNTH_DROPOUT_BOUNDARY`;
  try {
    await fetch(path);
    clock = 10_900;
    const reading = new Promise((resolve) => { onRead = resolve; });
    const poll = fetch(`${path}/events?after=999&wait=20`, { signal: AbortSignal.timeout(2000) });
    const dropped = assert.rejects(poll, /fetch failed/);
    await reading;
    clock = 11_000;
    await dropped;
    clock = 19_000;
    assert.equal((await fetch(path)).status, 200);
  } finally { await api.close(); }
});
