// Runs the page in headless Chrome under the conditions an embedded preview panel produced:
// scripts and timers ran, but no animation or transition advanced, and frames were drawn only
// when the host captured the panel. Many server batches then reach the page in one frame. The
// page must still show every count and state the server reported. Skipped when no Chrome exists.

import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createViewerServer } from "../relay.js";
import { createSyntheticApi } from "../synthetic-api.js";
import { findChrome, launchChrome } from "./support/chrome.js";

const chromePath = findChrome();
let synthetic;
let viewer;
let origin;

before(async () => {
  synthetic = createSyntheticApi({ speed: 2 });
  const sPort = await synthetic.listen(0);
  viewer = createViewerServer({
    upstreams: { synthetic: { base: `http://127.0.0.1:${sPort}/v1`, label: "Synthetic", kind: "synthetic" } },
    publicDir: join(dirname(fileURLToPath(import.meta.url)), "..", "public"),
  });
  origin = `http://127.0.0.1:${await viewer.listen(0)}`;
});

after(async () => {
  await viewer.close();
  await synthetic.close();
});

// Frames run only when the test calls __flushFrames, the way a panel paints only when captured.
const RARE_FRAMES = `
  window.__frames = [];
  window.requestAnimationFrame = (cb) => window.__frames.push(cb);
  window.cancelAnimationFrame = () => {};
  window.__flushFrames = () => { const due = window.__frames; window.__frames = []; const t = performance.now(); for (const cb of due) cb(t); };
`;

const READ_PAGE = `(() => {
  window.__flushFrames();
  const last = [...document.querySelectorAll("#log li .s")].map((e) => Number(e.textContent.slice(1))).filter(Boolean).at(-1) ?? 0;
  const done = document.querySelector('.st[data-status="done"] .st-mark');
  return {
    lastSeq: last,
    ack: Number(document.querySelector("#ack").textContent),
    kinds: ["k-photo", "k-depth", "k-other"].map((id) => Number(document.getElementById(id).textContent)).reduce((a, b) => a + b, 0),
    status: document.querySelector("#server-state").textContent,
    doneMark: done ? getComputedStyle(done).backgroundColor : null,
    band: document.querySelector("#band-text").textContent,
    caption: document.querySelector("#model-caption").textContent,
    captionOverCanvas: (() => {
      const a = document.querySelector("#model-caption").getBoundingClientRect();
      const b = document.querySelector("#cloud").getBoundingClientRect();
      return a.top < b.bottom && a.bottom > b.top && a.left < b.right && a.right > b.left;
    })(),
  };
})()`;

for (const phase of ["needs_views", "pending", "ready"]) {
  test(`${phase} result followed by null shows the error in the page`, { skip: chromePath ? false : "no Chrome found; set CHROME_PATH" }, async () => {
    const chrome = await launchChrome(chromePath);
    try {
      await chrome.send("Page.enable");
      await chrome.send("Page.addScriptToEvaluateOnNewDocument", { source: RARE_FRAMES + `
        const originalFetch = window.fetch;
        window.__resultReads = 0;
        window.__refreshResult = false;
        window.fetch = async (input, init) => {
          const path = new URL(input, location.href).pathname;
          const json = (body) => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
          if (path.endsWith("/result")) {
            window.__resultReads += 1;
            return json(window.__resultReads > 1 ? null : {
              runId: "run_a", status: ${JSON.stringify(phase === "needs_views" ? "needs_views" : "complete")},
              viewsNeeded: ${phase === "needs_views" ? '[{ id: "view_left", prompt: { title: "Show the left edge" } }]' : '[]'},
              outcome: ${phase === "ready" ? '{ kind: "eligible", message: "Accepted result" }' : 'null'},
            });
          }
          if (path.endsWith("/events")) return json({ status: "complete", events: window.__refreshResult ? [
            { seq: 1, type: "verdict_ready", data: { runId: "run_a", kind: "eligible" } },
          ] : [] });
          if (path.includes("/captures/")) return json({ runId: "run_a", status: "complete" });
          return originalFetch(input, init);
        };
      ` });
      await chrome.send("Page.navigate", { url: `${origin}/?mode=live&source=synthetic&capture=cap_TEST_REFRESH` });
      let page;
      const started = Date.now();
      while (Date.now() - started < 10_000) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        page = await chrome.evaluate(`(() => {
          if (!window.__flushFrames || !document.getElementById("outcome")) return null;
          window.__flushFrames();
          if (window.__resultReads === 1) window.__refreshResult = true;
          const box = document.getElementById("outcome");
          const note = document.getElementById("outcome-note");
          return { reads: window.__resultReads, visible: !box.hidden,
            kind: document.getElementById("outcome-kind").textContent,
            message: document.getElementById("outcome-msg").textContent,
            note: note.hidden ? "" : note.textContent,
            views: document.getElementById("views").hidden ? "" : document.getElementById("views-list").textContent };
        })()`);
        if (page?.reads >= 2 && /Result was not a JSON object/.test(page.message + page.note)) break;
      }
      assert.ok(page?.reads >= 2, "the follower did not refresh the result");
      assert.equal(page.visible, true, "the error must be visible");
      if (phase === "ready") {
        assert.equal(page.kind, "A battery fits");
        assert.equal(page.message, "Accepted result");
        assert.match(page.note, /Refreshing the result failed.*Result was not a JSON object/);
      } else {
        assert.equal(page.kind, "No outcome");
        assert.match(page.note, /Refreshing the result failed.*Result was not a JSON object/);
        if (phase === "needs_views") assert.equal(page.views, "Show the left edgeview_left");
      }
    } finally {
      await chrome.close();
    }
  });
}

