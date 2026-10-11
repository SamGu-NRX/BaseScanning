// Session persistence and the redacted export/import record. One payload shape serves
// both the browser-local storage slot and the user-initiated JSON export:
//
//   { format, version, savedAt, scene: { fileName, text }, session: SessionRecord }
//
// The scene's exact text is the payload's byte carrier: re-intaking it reproduces the
// upload bytes and their SHA-256 deterministically, so a reopened session can submit a
// new attempt bound to the same bytes.
//
// The redaction boundary is structural, not a filter: this payload type has no field a
// credential could occupy. The bearer token lives in the store's private memory only
// (never in any snapshot or receipt), so nothing here can ever write it — there is no
// path for it. Receipt URLs keep the server origin (they are the evidence of what was
// sent); the client never records request or response headers at all.
//
// Restore is defensive: parseSessionPayload validates and REBUILDS the payload from
// known fields only (unknown fields are dropped, so a hostile or hand-edited export
// cannot smuggle state in), re-intakes the scene through the same intake a fresh file
// takes, and rehydrates the session: an attempt recorded as "submitting" can never
// settle (the page closed mid-flight), so it restores as cancelled, and currentResult
// is recomputed from the attempts instead of trusting the stored field.

import type { PlacementFailure } from "../contract/errors.ts";
import type { ResultBinding } from "../contract/result.ts";
import {
  bindResultToRequest,
  intakeResultBody,
  type ResultIntakeFailure,
} from "../contract/result.ts";
import type {
  AttemptOutcome,
  RequestReceipt,
  ResponseReceipt,
  SessionRecord,
  SessionWitness,
  SubmissionAttempt,
  SubmissionStatus,
} from "../contract/session.ts";

/** The storage slot this app uses for its one saved session. */
export const SESSION_STORAGE_KEY = "basescanning-web.session.v1";

export const PERSIST_FORMAT = "basescanning-session-record";
export const PERSIST_VERSION = 1;

/** The redacted record: the only shape storage and the export ever carry. */
export interface PersistedSessionPayload {
  readonly format: typeof PERSIST_FORMAT;
  readonly version: typeof PERSIST_VERSION;
  readonly savedAt: number;
  readonly scene: {
    readonly fileName: string;
    /** The file's exact text — re-intaking it reproduces the upload bytes and hash. */
    readonly text: string;
  };
  readonly session: SessionRecord;
}

/** What the page shows about a saved record before the user chooses to reopen it. */
export interface SavedSessionSummary {
  readonly sessionId: string;
  readonly label: string;
  readonly sceneFileName: string;
  readonly attemptCount: number;
  readonly savedAt: number;
}

