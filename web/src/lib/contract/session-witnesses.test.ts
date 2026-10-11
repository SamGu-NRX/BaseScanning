// Witness tests for the session layer, one per witness the contract names:
//
// - stale: an outcome for a superseded attempt is recorded and never applied;
// - mismatched: a result naming other bytes is a witness, never the displayed result;
// - unparseable (missing): a 200 whose body is not a result is a witness, never displayed.
//
// Plus the display rules around them: a refused, failed or cancelled attempt strips the
// displayed result, the history keeps every outcome with its own binding, and starting
// a new attempt demotes the previous one. Pure state under test: no fetch, no real
// clock (every now is explicit). The result-intake refusals behind the "missing"
// witness are covered here too, because that is where their failures surface.

import { describe, expect, it } from "vitest";
import { MISMATCHED_SHA256, RULES_SHA256, resultFixture } from "./contract-fake.ts";
import type { PlacementFailure } from "./errors.ts";
import { bindResultToRequest, intakeResultBody, REQUIRED_RESULT_FIELDS } from "./result.ts";
import type { AttemptOutcome, RequestReceipt, ResponseReceipt } from "./session.ts";
import {
  applyOutcome,
  createSession,
  newAttemptId,
  outcomeFromBody,
  type SessionRecord,
  startAttempt,
} from "./session.ts";

const SHA = RULES_SHA256;

function requestReceipt(attemptId: string, sha: string): RequestReceipt {
  return {
    attemptId,
    url: "http://localhost:8000/v1/placements",
    method: "POST",
    contentType: "application/json",
    byteLength: 1024,
    requestSha256: sha,
    startedAt: 1_000,
  };
}

function responseReceipt(attemptId: string): ResponseReceipt {
  return {
    attemptId,
    status: 200,
    contentType: "application/json",
    bodyByteLength: 2048,
    bodySha256: SHA,
    bodyExcerpt: "{}",
    bodyTruncated: false,
    elapsedMs: 5,
    finishedAt: 1_005,
  };
}

function startAttemptAt(
  session: SessionRecord,
  attemptId: string,
  sha: string,
  now: number,
): SessionRecord {
  return startAttempt(session, attemptId, requestReceipt(attemptId, sha), now);
}

/** A result outcome the way the client produces it: parsed from the body, binding attached. */
function resultOutcome(attemptId: string, requestSha: string, resultSha: string): AttemptOutcome {
  const body: unknown = JSON.parse(JSON.stringify(resultFixture(resultSha)));
  return outcomeFromBody(responseReceipt(attemptId), body, requestSha);
}

function attemptOf(session: SessionRecord, attemptId: string) {
  const attempt = session.attempts.find((candidate) => candidate.attemptId === attemptId);
  if (attempt === undefined) throw new Error(`no attempt ${attemptId} recorded`);
  return attempt;
}

describe("attempt identity", () => {
  it("hands out monotonic, session-scoped ids that are never reused", () => {
    const first = newAttemptId("s1");
    const second = newAttemptId("s1");
    const otherSession = newAttemptId("s2");
    expect(first.startsWith("s1:")).toBe(true);
    expect(second.startsWith("s1:")).toBe(true);
    expect(otherSession.startsWith("s2:")).toBe(true);
    expect(new Set([first, second, otherSession]).size).toBe(3);
    expect(second > first).toBe(true);
  });
});

describe("bound results", () => {
  it("the newest attempt's bound result is the displayed result", () => {
    let session = createSession("s", "walk", SHA);
    const attemptId = newAttemptId("s");
    session = startAttemptAt(session, attemptId, SHA, 1_000);
    session = applyOutcome(session, attemptId, resultOutcome(attemptId, SHA, SHA));
    expect(attemptOf(session, attemptId).status).toBe("bound");
    expect(session.currentResult?.stats.input_sha256).toBe(SHA);
    expect(session.witnesses).toEqual([]);
  });
});

