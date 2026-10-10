// Session identity and outcome association, pure: no DOM, no storage, no fetch — the
// store and page layers (and their tests) build on this. A session is a named container
// for one scene's inspection life; each submission attempt is identified by an explicit
// attempt id and the SHA-256 of the exact bytes it sent. Three witnesses are handled
// here and tested below-by-contract:
//
// - stale: an outcome for a superseded attempt (a retry happened, or a duplicate
//   response landed late) is recorded but never applied over newer state — the HLD's
//   "validate session and input revision before applying it; never overwrite newer
//   state blindly".
// - mismatched: a result whose stats.input_sha256 names different bytes than the
//   request sent is not this submission's answer. Recorded as a witness, never shown.
// - missing: a 200 whose body is not a parseable result (or the response never parses)
//   is recorded as such, again never shown as an answer.
//
// A refused, failed or cancelled attempt also strips the displayed result: a partial or
// refused round of processing must not leave yesterday's answer presented as current.
// The previous answer stays in the attempt history, labelled with its own hash.

import type { PlacementFailure } from "./errors.ts";
import {
  bindResultToRequest,
  intakeResultBody,
  type ResultBinding,
  type ResultIntakeFailure,
} from "./result.ts";
import type { PlacementResult } from "./types.ts";

export type SubmissionStatus =
  | "submitting"
  | "bound"
  /** The server answered 200 with a parseable result that names different bytes. */
  | "mismatched"
  | "refused"
  | "failed"
  | "cancelled"
  | "stale";

/** Bounded receipt of what was actually sent, for the committed evidence trail. */
export interface RequestReceipt {
  readonly attemptId: string;
  readonly url: string;
  readonly method: string;
  readonly contentType: string;
  readonly byteLength: number;
  readonly requestSha256: string;
  readonly startedAt: number;
}

/** Bounded receipt of what actually came back (body bytes capped by the client). */
export interface ResponseReceipt {
  readonly attemptId: string;
  readonly status: number;
  readonly contentType: string;
  readonly bodyByteLength: number;
  readonly bodySha256: string | null;
  readonly bodyExcerpt: string | null;
  /** True when the body exceeded the client's cap and was not read to the end. */
  readonly bodyTruncated: boolean;
  readonly elapsedMs: number;
  readonly finishedAt: number;
}

export type AttemptOutcome =
  | {
      readonly type: "result";
      readonly response: ResponseReceipt;
      readonly result: PlacementResult;
      readonly binding: ResultBinding;
      readonly additiveNotes: readonly string[];
    }
  | {
      readonly type: "unparseable_result";
      readonly response: ResponseReceipt;
      readonly failure: ResultIntakeFailure;
    }
  | {
      readonly type: "refused";
      readonly response: ResponseReceipt;
      readonly failure: PlacementFailure;
    }
  | { readonly type: "failed"; readonly failure: PlacementFailure }
  | { readonly type: "cancelled" };

export interface SubmissionAttempt {
  readonly attemptId: string;
  readonly startedAt: number;
  readonly requestSha256: string;
  readonly request: RequestReceipt;
  readonly status: SubmissionStatus;
  readonly outcome: AttemptOutcome | null;
}

export interface SessionRecord {
  readonly sessionId: string;
  readonly label: string;
  readonly createdAt: number;
  readonly sceneSha256: string;
  readonly attempts: readonly SubmissionAttempt[];
  /**
   * The one result currently presented, or null. Derived once here so every caller
   * agrees: the result of the newest attempt, and only when that attempt is bound.
   */
  readonly currentResult: PlacementResult | null;
  /** Witnesses observed on this session: mismatches, stale arrivals, unparseable bodies. */
  readonly witnesses: readonly SessionWitness[];
}

export type SessionWitness =
  | {
      readonly kind: "mismatched_result";
      readonly attemptId: string;
      readonly requestSha256: string;
      readonly resultSha256: string;
    }
  | { readonly kind: "stale_outcome"; readonly attemptId: string; readonly newestAttemptId: string }
  | { readonly kind: "unparseable_result"; readonly attemptId: string; readonly reason: string }
  | {
      readonly kind: "refused";
      readonly attemptId: string;
      readonly code: string;
      readonly detail: string;
    };

let attemptCounter = 0;

/** Explicit attempt identity: monotonic, session-scoped, never reused. */
export function newAttemptId(sessionId: string): string {
  attemptCounter += 1;
  return `${sessionId}:attempt-${attemptCounter.toString(36).padStart(4, "0")}`;
}

