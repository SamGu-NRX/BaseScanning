import assert from "node:assert/strict";
import { test } from "node:test";
import { createSyntheticApi } from "../synthetic-api.js";

test("a long poll started before the outage drops at its boundary", async () => {
  let clock = 0;
  let wake = () => {};
  let scheduled;
  const waiting = new Promise((resolve) => { scheduled = resolve; });
  const api = createSyntheticApi({
    clock: () => clock,
    sleep: (ms) => new Promise((resolve) => { wake = resolve; scheduled(ms); }),
  });
  const origin = `http://127.0.0.1:${await api.listen()}`;
  const path = `${origin}/v1/captures/cap_SYNTH_DROPOUT_BOUNDARY`;
  try {
    await fetch(path);
    clock = 10_900;
    const poll = fetch(`${path}/events?after=999&wait=20`, { signal: AbortSignal.timeout(2000) });
    const dropped = assert.rejects(poll, /fetch failed/);
    const delay = await waiting;
    clock = 11_000;
    wake();
    assert.equal(delay, 100, "the poll wakes at 11 s, before the next event");
    await dropped;
    clock = 19_000;
    assert.equal((await fetch(path)).status, 200);
  } finally { clock = 11_000; wake(); await api.close(); }
});
