// Client tests: PlacementClient against the contract fake, so the fetch-based
// transport, its exchange receipts and every failure shape are exercised through a
// real HTTP-shaped boundary without a server process. The response hashes are
// recomputed with node:crypto, an implementation independent of the client's
// crypto.subtle path, so a receipt's evidence is checked, not trusted.

import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { PlacementClient } from "./client.ts";
import {
  createContractFake,
  type FakeBehavior,
  MISMATCHED_SHA256,
  sceneText,
} from "./contract-fake.ts";
import { failureHeadline } from "./errors.ts";
import { intakeSceneText } from "./scene.ts";
import {
  applyOutcome,
  createSession,
  newAttemptId,
  type SessionRecord,
  startAttempt,
} from "./session.ts";

interface Submitted {
  readonly report: Awaited<ReturnType<PlacementClient["submitPlacement"]>>;
  readonly requestSha256: string;
  readonly attemptId: string;
}

async function submitted(
  behavior?: FakeBehavior,
  options?: { maxResponseBytes?: number },
): Promise<{
  fake: ReturnType<typeof createContractFake>;
  submitted: Submitted;
}> {
  const fake = createContractFake(behavior ? { behavior } : {});
  const client = new PlacementClient({
    baseUrl: "http://localhost:8000/",
    fetchImpl: fake.fetch,
    ...(options?.maxResponseBytes !== undefined
      ? { maxResponseBytes: options.maxResponseBytes }
      : {}),
  });
  const intake = await intakeSceneText(sceneText());
  const attemptId = newAttemptId("t");
  const report = await client.submitPlacement(intake, attemptId, new AbortController().signal);
  return { fake, submitted: { report, requestSha256: intake.sha256, attemptId } };
}

describe("the bound happy path", () => {
  it("posts the scene's exact bytes and binds the answer to them", async () => {
    const { fake, submitted: run } = await submitted();
    const { report } = run;
    expect(report.ok).toBe(true);
    if (!report.ok) return;
    expect(report.outcome.type).toBe("result");
    if (report.outcome.type !== "result") return;
    expect(report.outcome.binding).toEqual({ kind: "bound" });
    expect(report.outcome.additiveNotes).toEqual([]);
    expect(report.outcome.result.decision).toBe("pass");

    // The request receipt: what was sent, byte for byte.
    expect(report.exchange.request.url).toBe("http://localhost:8000/v1/placements");
    expect(report.exchange.request.method).toBe("POST");
    expect(report.exchange.request.contentType).toBe("application/json");
    expect(report.exchange.request.byteLength).toBe(intakeByteLength());
    expect(report.exchange.request.requestSha256).toBe(run.requestSha256);

    // The fake saw the same bytes, and the answer's hash binds to them.
    const sent = fake.requests[0];
    expect(sent?.method).toBe("POST");
    expect(sent?.headers["content-type"]).toBe("application/json");
    expect(sent?.body?.byteLength).toBe(intakeByteLength());
  });

  it("receipts the response independently: status, type, excerpt, and an oracle-checked hash", async () => {
    const { fake, submitted: run } = await submitted();
    const { report } = run;
    expect(report.ok).toBe(true);
    if (!report.ok) return;
    const response = report.exchange.response;
    expect(response).not.toBeNull();
    if (response === null) return;
    expect(response.status).toBe(200);
    expect(response.contentType).toBe("application/json");
    expect(response.bodyTruncated).toBe(false);
    expect(response.bodyExcerpt?.startsWith('{"schema_version":"1.0"')).toBe(true);
    expect(response.bodySha256).toBe(
      createHash("sha256")
        .update(fake.lastResponseText ?? "", "utf8")
        .digest("hex"),
    );
    expect(response.elapsedMs).toBeGreaterThanOrEqual(0);
    expect(response.finishedAt).toBeGreaterThanOrEqual(response.elapsedMs);
  });

  it("feeds the session layer: the bound result becomes the displayed result", async () => {
    const { submitted: run } = await submitted();
    const { report } = run;
    expect(report.ok).toBe(true);
    if (!report.ok || report.outcome.type !== "result") throw new Error("expected a bound result");
    let session: SessionRecord = createSession("s", "walk", run.requestSha256);
    session = startAttempt(session, run.attemptId, report.exchange.request, 1_000);
    session = applyOutcome(session, run.attemptId, report.outcome);
    expect(session.currentResult?.decision).toBe("pass");
    expect(session.witnesses).toEqual([]);
  });
});