/** The storage surface the store persists through; localStorage satisfies this. */
export interface SessionPersistence {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** localStorage when the environment has one, null where persistence is unavailable. */
export function defaultSessionPersistence(): SessionPersistence | null {
  try {
    return typeof globalThis.localStorage === "undefined" ? null : globalThis.localStorage;
  } catch {
    return null;
  }
}

export function buildSessionPayload(
  fileName: string,
  sceneText: string,
  session: SessionRecord,
  savedAt: number = Date.now(),
): PersistedSessionPayload {
  return {
    format: PERSIST_FORMAT,
    version: PERSIST_VERSION,
    savedAt,
    scene: { fileName, text: sceneText },
    session,
  };
}

export function savedSessionSummaryOf(payload: PersistedSessionPayload): SavedSessionSummary {
  return {
    sessionId: payload.session.sessionId,
    label: payload.session.label,
    sceneFileName: payload.scene.fileName,
    attemptCount: payload.session.attempts.length,
    savedAt: payload.savedAt,
  };
}

export interface RestoreFailure {
  readonly code:
    | "invalid_json"
    | "wrong_format"
    | "wrong_version"
    | "invalid_scene_block"
    | "invalid_session_block"
    | "invalid_attempt"
    | "invalid_outcome"
    | "invalid_witness";
  readonly message: string;
}

export type ParseResult =
  | { readonly ok: true; readonly payload: PersistedSessionPayload }
  | { readonly ok: false; readonly failure: RestoreFailure };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isSha256Hex(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function requestReceiptOf(raw: unknown): RequestReceipt | null {
  if (!isRecord(raw)) return null;
  const attemptId = raw.attemptId;
  const url = raw.url;
  const method = raw.method;
  const contentType = raw.contentType;
  if (
    typeof attemptId !== "string" ||
    typeof url !== "string" ||
    typeof method !== "string" ||
    typeof contentType !== "string" ||
    !isFiniteNumber(raw.byteLength) ||
    !isSha256Hex(raw.requestSha256) ||
    !isFiniteNumber(raw.startedAt)
  ) {
    return null;
  }
  return {
    attemptId,
    url,
    method,
    contentType,
    byteLength: raw.byteLength,
    requestSha256: raw.requestSha256,
    startedAt: raw.startedAt,
  };
}

function responseReceiptOf(raw: unknown): ResponseReceipt | null {
  if (!isRecord(raw)) return null;
  if (
    typeof raw.attemptId !== "string" ||
    !isFiniteNumber(raw.status) ||
    typeof raw.contentType !== "string" ||
    !isFiniteNumber(raw.bodyByteLength) ||
    !(raw.bodySha256 === null || isSha256Hex(raw.bodySha256)) ||
    !(raw.bodyExcerpt === null || typeof raw.bodyExcerpt === "string") ||
    typeof raw.bodyTruncated !== "boolean" ||
    !isFiniteNumber(raw.elapsedMs) ||
    !isFiniteNumber(raw.finishedAt)
  ) {
    return null;
  }
  return {
    attemptId: raw.attemptId,
    status: raw.status,
    contentType: raw.contentType,
    bodyByteLength: raw.bodyByteLength,
    bodySha256: raw.bodySha256,
    bodyExcerpt: raw.bodyExcerpt,
    bodyTruncated: raw.bodyTruncated,
    elapsedMs: raw.elapsedMs,
    finishedAt: raw.finishedAt,
  };
}

/** Server-side failures an outcome may carry: the envelope, a non-envelope answer, or no answer. */
function serverFailureOf(raw: unknown): PlacementFailure | null {
  if (!isRecord(raw)) return null;
  if (raw.kind === "server_error") {
    const envelope = raw.envelope;
    if (
      !isRecord(envelope) ||
      typeof envelope.code !== "string" ||
      typeof envelope.message !== "string" ||
      !(envelope.path === null || typeof envelope.path === "string") ||
      !isFiniteNumber(raw.status) ||
      typeof raw.knownCode !== "boolean"
    ) {
      return null;
    }
    return {
      kind: "server_error",
      status: raw.status,
      envelope: { code: envelope.code, message: envelope.message, path: envelope.path },
      knownCode: raw.knownCode,
    };
  }
  if (raw.kind === "unparseable_error") {
    if (!isFiniteNumber(raw.status) || typeof raw.detail !== "string") return null;
    return { kind: "unparseable_error", status: raw.status, detail: raw.detail };
  }
  if (raw.kind === "network_error") {
    return typeof raw.detail === "string" ? { kind: "network_error", detail: raw.detail } : null;
  }
  return null;
}

/** The intake failure a recorded "unparseable_result" outcome carries. */
const INTAKE_FAILURE_REASONS = [
  "not_json",
  "not_object",
  "wrong_version",
  "missing_required",
  "wrong_shape",
];

function resultIntakeFailureOf(raw: unknown): { reason: string; detail: string } | null {
  if (
    !isRecord(raw) ||
    !INTAKE_FAILURE_REASONS.includes(raw.reason as string) ||
    typeof raw.detail !== "string"
  ) {
    return null;
  }
  return { reason: raw.reason as string, detail: raw.detail };
}

/**
 * Rebuilds one attempt outcome from the record. A stored result is re-validated by the
 * same intake a live 200 body takes (intakeResultBody), and its binding is re-derived
 * from the hashes — the data decides "bound" or "mismatched", not the stored label.
 */
function outcomeOf(raw: unknown, requestSha256: string): AttemptOutcome | null {
  if (!isRecord(raw)) return null;
  switch (raw.type) {
    case "result": {
      const response = responseReceiptOf(raw.response);
      if (response === null) return null;
      const intake = intakeResultBody(raw.result);
      if (!intake.ok) return null;
      const binding: ResultBinding = bindResultToRequest(intake.result, requestSha256);
      return {
        type: "result",
        response,
        result: intake.result,
        binding,
        additiveNotes: intake.additiveNotes,
      };
    }
    case "unparseable_result": {
      const response = responseReceiptOf(raw.response);
      const failure = resultIntakeFailureOf(raw.failure);
      if (response === null || failure === null) return null;
      return {
        type: "unparseable_result",
        response,
        failure: { reason: failure.reason, detail: failure.detail } as ResultIntakeFailure,
      };
    }
    case "refused": {
      const response = responseReceiptOf(raw.response);
      const failure = serverFailureOf(raw.failure);
      if (response === null || failure === null) return null;
      if (failure.kind !== "server_error" && failure.kind !== "unparseable_error") return null;
      return { type: "refused", response, failure };
    }
    case "failed": {
      const failure = serverFailureOf(raw.failure);
      if (failure === null) return null;
      return { type: "failed", failure };
    }
    case "cancelled":
      return { type: "cancelled" };
    default:
      return null;
  }
}

const STATUSES: readonly SubmissionStatus[] = [
  "submitting",
  "bound",
  "mismatched",
  "refused",
  "failed",
  "cancelled",
  "stale",
];

/**
 * Re-derives an attempt's status from its outcome, mirroring the live store's
 * applyOutcome switch. The stored label is never trusted where it can promote
 * something to the display: only the outcome's data decides "bound" or
 * "mismatched". A stale label survives only as the "superseded mid-flight" marker
 * the live store itself would have left.
 */
function rederiveStatus(
  stored: SubmissionStatus,
  outcome: AttemptOutcome | null,
): SubmissionStatus | null {
  if (outcome === null) {
    // No outcome is only consistent with an attempt still mid-flight ("submitting"),
    // one superseded before an answer arrived ("stale"), or an already-cancelled one.
    if (stored === "submitting" || stored === "stale" || stored === "cancelled") {
      return stored;
    }
    return null;
  }
  switch (outcome.type) {
    case "result":
      if (outcome.binding.kind !== "bound") return "mismatched";
      return stored === "stale" ? "stale" : "bound";
    case "unparseable_result":
      return "failed";
    case "refused":
      return "refused";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
  }
}

function attemptOf(raw: unknown): SubmissionAttempt | null {
  if (!isRecord(raw)) return null;
  const request = requestReceiptOf(raw.request);
  if (request === null) return null;
  if (typeof raw.attemptId !== "string" || !isFiniteNumber(raw.startedAt)) return null;
  if (!STATUSES.includes(raw.status as SubmissionStatus)) return null;
  const storedStatus = raw.status as SubmissionStatus;
  // A "submitting" attempt is mid-flight by definition: a record that claims an outcome
  // for it is inconsistent and refused rather than guessed at.
  if (storedStatus === "submitting" && raw.outcome !== null) return null;
  let outcome: AttemptOutcome | null = null;
  if (raw.outcome !== null) {
    outcome = outcomeOf(raw.outcome, request.requestSha256);
    if (outcome === null) return null;
  }
  const status = rederiveStatus(storedStatus, outcome);
  if (status === null) return null;
  return {
    attemptId: raw.attemptId,
    startedAt: raw.startedAt,
    requestSha256: request.requestSha256,
    request,
    status,
    outcome,
  };
}

function witnessOf(raw: unknown): SessionWitness | null {
  if (!isRecord(raw)) return null;
  switch (raw.kind) {
    case "mismatched_result":
      if (
        typeof raw.attemptId !== "string" ||
        !isSha256Hex(raw.requestSha256) ||
        !isSha256Hex(raw.resultSha256)
      ) {
        return null;
      }
      return {
        kind: "mismatched_result",
        attemptId: raw.attemptId,
        requestSha256: raw.requestSha256,
        resultSha256: raw.resultSha256,
      };
    case "stale_outcome":
      if (typeof raw.attemptId !== "string" || typeof raw.newestAttemptId !== "string") return null;
      return {
        kind: "stale_outcome",
        attemptId: raw.attemptId,
        newestAttemptId: raw.newestAttemptId,
      };
    case "unparseable_result":
      if (typeof raw.attemptId !== "string" || typeof raw.reason !== "string") return null;
      return { kind: "unparseable_result", attemptId: raw.attemptId, reason: raw.reason };
    case "refused":
      if (
        typeof raw.attemptId !== "string" ||
        typeof raw.code !== "string" ||
        typeof raw.detail !== "string"
      ) {
        return null;
      }
      return { kind: "refused", attemptId: raw.attemptId, code: raw.code, detail: raw.detail };
    default:
      return null;
  }
}

/**
 * Parses stored or exported text into the payload, rebuilding every known field and
 * dropping everything else. The scene text is checked for shape only here; the full
 * scene intake runs at restore time and reports precise refusals.
 */
export function parseSessionPayload(raw: string): ParseResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw) as unknown;
  } catch (error) {
    return {
      ok: false,
      failure: {
        code: "invalid_json",
        message: `The record is not valid JSON: ${(error as Error).message}`,
      },
    };
  }
  if (!isRecord(parsed) || parsed.format !== PERSIST_FORMAT) {
    return {
      ok: false,
      failure: {
        code: "wrong_format",
        message: `The record is not a ${PERSIST_FORMAT} payload.`,
      },
    };
  }
  if (parsed.version !== PERSIST_VERSION) {
    return {
      ok: false,
      failure: {
        code: "wrong_version",
        message: `The record names version ${JSON.stringify(parsed.version)}; this build reads ${PERSIST_VERSION} only.`,
      },
    };
  }
  const scene = parsed.scene;
  if (!isRecord(scene) || typeof scene.fileName !== "string" || typeof scene.text !== "string") {
    return {
      ok: false,
      failure: {
        code: "invalid_scene_block",
        message: "The record's scene block is missing a file name or the scene's exact text.",
      },
    };
  }
  const stored = parsed.session;
  if (
    !isRecord(stored) ||
    typeof stored.sessionId !== "string" ||
    stored.sessionId.length === 0 ||
    typeof stored.label !== "string" ||
    !isFiniteNumber(stored.createdAt) ||
    !isSha256Hex(stored.sceneSha256) ||
    !Array.isArray(stored.attempts) ||
    !Array.isArray(stored.witnesses)
  ) {
    return {
      ok: false,
      failure: {
        code: "invalid_session_block",
        message:
          "The record's session block is missing its id, label, timestamps, attempts or witnesses.",
      },
    };
  }
  const attempts: SubmissionAttempt[] = [];
  for (const rawAttempt of stored.attempts) {
    const attempt = attemptOf(rawAttempt);
    if (attempt === null) {
      return {
        ok: false,
        failure: {
          code: "invalid_attempt",
          message: "The record's attempt history contains an entry this build cannot read.",
        },
      };
    }
    attempts.push(attempt);
  }
  const witnesses: SessionWitness[] = [];
  for (const rawWitness of stored.witnesses) {
    const witness = witnessOf(rawWitness);
    if (witness === null) {
      return {
        ok: false,
        failure: {
          code: "invalid_witness",
          message: "The record's witness list contains an entry this build cannot read.",
        },
      };
    }
    witnesses.push(witness);
  }
  return {
    ok: true,
    payload: {
      format: PERSIST_FORMAT,
      version: PERSIST_VERSION,
      savedAt: isFiniteNumber(parsed.savedAt) ? parsed.savedAt : 0,
      scene: { fileName: scene.fileName, text: scene.text },
      // currentResult is deliberately not read from the record: restore recomputes it
      // from the attempts (see rehydrateSession), so a stale or doctored field is inert.
      session: {
        sessionId: stored.sessionId,
        label: stored.label,
        createdAt: stored.createdAt,
        sceneSha256: stored.sceneSha256,
        attempts,
        currentResult: null,
        witnesses,
      },
    },
  };
}

/**
 * Turns a validated payload into a live SessionRecord. Two normalizations, both honest
 * restatements of what the data can mean: an attempt that was mid-flight when the page
 * closed can never receive its outcome, so it restores as cancelled (its request
 * receipt stays); and the displayed result is recomputed from the newest attempt, so
 * the record's stored currentResult field is never trusted.
 */
export function rehydrateSession(payload: PersistedSessionPayload): SessionRecord {
  const attempts = payload.session.attempts.map((attempt) =>
    attempt.status === "submitting"
      ? {
          ...attempt,
          status: "cancelled" as const,
          // The interrupted attempt gains the same cancelled outcome the live
          // cancel path records (applyOutcome), so history reads the same either way.
          outcome: { type: "cancelled" } as const,
        }
      : attempt,
  );
  const newest = attempts.at(-1);
  const currentResult =
    newest !== undefined && newest.status === "bound" && newest.outcome?.type === "result"
      ? newest.outcome.result
      : null;
  return { ...payload.session, attempts, currentResult };
}
