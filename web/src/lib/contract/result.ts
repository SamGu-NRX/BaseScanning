// Result intake and association. Two jobs:
//
// 1. Parse a 200 response body defensively. The result schema is additive — new fields
//    are optional, and an old result missing them still validates — so a missing
//    optional field is tolerated and noted, never treated as damage. A missing REQUIRED
//    field, a wrong schema_version, or a non-object body is not a result at all.
// 2. Bind the parsed result to the submission that produced it. The contract gives the
//    binding: stats.input_sha256 is the hash of the scene bytes as uploaded, which the
//    client computed before sending. A result whose hash names different bytes is a
//    witness of a mismatched answer — recorded, never displayed as this submission's.
//    Whether a late answer is stale (belongs to an older attempt) is the session's call
//    (applyOutcome in session.ts), because ordering is session state, not body state.

import type { PlacementResult } from "./types.ts";
import { RESULT_VERSION } from "./versions.ts";

export type ResultIntakeFailure =
  | { readonly reason: "not_json"; readonly detail: string }
  | { readonly reason: "not_object"; readonly detail: string }
  | { readonly reason: "wrong_version"; readonly detail: string }
  | { readonly reason: "missing_required"; readonly detail: string }
  | { readonly reason: "wrong_shape"; readonly detail: string };

export type ResultIntake =
  | {
      readonly ok: true;
      readonly result: PlacementResult;
      readonly additiveNotes: readonly string[];
    }
  | { readonly ok: false; readonly failure: ResultIntakeFailure };

/**
 * Top-level fields result.schema.json requires, in the schema's order. Exported so the
 * schema-conformance tests can compare this list with the schema file itself and keep
 * intake honest; intake treats the absence of any of them as fatal.
 */
export const REQUIRED_RESULT_FIELDS = [
  "schema_version",
  "decision",
  "summary",
  "reasons",
  "policy",
  "spot",
  "route",
  "checks",
  "missing_evidence",
  "ends",
  "sweep",
  "stats",
] as const;

/**
 * Optional-by-schema fields this build knows how to render. A result from an older
 * server without them is still valid; each absence becomes one additiveNote so the UI
 * can say what the answer did not carry rather than rendering silence.
 */
const ADDITIVE_FIELDS = [
  ["policy", "allow_reject"],
  ["policy", "notice"],
  ["nearest_considered"],
  ["objects_not_used"],
  ["route", "length_is_lower_bound"],
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function intakeResultBody(body: unknown): ResultIntake {
  if (!isRecord(body)) {
    return {
      ok: false,
      failure: { reason: "not_object", detail: "The result body is not a JSON object." },
    };
  }
  if (body.schema_version !== RESULT_VERSION) {
    return {
      ok: false,
      failure: {
        reason: "wrong_version",
        detail: `The result names schema_version ${JSON.stringify(body.schema_version)}; this client reads ${RESULT_VERSION} only.`,
      },
    };
  }
  const missing = REQUIRED_RESULT_FIELDS.filter((field) => !(field in body));
  if (missing.length > 0) {
    return {
      ok: false,
      failure: {
        reason: "missing_required",
        detail: `The result is missing required fields: ${missing.join(", ")}.`,
      },
    };
  }
  const stats = body.stats;
  if (
    !isRecord(stats) ||
    typeof stats.input_sha256 !== "string" ||
    stats.input_sha256.length !== 64
  ) {
    return {
      ok: false,
      failure: {
        reason: "wrong_shape",
        detail:
          "The result's stats.input_sha256 is not a SHA-256 hex digest, so the answer cannot be bound to an upload.",
      },
    };
  }
  const decision = body.decision;
  if (decision !== "pass" && decision !== "manual_review" && decision !== "reject") {
    return {
      ok: false,
      failure: {
        reason: "wrong_shape",
        detail: `The result's decision is not pass, manual_review or reject.`,
      },
    };
  }
  const additiveNotes: string[] = [];
  for (const [field, sub] of ADDITIVE_FIELDS) {
    const holder = body[field];
    const absent =
      sub === undefined
        ? holder === undefined
        : !isRecord(holder) || (holder as Record<string, unknown>)[sub] === undefined;
    if (absent) {
      additiveNotes.push(
        sub === undefined
          ? `The result has no ${field} — an older server answer without that additive field.`
          : `The result's ${field} has no ${sub} — an older server answer without that additive field.`,
      );
    }
  }
  return { ok: true, result: body as unknown as PlacementResult, additiveNotes };
}

export type ResultBinding =
  | { readonly kind: "bound" }
  | {
      readonly kind: "mismatched";
      /** The hash the request carried. */
      readonly requestSha256: string;
      /** The hash the result says it answered. */
      readonly resultSha256: string;
    };

/**
 * Binds a parsed result to the request bytes. "bound" only when the result answers the
 * exact bytes this submission sent; "mismatched" is a witness, never a display.
 */
export function bindResultToRequest(result: PlacementResult, requestSha256: string): ResultBinding {
  const resultSha256 = result.stats.input_sha256;
  return resultSha256 === requestSha256
    ? { kind: "bound" }
    : { kind: "mismatched", requestSha256, resultSha256 };
}