describe("mismatched answers", () => {
  it("returns a mismatched binding when the result names other bytes", async () => {
    const { submitted: run } = await submitted({ kind: "mismatched_sha" });
    const { report } = run;
    expect(report.ok).toBe(true);
    if (!report.ok || report.outcome.type !== "result") return;
    expect(report.outcome.binding).toEqual({
      kind: "mismatched",
      requestSha256: run.requestSha256,
      resultSha256: MISMATCHED_SHA256,
    });
  });

  it("never becomes the displayed result through the session layer", async () => {
    const { submitted: run } = await submitted({ kind: "mismatched_sha" });
    const { report } = run;
    expect(report.ok).toBe(true);
    if (!report.ok || report.outcome.type !== "result")
      throw new Error("expected a result outcome");
    let session: SessionRecord = createSession("s", "walk", run.requestSha256);
    session = startAttempt(session, run.attemptId, report.exchange.request, 1_000);
    session = applyOutcome(session, run.attemptId, report.outcome);
    expect(session.currentResult).toBeNull();
    expect(session.witnesses[0]?.kind).toBe("mismatched_result");
  });
});

describe("unparseable answers", () => {
  it("a 200 that is not JSON is an unparseable-result outcome, not a result", async () => {
    const { submitted: run } = await submitted({ kind: "unparseable_200" });
    const { report } = run;
    expect(report.ok).toBe(true);
    if (!report.ok) return;
    expect(report.outcome.type).toBe("unparseable_result");
    if (report.outcome.type !== "unparseable_result") return;
    expect(report.outcome.failure.reason).toBe("not_json");
    expect(report.exchange.response?.status).toBe(200);
  });

  it("a 200 with a foreign schema_version is refused by the result intake", async () => {
    const { submitted: run } = await submitted({ kind: "wrong_version_200" });
    const { report } = run;
    expect(report.ok).toBe(true);
    if (!report.ok) return;
    expect(report.outcome.type).toBe("unparseable_result");
    if (report.outcome.type !== "unparseable_result") return;
    expect(report.outcome.failure.reason).toBe("wrong_version");
  });

  it("caps an oversized body and refuses to call the remains a result", async () => {
    const { submitted: run } = await submitted(undefined, { maxResponseBytes: 128 });
    const { report } = run;
    expect(report.ok).toBe(true);
    if (!report.ok) return;
    expect(report.outcome.type).toBe("unparseable_result");
    if (report.outcome.type !== "unparseable_result") return;
    expect(report.outcome.failure.reason).toBe("not_json");
    const response = report.exchange.response;
    expect(response?.bodyTruncated).toBe(true);
    expect(response?.bodyByteLength).toBeLessThanOrEqual(128);
  });
});

describe("server refusals", () => {
  it("surfaces the refusal envelope with its code, pointer and known-code flag", async () => {
    const { submitted: run } = await submitted({
      kind: "refused",
      status: 422,
      code: "invalid_scene",
      message: "The scene has no meter object.",
      path: "/meter",
    });
    const { report } = run;
    expect(report.ok).toBe(false);
    if (report.ok) return;
    expect(report.failure).toEqual({
      kind: "server_error",
      status: 422,
      envelope: {
        code: "invalid_scene",
        message: "The scene has no meter object.",
        path: "/meter",
      },
      knownCode: true,
    });
    expect(report.exchange.response?.status).toBe(422);
  });

  it("flags an unknown refusal code instead of mashing it into a known one", async () => {
    const { submitted: run } = await submitted({
      kind: "refused",
      status: 422,
      code: "quantum_disagreement",
      message: "The scene is in two states at once.",
      path: null,
    });
    const { report } = run;
    expect(report.ok).toBe(false);
    if (report.ok) return;
    expect(report.failure.kind).toBe("server_error");
    if (report.failure.kind !== "server_error") return;
    expect(report.failure.knownCode).toBe(false);
    expect(failureHeadline(report.failure)).toContain("does not know");
  });

  it("classifies an error body that is not the envelope as unparseable_error", async () => {
    const { submitted: run } = await submitted({
      kind: "raw",
      status: 502,
      contentType: "text/html",
      bodyText: "<html>bad gateway</html>",
    });
    const { report } = run;
    expect(report.ok).toBe(false);
    if (report.ok) return;
    expect(report.failure).toEqual({
      kind: "unparseable_error",
      status: 502,
      detail: expect.stringContaining("502"),
    });
  });
});

