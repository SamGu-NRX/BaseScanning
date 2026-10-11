// Store tests: the session store bound to the contract fake, which is the only server
// surface these tests touch — nothing here opens a real network connection. Each test
// drives the operations the page does (select a file, submit, cancel, re-select) and
// asserts on the store's displayed view and the session's recorded receipts,
// witnesses and history. Response hashes are recomputed with node:crypto, an
// implementation independent of the client's crypto.subtle path, so receipt evidence
// is checked, not trusted.

import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import {
  type ContractFake,
  createContractFake,
  MISMATCHED_SHA256,
  sceneText,
} from "../contract/contract-fake.ts";
import { sha256HexOfText } from "../contract/hash.ts";
import { DEFAULT_BASE_URL, type SceneFileInput, SessionStore, statusLine } from "./store.ts";

function fileNamed(name: string, text: string = sceneText()): SceneFileInput {
  return { name, text: async () => text };
}

/** A fetch that delegates to a switchable fake, so one scenario can change behaviour mid-flight. */
function switchableTransport(initial: ContractFake): {
  fetch: typeof fetch;
  readonly fake: ContractFake;
  switchTo(next: ContractFake): void;
} {
  let current = initial;
  return {
    fetch: (input, init) => current.fetch(input, init),
    get fake() {
      return current;
    },
    switchTo(next) {
      current = next;
    },
  };
}

describe("select then submit", () => {
  it("binds the newest attempt's result to the display with both receipts attached", async () => {
    const fake = createContractFake();
    const store = new SessionStore({ fetchImpl: fake.fetch });
    expect(store.getSnapshot().baseUrl).toBe(DEFAULT_BASE_URL);
    expect(store.getSnapshot().display.kind).toBe("no_scene");

    await store.selectSceneFile(fileNamed("scene.json"));
    const text = sceneText();
    const ready = store.getSnapshot();
    expect(ready.scene?.fileName).toBe("scene.json");
    expect(ready.scene?.sha256).toBe(await sha256HexOfText(text));
    expect(ready.scene?.byteLength).toBe(new TextEncoder().encode(text).byteLength);
    expect(ready.display.kind).toBe("scene_ready");
    expect(ready.session?.attempts).toHaveLength(0);

    await store.submit();
    const snap = store.getSnapshot();
    expect(snap.display.kind).toBe("bound");
    if (snap.display.kind !== "bound") return;
    const session = snap.session;
    if (session === null) throw new Error("no session after a bound submission");
    expect(session.currentResult).toBe(snap.display.result);
    expect(snap.display.result.decision).toBe("pass");
    expect(snap.display.result.stats.input_sha256).toBe(snap.scene?.sha256);
    expect(statusLine(snap.display)).toContain("pass");

    const attempt = session.attempts.at(-1);
    if (attempt === undefined) throw new Error("no attempt recorded");
    expect(attempt.status).toBe("bound");
    // The request receipt: what was sent, and its hash.
    expect(attempt.request.url).toBe("http://localhost:8000/v1/placements");
    expect(attempt.request.method).toBe("POST");
    expect(attempt.request.contentType).toBe("application/json");
    expect(attempt.request.byteLength).toBe(snap.scene?.byteLength);
    expect(attempt.request.requestSha256).toBe(snap.scene?.sha256);
    // The response receipt: what came back, hashed independently of the client.
    expect(attempt.outcome?.type).toBe("result");
    if (attempt.outcome?.type !== "result") return;
    expect(attempt.outcome.response.status).toBe(200);
    expect(attempt.outcome.response.contentType).toBe("application/json");
    expect(attempt.outcome.response.bodySha256).toBe(
      createHash("sha256")
        .update(fake.lastResponseText ?? "")
        .digest("hex"),
    );
    // The fake saw the scene's exact bytes and nothing authorizing.
    expect(fake.requests).toHaveLength(1);
    expect(new TextDecoder().decode(fake.requests[0]?.body ?? new Uint8Array())).toBe(text);
    expect(fake.requests[0]?.headers.authorization).toBeUndefined();
    // A clean bound answer leaves no witnesses.
    expect(session.witnesses).toHaveLength(0);
  });

  it("sends the bearer token to the wire once and never exposes it in any snapshot or receipt", async () => {
    const fake = createContractFake();
    const store = new SessionStore({ fetchImpl: fake.fetch });
    store.setBaseUrl("http://localhost:9000");
    store.setToken("sekrit-token-abc");
    expect(store.getSnapshot().hasToken).toBe(true);

    await store.selectSceneFile(fileNamed("scene.json"));
    await store.submit();
    const snap = store.getSnapshot();
    expect(snap.display.kind).toBe("bound");
    expect(fake.requests[0]?.url).toBe("http://localhost:9000/v1/placements");
    expect(fake.requests[0]?.headers.authorization).toBe("Bearer sekrit-token-abc");

    // The whole snapshot — session, attempts, receipts, witnesses, display — carries
    // no trace of the token.
    expect(JSON.stringify(snap)).not.toContain("sekrit-token-abc");

    store.setToken("");
    expect(store.getSnapshot().hasToken).toBe(false);
  });
});

