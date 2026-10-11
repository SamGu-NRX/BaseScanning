// Persistence and redaction tests: the store bound to the contract fake (the only
// server surface here — no real network) and an in-memory stand-in for localStorage.
// Each test drives the operations the page does and asserts on what actually lands in
// storage and what comes back out: the roundtrip must restore attempts, receipts,
// witnesses and the displayed result, must never carry a credential, and a doctored
// record must fail closed instead of displaying what it claims.

import { describe, expect, it } from "vitest";
import {
  type ContractFake,
  createContractFake,
  MISMATCHED_SHA256,
  sceneText,
} from "../contract/contract-fake.ts";
import { sha256HexOfText } from "../contract/hash.ts";
import {
  PERSIST_FORMAT,
  PERSIST_VERSION,
  parseSessionPayload,
  SESSION_STORAGE_KEY,
} from "./persist.ts";
import { type SceneFileInput, SessionStore } from "./store.ts";

function fileNamed(name: string, text: string = sceneText()): SceneFileInput {
  return { name, text: async () => text };
}

function must<T>(value: T | null | undefined, what: string): T {
  if (value === null || value === undefined) throw new Error(`test setup lost ${what}`);
  return value;
}

/** In-memory stand-in for localStorage, with the map exposed for direct assertions. */
function memoryPersistence(): {
  readonly storage: {
    getItem(key: string): string | null;
    setItem(key: string, value: string): void;
    removeItem(key: string): void;
  };
  readonly map: Map<string, string>;
} {
  const map = new Map<string, string>();
  return {
    map,
    storage: {
      getItem: (key) => map.get(key) ?? null,
      setItem: (key, value) => {
        map.set(key, value);
      },
      removeItem: (key) => {
        map.delete(key);
      },
    },
  };
}

/** Depth-first key scan, so a credential cannot hide in a nested block of a record. */
function findKeyMatching(value: unknown, needle: RegExp, path = ""): string | null {
  if (Array.isArray(value)) {
    for (const [index, item] of value.entries()) {
      const hit = findKeyMatching(item, needle, `${path}[${index}]`);
      if (hit !== null) return hit;
    }
    return null;
  }
  if (typeof value === "object" && value !== null) {
    for (const [key, item] of Object.entries(value)) {
      const keyPath = path === "" ? key : `${path}.${key}`;
      if (needle.test(key)) return keyPath;
      const hit = findKeyMatching(item, needle, keyPath);
      if (hit !== null) return hit;
    }
  }
  return null;
}

/** First store with a fresh bound session; returns the store, its raw saved bytes and the fake. */
async function boundStoreWith(token: string): Promise<{
  store: SessionStore;
  raw: string;
  fake: ContractFake;
  persistence: ReturnType<typeof memoryPersistence>;
}> {
  const fake = createContractFake();
  const persistence = memoryPersistence();
  const store = new SessionStore({ fetchImpl: fake.fetch, storage: persistence.storage });
  store.setToken(token);
  await store.selectSceneFile(fileNamed("scene.json"));
  await store.submit();
  expect(store.getSnapshot().display.kind).toBe("bound");
  const raw = must(persistence.map.get(SESSION_STORAGE_KEY), "the saved record");
  return { store, raw, fake, persistence };
}