describe("transport faults and cancellation", () => {
  it("reports a network fault with the request receipt but no response", async () => {
    const { submitted: run } = await submitted({ kind: "throws", detail: "connection reset" });
    const { report } = run;
    expect(report.ok).toBe(false);
    if (report.ok) return;
    expect(report.failure).toEqual({ kind: "network_error", detail: "connection reset" });
    expect(report.exchange.response).toBeNull();
    expect(report.exchange.request.requestSha256).toBe(run.requestSha256);
  });

  it("reports cancellation when the signal is already aborted", async () => {
    const fake = createContractFake();
    const client = new PlacementClient({ baseUrl: "http://localhost:8000", fetchImpl: fake.fetch });
    const intake = await intakeSceneText(sceneText());
    const controller = new AbortController();
    controller.abort();
    const report = await client.submitPlacement(intake, newAttemptId("t"), controller.signal);
    expect(report.ok).toBe(false);
    if (report.ok) return;
    expect(report.failure).toEqual({ kind: "aborted" });
    expect(fake.requests).toHaveLength(0);
  });

  it("reports cancellation during flight", async () => {
    const fake = createContractFake({ behavior: { kind: "delayed_then_abort", delayMs: 100 } });
    const client = new PlacementClient({ baseUrl: "http://localhost:8000", fetchImpl: fake.fetch });
    const intake = await intakeSceneText(sceneText());
    const controller = new AbortController();
    const pending = client.submitPlacement(intake, newAttemptId("t"), controller.signal);
    setTimeout(() => controller.abort(), 10);
    const report = await pending;
    expect(report.ok).toBe(false);
    if (report.ok) return;
    expect(report.failure).toEqual({ kind: "aborted" });
  });
});

describe("auth and the read-only endpoints", () => {
  it("sends the bearer token only when configured", async () => {
    const plain = createContractFake();
    const tokened = createContractFake();
    const plainClient = new PlacementClient({
      baseUrl: "http://localhost:8000",
      fetchImpl: plain.fetch,
    });
    const tokenClient = new PlacementClient({
      baseUrl: "http://localhost:8000",
      fetchImpl: tokened.fetch,
      authToken: "tok",
    });
    const intake = await intakeSceneText(sceneText());
    await plainClient.submitPlacement(intake, newAttemptId("t"), new AbortController().signal);
    await tokenClient.submitPlacement(intake, newAttemptId("t"), new AbortController().signal);
    expect(plain.requests[0]?.headers.authorization).toBeUndefined();
    expect(tokened.requests[0]?.headers.authorization).toBe("Bearer tok");
  });

  it("reads /health", async () => {
    const fake = createContractFake();
    const client = new PlacementClient({ baseUrl: "http://localhost:8000", fetchImpl: fake.fetch });
    const { health, failure } = await client.getHealth();
    expect(failure).toBeNull();
    expect(health).toEqual({
      status: "ok",
      schemaVersion: "1.0",
      policy: { id: "public-demo", auto_approve: true },
    });
    expect(fake.requests[0]?.method).toBe("GET");
  });

  it("classifies a refused health answer", async () => {
    const fake = createContractFake({
      behavior: {
        kind: "refused",
        status: 503,
        code: "internal_error",
        message: "rules not loaded",
        path: null,
      },
      applyTo: "/health",
    });
    const client = new PlacementClient({ baseUrl: "http://localhost:8000", fetchImpl: fake.fetch });
    const { health, failure } = await client.getHealth();
    expect(health).toBeNull();
    expect(failure).toEqual({
      kind: "server_error",
      status: 503,
      envelope: { code: "internal_error", message: "rules not loaded", path: null },
      knownCode: true,
    });
  });

  it("reads both schema endpoints", async () => {
    const fake = createContractFake();
    const client = new PlacementClient({ baseUrl: "http://localhost:8000", fetchImpl: fake.fetch });
    const { schemas, failure } = await client.getSchemas();
    expect(failure).toBeNull();
    expect(schemas?.scene).toEqual({ type: "object", required: ["meter", "walls"] });
    expect(schemas?.result).toEqual({ type: "object", required: ["schema_version"] });
  });
});

/** The fixture scene's byte length as an upload, computed independently of the client. */
function intakeByteLength(): number {
  return new TextEncoder().encode(sceneText()).byteLength;
}