test("with the animation clock stopped, counts and stage marks still follow the server", { skip: chromePath ? false : "no Chrome found; set CHROME_PATH" }, async () => {
  const chrome = await launchChrome(chromePath);
  try {
    await chrome.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
    await chrome.send("Page.enable");
    await chrome.send("Animation.enable");
    await chrome.send("Animation.setPlaybackRate", { playbackRate: 0 });
    await chrome.send("Page.addScriptToEvaluateOnNewDocument", { source: RARE_FRAMES });
    const capture = `cap_SYNTH_COMPLETE_B${Date.now()}`;
    await chrome.send("Page.navigate", { url: `${origin}/?mode=live&source=synthetic&capture=${capture}` });

    // Uploads finish about 4.5 s in at double speed; a frame every 3 s sees them in two or three batches.
    const samples = [];
    let doneSince = null;
    let doneMark = null;
    const started = Date.now();
    while (Date.now() - started < 15_000) {
      await new Promise((r) => setTimeout(r, 3000));
      const page = await chrome.evaluate(READ_PAGE);
      samples.push(page);
      if (page.doneMark && doneSince == null) doneSince = Date.now();
      else if (doneSince) {
        doneMark = page.doneMark;
        break;
      }
    }

    // The truth: unique acknowledged paths among events up to the last seq the page had drawn.
    const reply = await fetch(`${origin}/relay/synthetic/captures/${capture}/events?after=0&wait=0`);
    const events = (await reply.json()).events;
    const pathsThrough = (seq) => new Set(events.filter((e) => e.type === "files_committed" && e.seq <= seq).flatMap((e) => e.data.paths)).size;

    // Each frame draws the log and the counts together, so they must agree in that frame.
    const wrong = samples
      .map((s) => ({ seq: s.lastSeq, expected: pathsThrough(s.lastSeq), shown: s.ack, kinds: s.kinds }))
      .filter((s) => s.shown !== s.expected || s.kinds !== s.expected);
    assert.deepEqual(wrong, [], "file counts disagreed with the events the same frame showed");
    assert.ok(samples.at(-1).ack > 0);

    // Provenance is text in normal flow: the band names the synthetic source and the caption
    // never sits over the canvas, which redraws continuously.
    const last = samples.at(-1);
    assert.match(last.band, /synthetic local API/);
    assert.ok(last.caption.length > 0);
    assert.equal(last.captionOverCanvas, false);

    assert.ok(doneSince, "no stage finished within the test window");
    // A transparent computed colour ends in "/ 0)" (oklab) or ", 0)" (rgba).
    assert.doesNotMatch(doneMark, /(\/ 0\)|, 0\))$/, `a finished stage still showed an empty marker (${doneMark})`);
  } finally {
    await chrome.close();
  }
});