export function createSession(
  sessionId: string,
  label: string,
  sceneSha256: string,
): SessionRecord {
  return {
    sessionId,
    label,
    createdAt: Date.now(),
    sceneSha256,
    attempts: [],
    currentResult: null,
    witnesses: [],
  };
}

export function startAttempt(
  session: SessionRecord,
  attemptId: string,
  request: RequestReceipt,
  now: number = Date.now(),
): SessionRecord {
  // Starting any attempt demotes every older one: only the newest submission is current.
  const demoted = session.attempts.map((attempt) =>
    attempt.status === "submitting"
      ? { ...attempt, status: "stale" as const, outcome: attempt.outcome }
      : attempt,
  );
  return {
    ...session,
    attempts: [
      ...demoted,
      {
        attemptId,
        startedAt: now,
        requestSha256: request.requestSha256,
        request,
        status: "submitting",
        outcome: null,
      },
    ],
    currentResult: null,
  };
}

function newestAttemptId(session: SessionRecord): string | null {
  return session.attempts.length > 0
    ? (session.attempts[session.attempts.length - 1]?.attemptId ?? null)
    : null;
}

/**
 * Applies an outcome to one attempt. Outcomes for superseded attempts are witnessed as
 * stale and left unapplied. Only a bound result of the newest attempt becomes the
 * displayed result; a mismatched result is a witness, never a display.
 */
export function applyOutcome(
  session: SessionRecord,
  attemptId: string,
  outcome: AttemptOutcome,
): SessionRecord {
  const newest = newestAttemptId(session);
  const isStaleArrival = attemptId !== newest;

  const witness: SessionWitness | null = (() => {
    if (isStaleArrival && outcome.type !== "cancelled") {
      return { kind: "stale_outcome", attemptId, newestAttemptId: newest ?? "" };
    }
    if (outcome.type === "result" && outcome.binding.kind === "mismatched") {
      return {
        kind: "mismatched_result",
        attemptId,
        requestSha256: outcome.binding.requestSha256,
        resultSha256: outcome.binding.resultSha256,
      };
    }
    if (outcome.type === "unparseable_result") {
      return { kind: "unparseable_result", attemptId, reason: outcome.failure.reason };
    }
    if (outcome.type === "refused") {
      return {
        kind: "refused",
        attemptId,
        code:
          outcome.failure.kind === "server_error"
            ? outcome.failure.envelope.code
            : outcome.failure.kind,
        detail:
          outcome.failure.kind === "server_error"
            ? outcome.failure.envelope.message
            : "The server did not answer with the contract's envelope.",
      };
    }
    return null;
  })();

  const attempts = session.attempts.map((attempt) => {
    if (attempt.attemptId !== attemptId) {
      return attempt;
    }
    if (isStaleArrival && outcome.type !== "cancelled") {
      if (attempt.status === "submitting") {
        return { ...attempt, status: "stale" as const, outcome };
      }
      // Already stale: keep that status, but still record the late outcome in history.
      return attempt.status === "stale" ? { ...attempt, outcome } : attempt;
    }
    switch (outcome.type) {
      case "result":
        // "bound" only when the result answers the exact bytes this attempt sent; a
        // mismatched answer is a witness (recorded above), never the displayed result.
        return {
          ...attempt,
          status: outcome.binding.kind === "bound" ? ("bound" as const) : ("mismatched" as const),
          outcome,
        };
      case "unparseable_result":
        return { ...attempt, status: "failed" as const, outcome };
      case "refused":
        return { ...attempt, status: "refused" as const, outcome };
      case "failed":
        return { ...attempt, status: "failed" as const, outcome };
      case "cancelled":
        return { ...attempt, status: "cancelled" as const, outcome };
      default:
        return attempt;
    }
  });

  const current = attempts[attempts.length - 1];
  const updated: SessionRecord = {
    ...session,
    attempts,
    currentResult:
      current !== undefined && current.status === "bound" && current.outcome?.type === "result"
        ? current.outcome.result
        : null,
    witnesses: witness === null ? session.witnesses : [...session.witnesses, witness],
  };
  return updated;
}

/** Convenience used by the client layer: parse a body and produce the matching outcome. */
export function outcomeFromBody(
  response: ResponseReceipt,
  body: unknown,
  requestSha256: string,
): AttemptOutcome {
  const intake = intakeResultBody(body);
  if (!intake.ok) {
    return { type: "unparseable_result", response, failure: intake.failure };
  }
  const binding = bindResultToRequest(intake.result, requestSha256);
  return {
    type: "result",
    response,
    result: intake.result,
    binding,
    additiveNotes: intake.additiveNotes,
  };
}