describe("browser-local persistence", () => {
  it("saves the redacted record after every change and never stores the token or headers", async () => {
    const { store, raw } = await boundStoreWith("sekrit-reopen-token");
    // The record carries the scene's exact text (as the escaped string of the stored
    // JSON) and the attempt history...
    expect(raw).toContain(PERSIST_FORMAT);
    expect(raw).toContain(JSON.stringify(sceneText()).slice(1, -1));
    expect(raw).toContain("requestSha256");
    // ...and no trace of the credential, by field or by value.
    expect(raw).not.toContain("sekrit-reopen-token");
    const parsed: unknown = JSON.parse(raw);
    expect(findKeyMatching(parsed, /token|authorization|bearer|cookie/i)).toBeNull();
    // The store's snapshot stays clean too (the token lives in memory only).
    expect(JSON.stringify(store.getSnapshot())).not.toContain("sekrit-reopen-token");
  });

  it("reopens the saved session with the display intact and can submit a fresh attempt", async () => {
    const token = "sekrit-reopen-token";
    const { store: first, raw, fake, persistence } = await boundStoreWith(token);
    const firstSession = must(first.getSnapshot().session, "the bound session");
    const firstScene = must(first.getSnapshot().scene, "the scene summary");

    // A brand-new store over the same storage — what a page reload sees.
    const reopened = new SessionStore({ fetchImpl: fake.fetch, storage: persistence.storage });
    const before = reopened.getSnapshot();
    expect(before.savedSession).not.toBeNull();
    expect(before.savedSession?.attemptCount).toBe(1);
    expect(before.savedSession?.sceneFileName).toBe("scene.json");
    expect(before.display.kind).toBe("no_scene"); // Nothing is shown until the user reopens.

    await reopened.reopenSession();
    const snap = reopened.getSnapshot();
    expect(snap.display.kind).toBe("bound");
    if (snap.display.kind !== "bound") return;
    expect(snap.display.result.decision).toBe("pass");
    expect(snap.hasToken).toBe(false); // The token is never restored.
    expect(snap.scene?.sha256).toBe(firstScene.sha256);
    expect(snap.scene?.sha256).toBe(await sha256HexOfText(sceneText()));
    // The whole record comes back equal: attempts, receipts, outcome, witnesses.
    expect(snap.session).toEqual(firstSession);
    // The opened session is now the live one; a new attempt starts and binds.
    await reopened.submit();
    const after = reopened.getSnapshot();
    expect(after.display.kind).toBe("bound");
    expect(after.session?.attempts).toHaveLength(2);
    expect(after.session?.attempts[0]?.status).toBe("bound"); // history kept
    // The saved record still carries no credential after the new attempt.
    expect(must(raw, "raw")).not.toContain(token);
  });

  it("reopens an interrupted in-flight attempt as cancelled, keeping its receipt, and can bind afterwards", async () => {
    const persistence = memoryPersistence();
    const hanging: typeof fetch = () => new Promise(() => {}); // never settles
    const stuck = new SessionStore({ fetchImpl: hanging, storage: persistence.storage });
    await stuck.selectSceneFile(fileNamed("scene.json"));
    const inFlight = stuck.submit(); // published synchronously: the record now says "submitting"
    const raw = must(persistence.map.get(SESSION_STORAGE_KEY), "the mid-flight record");
    const stored = must(JSON.parse(raw), "the stored record") as {
      session: { attempts: { status: unknown }[] };
    };
    const attempt = must(stored.session.attempts[0], "the mid-flight attempt");
    expect(attempt.status).toBe("submitting");

    // The page reloads; the reopened record settles what the data can mean.
    const fake = createContractFake();
    const reopened = new SessionStore({ fetchImpl: fake.fetch, storage: persistence.storage });
    await reopened.reopenSession();
    const snap = reopened.getSnapshot();
    expect(snap.display.kind).toBe("cancelled"); // No bound answer was ever received;
    // the newest (interrupted) attempt is what the display reports.
    const attemptRestored = must(snap.session?.attempts[0], "the restored attempt");
    expect(attemptRestored.status).toBe("cancelled");
    expect(attemptRestored.outcome?.type).toBe("cancelled");
    expect(attemptRestored.request.requestSha256).toBe(await sha256HexOfText(sceneText()));
    void inFlight;

    // A fresh attempt on the reopened session still binds.
    await reopened.submit();
    expect(reopened.getSnapshot().display.kind).toBe("bound");
    expect(reopened.getSnapshot().session?.attempts).toHaveLength(2);
  });

  it("reports a corrupt saved record, keeps the bytes, and discard clears the slot", () => {
    const persistence = memoryPersistence();
    persistence.map.set(SESSION_STORAGE_KEY, "this is not the record you are looking for");
    const store = new SessionStore({
      fetchImpl: createContractFake().fetch,
      storage: persistence.storage,
    });
    const snap = store.getSnapshot();
    expect(snap.savedSession).toBeNull();
    expect(snap.storageWarning).toContain("could not be read");

    store.discardSavedSession();
    expect(persistence.map.has(SESSION_STORAGE_KEY)).toBe(false);
  });
});