describe("cancel mid-flight", () => {
  it("records a cancelled outcome with its request receipt, and a new attempt can still bind", async () => {
    const slow = createContractFake({
      behavior: { kind: "delayed_then_abort", delayMs: 50 },
    });
    const fast = createContractFake();
    const transport = switchableTransport(slow);
    const store = new SessionStore({ fetchImpl: transport.fetch });
    await store.selectSceneFile(fileNamed("scene.json"));

    const first = store.submit();
    expect(store.getSnapshot().session?.attempts.at(-1)?.status).toBe("submitting");
    store.cancel();
    await first;

    const cancelled = store.getSnapshot();
    expect(cancelled.display.kind).toBe("cancelled");
    const attempt = cancelled.session?.attempts.at(-1);
    expect(attempt?.status).toBe("cancelled");
    expect(attempt?.outcome?.type).toBe("cancelled");
    // The aborted attempt keeps the receipt of what it sent.
    expect(attempt?.request.requestSha256).toBe(cancelled.scene?.sha256);
    expect(cancelled.session?.currentResult).toBeNull();

    // Cancelling a settled attempt changes nothing.
    store.cancel();
    expect(store.getSnapshot().session?.attempts).toHaveLength(1);

    transport.switchTo(fast);
    await store.submit();
    const retried = store.getSnapshot();
    expect(retried.display.kind).toBe("bound");
    expect(retried.session?.attempts).toHaveLength(2);
    expect(retried.session?.attempts[0]?.status).toBe("cancelled");
    expect(retried.session?.currentResult?.stats.input_sha256).toBe(retried.scene?.sha256);
  });
});

describe("refusal", () => {
  it("shows the server's message and path from the 422 envelope and witnesses the refusal", async () => {
    const fake = createContractFake({
      behavior: {
        kind: "refused",
        status: 422,
        code: "invalid_scene",
        message: "Wall 'north' has no usable baseline.",
        path: "/walls/0/baseline",
      },
    });
    const store = new SessionStore({ fetchImpl: fake.fetch });
    await store.selectSceneFile(fileNamed("scene.json"));
    await store.submit();

    const snap = store.getSnapshot();
    expect(snap.display.kind).toBe("refused");
    if (snap.display.kind !== "refused") return;
    expect(snap.display.failure.kind).toBe("server_error");
    if (snap.display.failure.kind !== "server_error") return;
    expect(snap.display.failure.envelope.code).toBe("invalid_scene");
    expect(snap.display.failure.envelope.message).toBe("Wall 'north' has no usable baseline.");
    expect(snap.display.failure.envelope.path).toBe("/walls/0/baseline");
    expect(snap.session?.currentResult).toBeNull();

    const attempt = snap.session?.attempts.at(-1);
    expect(attempt?.status).toBe("refused");
    expect(attempt?.outcome?.type).toBe("refused");
    if (attempt?.outcome?.type !== "refused") return;
    expect(attempt.outcome.response.status).toBe(422);
    expect(attempt.outcome.response.bodySha256).toBe(
      createHash("sha256")
        .update(fake.lastResponseText ?? "")
        .digest("hex"),
    );

    const witness = snap.session?.witnesses.at(-1);
    expect(witness?.kind).toBe("refused");
    if (witness?.kind !== "refused") return;
    expect(witness.code).toBe("invalid_scene");
    expect(witness.detail).toBe("Wall 'north' has no usable baseline.");
  });
});