describe("stale outcomes", () => {
  it("a late outcome for a superseded attempt is witnessed, not applied", () => {
    let session = createSession("s", "walk", SHA);
    const first = newAttemptId("s");
    const second = newAttemptId("s");
    session = startAttemptAt(session, first, SHA, 1_000);
    // A retry with re-edited scene bytes supersedes the first attempt.
    const editedSha = "ee".repeat(32);
    session = startAttemptAt(session, second, editedSha, 1_010);
    expect(attemptOf(session, first).status).toBe("stale"); // demoted by the new attempt

    session = applyOutcome(session, first, resultOutcome(first, SHA, SHA));
    const attempt = attemptOf(session, first);
    expect(attempt.status).toBe("stale");
    expect(attempt.outcome?.type).toBe("result"); // recorded in history with its own hash
    expect(session.witnesses).toEqual([
      { kind: "stale_outcome", attemptId: first, newestAttemptId: second },
    ]);
    expect(session.currentResult).toBeNull();
  });

  it("a stale arrival does not strip the newest attempt's bound result", () => {
    let session = createSession("s", "walk", SHA);
    const first = newAttemptId("s");
    const second = newAttemptId("s");
    session = startAttemptAt(session, first, SHA, 1_000);
    session = applyOutcome(session, first, resultOutcome(first, SHA, SHA));
    const editedSha = "ee".repeat(32);
    session = startAttemptAt(session, second, editedSha, 1_010);
    session = applyOutcome(session, second, resultOutcome(second, editedSha, editedSha));
    expect(session.currentResult?.stats.input_sha256).toBe(editedSha);

    session = applyOutcome(session, first, resultOutcome(first, SHA, SHA));
    expect(session.witnesses).toEqual([
      { kind: "stale_outcome", attemptId: first, newestAttemptId: second },
    ]);
    expect(session.currentResult?.stats.input_sha256).toBe(editedSha);
  });
});

describe("mismatched results", () => {
  it("a result naming other bytes is a witness, never the displayed result", () => {
    let session = createSession("s", "walk", SHA);
    const attemptId = newAttemptId("s");
    session = startAttemptAt(session, attemptId, SHA, 1_000);
    session = applyOutcome(session, attemptId, resultOutcome(attemptId, SHA, MISMATCHED_SHA256));
    const attempt = attemptOf(session, attemptId);
    expect(attempt.status).toBe("mismatched");
    expect(attempt.outcome?.type).toBe("result"); // the answer is kept in the history
    expect(session.witnesses).toEqual([
      { kind: "mismatched_result", attemptId, requestSha256: SHA, resultSha256: MISMATCHED_SHA256 },
    ]);
    expect(session.currentResult).toBeNull();
  });
});

describe("unparseable results", () => {
  it("a 200 whose body is not a result is witnessed as such, never displayed", () => {
    let session = createSession("s", "walk", SHA);
    const attemptId = newAttemptId("s");
    session = startAttemptAt(session, attemptId, SHA, 1_000);
    const outcome = outcomeFromBody(responseReceipt(attemptId), { hello: "world" }, SHA);
    expect(outcome.type).toBe("unparseable_result");
    session = applyOutcome(session, attemptId, outcome);
    expect(attemptOf(session, attemptId).status).toBe("failed");
    expect(session.witnesses).toEqual([
      { kind: "unparseable_result", attemptId, reason: "wrong_version" },
    ]);
    expect(session.currentResult).toBeNull();
  });

  it("a 200 with no body at all is witnessed as missing, never displayed", () => {
    let session = createSession("s", "walk", SHA);
    const attemptId = newAttemptId("s");
    session = startAttemptAt(session, attemptId, SHA, 1_000);
    const outcome = outcomeFromBody(responseReceipt(attemptId), null, SHA);
    expect(outcome.type).toBe("unparseable_result");
    session = applyOutcome(session, attemptId, outcome);
    expect(session.witnesses).toEqual([
      { kind: "unparseable_result", attemptId, reason: "not_object" },
    ]);
    expect(session.currentResult).toBeNull();
  });
});

describe("refusals, faults and cancellations", () => {
  it("a refused attempt records the envelope code and strips the display", () => {
    let session = createSession("s", "walk", SHA);
    const attemptId = newAttemptId("s");
    session = startAttemptAt(session, attemptId, SHA, 1_000);
    const failure: PlacementFailure = {
      kind: "server_error",
      status: 422,
      envelope: {
        code: "invalid_scene",
        message: "The scene has no meter object.",
        path: "/meter",
      },
      knownCode: true,
    };
    session = applyOutcome(session, attemptId, {
      type: "refused",
      response: responseReceipt(attemptId),
      failure,
    });
    expect(attemptOf(session, attemptId).status).toBe("refused");
    expect(session.witnesses).toEqual([
      {
        kind: "refused",
        attemptId,
        code: "invalid_scene",
        detail: "The scene has no meter object.",
      },
    ]);
    expect(session.currentResult).toBeNull();
  });

  it("a refusal after a bound attempt keeps the history and strips the display", () => {
    let session = createSession("s", "walk", SHA);
    const first = newAttemptId("s");
    const second = newAttemptId("s");
    session = startAttemptAt(session, first, SHA, 1_000);
    session = applyOutcome(session, first, resultOutcome(first, SHA, SHA));
    expect(session.currentResult).not.toBeNull();

    session = startAttemptAt(session, second, SHA, 1_010);
    expect(session.currentResult).toBeNull(); // a new attempt starts with no answer shown
    const failure: PlacementFailure = { kind: "network_error", detail: "connection reset" };
    session = applyOutcome(session, second, { type: "failed", failure });
    expect(session.currentResult).toBeNull();
    expect(session.attempts).toHaveLength(2);
    expect(attemptOf(session, first).outcome?.type).toBe("result"); // history intact
  });

  it("cancellation records the state but no witness", () => {
    let session = createSession("s", "walk", SHA);
    const attemptId = newAttemptId("s");
    session = startAttemptAt(session, attemptId, SHA, 1_000);
    session = applyOutcome(session, attemptId, { type: "cancelled" });
    expect(attemptOf(session, attemptId).status).toBe("cancelled");
    expect(session.witnesses).toEqual([]);
    expect(session.currentResult).toBeNull();
  });
});