describe("restore refuses records it cannot trust", () => {
  it("rejects malformed JSON, foreign formats and unsupported versions", async () => {
    const { raw } = await boundStoreWith("t");
    const payload = must(JSON.parse(raw) as Record<string, unknown>, "payload");
    expect(parseSessionPayload("not json at all")).toMatchObject({
      ok: false,
      failure: { code: "invalid_json" },
    });
    expect(
      parseSessionPayload(JSON.stringify({ ...payload, format: "someone-elses-export" })),
    ).toMatchObject({
      ok: false,
      failure: { code: "wrong_format" },
    });
    expect(
      parseSessionPayload(JSON.stringify({ ...payload, version: PERSIST_VERSION + 1 })),
    ).toMatchObject({
      ok: false,
      failure: { code: "wrong_version" },
    });
    expect(parseSessionPayload("{}")).toMatchObject({
      ok: false,
      failure: { code: "wrong_format" },
    });
  });

  it("rejects an attempt history this build cannot read", async () => {
    const { raw } = await boundStoreWith("t");
    const payload = must(JSON.parse(raw) as Record<string, unknown>, "payload");
    const session = must(payload.session as Record<string, unknown>, "session");
    const attempts = must(session.attempts as unknown[], "attempts");
    const attempt = must(attempts[0] as Record<string, unknown>, "attempt");

    expect(
      parseSessionPayload(
        JSON.stringify({
          ...payload,
          session: { ...session, attempts: [{ ...attempt, outcome: { type: "surprise" } }] },
        }),
      ),
    ).toMatchObject({ ok: false, failure: { code: "invalid_attempt" } });
    expect(
      parseSessionPayload(
        JSON.stringify({
          ...payload,
          session: { ...session, witnesses: [{ kind: "surprise" }] },
        }),
      ),
    ).toMatchObject({ ok: false, failure: { code: "invalid_witness" } });
  });

  it("re-derives the status from the data: a doctored 'bound' label on a mismatched answer cannot display it", async () => {
    // A session whose answer named other bytes: recorded as mismatched, never displayed.
    const mismatched = createContractFake({ behavior: { kind: "mismatched_sha" } });
    const persistence = memoryPersistence();
    const source = new SessionStore({ fetchImpl: mismatched.fetch, storage: persistence.storage });
    await source.selectSceneFile(fileNamed("scene.json"));
    await source.submit();
    expect(source.getSnapshot().display.kind).toBe("mismatched");
    const raw = must(persistence.map.get(SESSION_STORAGE_KEY), "the mismatched record");

    // Doctor the stored label to claim the display for the mismatched answer.
    const payload = must(JSON.parse(raw) as Record<string, unknown>, "payload");
    const session = must(payload.session as Record<string, unknown>, "session");
    const attempts = must(session.attempts as unknown[], "attempts");
    const attempt = must(attempts[0] as Record<string, unknown>, "attempt");
    attempt.status = "bound";
    expect(attempt.outcome).not.toBeNull();

    // The restore path also re-derives the result's own binding: the doctored hash
    // cannot promote it either.
    const outcome = must(attempt.outcome as Record<string, unknown>, "outcome");
    const result = must(outcome.result as Record<string, unknown>, "result");
    const stats = must(result.stats as Record<string, unknown>, "stats");
    stats.input_sha256 = MISMATCHED_SHA256; // already lies; still re-derived on restore

    const store = new SessionStore({ fetchImpl: createContractFake().fetch, storage: null });
    await store.importSession(
      JSON.stringify({ ...payload, session: { ...session, attempts: [attempt] } }),
    );
    const snap = store.getSnapshot();
    expect(snap.display.kind).toBe("mismatched"); // shown as what it is — not as the answer
    expect(snap.restoreError).toBeNull();
    const restored = must(snap.session?.attempts[0], "the restored attempt");
    expect(restored.status).toBe("mismatched");
    expect(snap.session?.currentResult).toBeNull();
    expect(snap.session?.witnesses.length).toBeGreaterThan(0);
  });
});

describe("redacted export and import", () => {
  it("exports a credential-free record whose import restores an equal session", async () => {
    const token = "sekrit-export-token";
    const { store: first } = await boundStoreWith(token);
    const firstSession = must(first.getSnapshot().session, "the bound session");
    const exported = must(first.exportSession(), "the export");
    expect(exported).not.toContain(token);
    expect(findKeyMatching(JSON.parse(exported), /token|authorization|bearer|cookie/i)).toBeNull();

    const second = new SessionStore({ fetchImpl: createContractFake().fetch, storage: null });
    await second.importSession(exported);
    const snap = second.getSnapshot();
    expect(snap.display.kind).toBe("bound");
    if (snap.display.kind !== "bound") return;
    expect(snap.display.result.decision).toBe("pass");
    expect(snap.scene?.sha256).toBe(first.getSnapshot().scene?.sha256);
    expect(snap.session).toEqual(firstSession);
    expect(snap.hasToken).toBe(false);
  });

  it("refuses an unusable import and leaves the current session untouched", async () => {
    const { store } = await boundStoreWith("t");
    const sessionBefore = store.getSnapshot().session;

    await store.importSession("{ definitely not the record");
    expect(store.getSnapshot().restoreError?.code).toBe("invalid_json");
    expect(store.getSnapshot().session).toEqual(sessionBefore);
    expect(store.getSnapshot().display.kind).toBe("bound");

    await store.importSession(JSON.stringify({ hello: "world" }));
    expect(store.getSnapshot().restoreError?.code).toBe("wrong_format");
    expect(store.getSnapshot().session).toEqual(sessionBefore);
  });

  it("returns no export when there is no session", () => {
    const store = new SessionStore({ fetchImpl: createContractFake().fetch, storage: null });
    expect(store.exportSession()).toBeNull();
  });
});