describe("mismatched answer", () => {
  it("keeps the result in history but never displays it, and witnesses the mismatch", async () => {
    const fake = createContractFake({ behavior: { kind: "mismatched_sha" } });
    const store = new SessionStore({ fetchImpl: fake.fetch });
    await store.selectSceneFile(fileNamed("scene.json"));
    await store.submit();

    const snap = store.getSnapshot();
    expect(snap.display.kind).toBe("mismatched");
    expect(snap.session?.currentResult).toBeNull();

    const attempt = snap.session?.attempts.at(-1);
    expect(attempt?.status).toBe("mismatched");
    expect(attempt?.outcome?.type).toBe("result");
    if (attempt?.outcome?.type !== "result") return;
    // The answer is retained on the attempt that produced it, labelled with its binding.
    expect(attempt.outcome.result.summary.length).toBeGreaterThan(0);
    expect(attempt.outcome.binding.kind).toBe("mismatched");
    if (attempt.outcome.binding.kind !== "mismatched") return;
    expect(attempt.outcome.binding.requestSha256).toBe(snap.scene?.sha256);
    expect(attempt.outcome.binding.resultSha256).toBe(MISMATCHED_SHA256);

    const witness = snap.session?.witnesses.at(-1);
    expect(witness?.kind).toBe("mismatched_result");
    if (witness?.kind !== "mismatched_result") return;
    expect(witness.attemptId).toBe(attempt.attemptId);
    expect(witness.resultSha256).toBe(MISMATCHED_SHA256);

    // A retry against an honest answer binds and displays.
    const honest = createContractFake();
    const transport = switchableTransport(fake);
    const store2 = new SessionStore({ fetchImpl: transport.fetch });
    await store2.selectSceneFile(fileNamed("scene.json"));
    transport.switchTo(honest);
    await store2.submit();
    expect(store2.getSnapshot().display.kind).toBe("bound");
  });
});

describe("a late outcome for a superseded attempt", () => {
  it("is witnessed and recorded in history, never displayed over the newer answer", async () => {
    const slow = createContractFake({
      behavior: { kind: "delayed_then_abort", delayMs: 120 },
    });
    const fast = createContractFake();
    const transport = switchableTransport(slow);
    const store = new SessionStore({ fetchImpl: transport.fetch });
    await store.selectSceneFile(fileNamed("scene.json"));

    const first = store.submit(); // lands late, with a bound answer for its own bytes
    transport.switchTo(fast);
    await store.submit(); // binds now
    const mid = store.getSnapshot();
    expect(mid.display.kind).toBe("bound");

    await first;
    const snap = store.getSnapshot();
    expect(snap.display.kind).toBe("bound");
    const attempts = snap.session?.attempts ?? [];
    expect(attempts).toHaveLength(2);
    expect(attempts[0]?.status).toBe("stale");
    expect(attempts[0]?.outcome?.type).toBe("result");
    // The display and the current result still belong to the newest attempt.
    expect(snap.display.kind === "bound" ? snap.display.attemptId : null).toBe(
      attempts[1]?.attemptId,
    );
    expect(snap.session?.currentResult?.stats.input_sha256).toBe(snap.scene?.sha256);

    const witness = snap.session?.witnesses.find((w) => w.kind === "stale_outcome");
    if (witness?.kind !== "stale_outcome") throw new Error("no stale_outcome witness recorded");
    expect(witness.attemptId).toBe(attempts[0]?.attemptId);
    expect(witness.newestAttemptId).toBe(attempts[1]?.attemptId);
  });
});

describe("a refused scene selection", () => {
  it("is refused locally, records no session, and never reaches the wire", async () => {
    const fake = createContractFake();
    const store = new SessionStore({ fetchImpl: fake.fetch });

    await store.selectSceneFile(fileNamed("broken.json", "{ not json"));
    let snap = store.getSnapshot();
    expect(snap.intakeError?.code).toBe("invalid_json");
    expect(snap.session).toBeNull();
    expect(snap.display.kind).toBe("no_scene");
    await store.submit(); // a no-op without a session
    expect(fake.requests).toHaveLength(0);

    await store.selectSceneFile(fileNamed("shape.json", JSON.stringify({ nope: true })));
    snap = store.getSnapshot();
    expect(snap.intakeError?.code).toBe("invalid_scene");
    expect(snap.intakeError?.path).toBe("/meter");
    expect(fake.requests).toHaveLength(0);
  });
});

describe("re-selecting a scene mid-flight", () => {
  it("abandons the in-flight attempt and opens a fresh session it cannot touch", async () => {
    const slow = createContractFake({
      behavior: { kind: "delayed_then_abort", delayMs: 120 },
    });
    const store = new SessionStore({ fetchImpl: slow.fetch });
    await store.selectSceneFile(fileNamed("first.json"));
    const abandoned = store.submit();
    const oldSessionId = store.getSnapshot().session?.sessionId;

    await store.selectSceneFile(fileNamed("second.json"));
    const fresh = store.getSnapshot();
    expect(fresh.scene?.fileName).toBe("second.json");
    expect(fresh.session?.sessionId).not.toBe(oldSessionId);
    expect(fresh.session?.attempts).toHaveLength(0);
    expect(fresh.display.kind).toBe("scene_ready");

    await abandoned; // the abandoned attempt settles; it must not touch the new session
    const settled = store.getSnapshot();
    expect(settled.session?.sessionId).toBe(fresh.session?.sessionId);
    expect(settled.session?.attempts).toHaveLength(0);
    expect(settled.display.kind).toBe("scene_ready");
    expect(settled.session?.witnesses).toHaveLength(0);
  });
});