describe("result intake witnesses", () => {
  const bodyOf = () => JSON.parse(JSON.stringify(resultFixture(SHA))) as Record<string, unknown>;

  it.each([null, 42, "result", []])("refuses a body that is not a JSON object (%j)", (value) => {
    const intake = intakeResultBody(value);
    expect(intake.ok).toBe(false);
    if (!intake.ok) {
      expect(intake.failure.reason).toBe("not_object");
    }
  });

  it("refuses a wrong schema_version and names both versions", () => {
    const body = bodyOf();
    body.schema_version = "2.0";
    const intake = intakeResultBody(body);
    expect(intake.ok).toBe(false);
    if (!intake.ok) {
      expect(intake.failure.reason).toBe("wrong_version");
      expect(intake.failure.detail).toContain("2.0");
      expect(intake.failure.detail).toContain("1.0");
    }
  });

  it("refuses a result missing any required field, naming it", () => {
    for (const field of REQUIRED_RESULT_FIELDS) {
      // A missing schema_version is a wrong version, not a missing field; covered above.
      if (field === "schema_version") continue;
      const stripped = bodyOf();
      Reflect.deleteProperty(stripped, field);
      const intake = intakeResultBody(stripped);
      expect(intake.ok).toBe(false);
      if (!intake.ok) {
        expect(intake.failure.reason).toBe("missing_required");
        expect(intake.failure.detail).toContain(field);
      }
    }
  });

  it("refuses a result whose input_sha256 cannot bind the answer to an upload", () => {
    const body = bodyOf();
    const stats = body.stats as Record<string, unknown>;
    stats.input_sha256 = "nothex";
    const intake = intakeResultBody(body);
    expect(intake.ok).toBe(false);
    if (!intake.ok) {
      expect(intake.failure.reason).toBe("wrong_shape");
      expect(intake.failure.detail).toContain("SHA-256");
    }
  });

  it("refuses a decision outside the contract", () => {
    const body = bodyOf();
    body.decision = "maybe";
    const intake = intakeResultBody(body);
    expect(intake.ok).toBe(false);
    if (!intake.ok) {
      expect(intake.failure.reason).toBe("wrong_shape");
      expect(intake.failure.detail).toContain("decision");
    }
  });

  it("notes older-server absences instead of calling them damage", () => {
    const stripped = bodyOf();
    const policy = stripped.policy as Record<string, unknown>;
    Reflect.deleteProperty(policy, "allow_reject");
    Reflect.deleteProperty(policy, "notice");
    Reflect.deleteProperty(stripped, "nearest_considered");
    Reflect.deleteProperty(stripped, "objects_not_used");
    const route = stripped.route as Record<string, unknown>;
    Reflect.deleteProperty(route, "length_is_lower_bound");
    const intake = intakeResultBody(stripped);
    expect(intake.ok).toBe(true);
    if (!intake.ok) return;
    expect(intake.additiveNotes).toHaveLength(5);
    const notes = intake.additiveNotes.join("\n");
    expect(notes).toContain("allow_reject");
    expect(notes).toContain("notice");
    expect(notes).toContain("nearest_considered");
    expect(notes).toContain("objects_not_used");
    expect(notes).toContain("length_is_lower_bound");
  });

  it("binding compares the request hash with the result's own report", () => {
    const parsed = intakeResultBody(bodyOf());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(bindResultToRequest(parsed.result, SHA)).toEqual({ kind: "bound" });
    expect(bindResultToRequest(parsed.result, MISMATCHED_SHA256)).toEqual({
      kind: "mismatched",
      requestSha256: MISMATCHED_SHA256,
      resultSha256: SHA,
    });
  });
});
