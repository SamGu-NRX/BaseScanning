// The session store: framework-plain state (no React import) that binds the contract
// layer to the page. It owns the one SessionRecord the UI presents and drives the
// operations the page exposes:
//
// - selectSceneFile: read the file's exact text, run scene intake (typed view, the
//   exact upload bytes, their SHA-256, the version report), and open a fresh session
//   for it. A selection while a request is in flight aborts that request; the
//   abandoned attempt's late completion finds a different session id and is dropped,
//   never applied across sessions.
// - submit: startAttempt (which demotes any older submitting attempt), then POST the
//   exact bytes through PlacementClient under an AbortSignal, then applyOutcome
//   whatever came back: a bound result, a refusal, a transport failure, or a
//   cancellation.
// - cancel: abort the in-flight attempt. The settled report records a cancelled
//   outcome on the attempt, alongside the receipt of what was sent.
//
// What the panel shows is derived in one place, displayedView: only the newest
// attempt's outcome is ever presented, and its result only when bound. Stale
// arrivals, mismatched answers and unparseable bodies are recorded as witnesses on
// the attempts that produced them — never displayed as the current answer. The
// bearer token lives in memory for the next submission only: never persisted, never
// logged, absent from every snapshot and receipt.
//
// M3 adds the record's lifecycle beyond the page's lifetime: every change re-saves
// the redacted record browser-locally (scene bytes included; the token and headers
// excluded by construction), the page offers reopening or discarding the saved
// record, and exportSession/importSession move the same redacted shape as JSON.
// Restore re-validates everything — the scene is re-intaked from its exact bytes and
// the current result is re-derived from the attempt history — so a doctored record
// fails closed instead of displaying what its author claimed. Reopening never
// restores a token: a reopened session submits unauthenticated until a new one is
// entered.

import { PlacementClient, type SubmitReport } from "../contract/client.ts";
import type { PlacementFailure } from "../contract/errors.ts";
import {
  intakeSceneText,
  type SceneIntake,
  SceneIntakeError,
  type SceneIntakeIssue,
} from "../contract/scene.ts";
import {
  type AttemptOutcome,
  applyOutcome,
  createSession,
  newAttemptId,
  type SessionRecord,
  startAttempt,
} from "../contract/session.ts";
import type { PlacementResult } from "../contract/types.ts";
import type { SceneVersionReport } from "../contract/versions.ts";
import {
  buildSessionPayload,
  defaultSessionPersistence,
  parseSessionPayload,
  rehydrateSession,
  type SavedSessionSummary,
  SESSION_STORAGE_KEY,
  type SessionPersistence,
  savedSessionSummaryOf,
} from "./persist.ts";

export const DEFAULT_BASE_URL = "http://localhost:8000";

/** Anything a file picker hands over: a DOM File, or a test stub with the same shape. */
export interface SceneFileInput {
  readonly name: string;
  text(): Promise<string>;
}

/** What the page shows about the selected scene, once intake has succeeded. */
export interface SceneSummary {
  readonly fileName: string;
  readonly sha256: string;
  readonly byteLength: number;
  readonly version: SceneVersionReport;
  readonly warnings: readonly string[];
}

/**
 * The one view the result panel is allowed to render. It is derived only from the
 * newest attempt, so an older attempt's outcome cannot reach the panel through it.
 */
export type DisplayView =
  | { readonly kind: "no_scene" }
  | { readonly kind: "scene_ready" }
  | { readonly kind: "submitting"; readonly attemptId: string; readonly startedAt: number }
  | {
      readonly kind: "bound";
      readonly attemptId: string;
      readonly result: PlacementResult;
      readonly additiveNotes: readonly string[];
    }
  | {
      readonly kind: "mismatched";
      readonly attemptId: string;
      readonly requestSha256: string;
      readonly resultSha256: string;
    }
  | { readonly kind: "refused"; readonly attemptId: string; readonly failure: PlacementFailure }
  | { readonly kind: "failed"; readonly attemptId: string; readonly failure: PlacementFailure }
  | { readonly kind: "unparseable_result"; readonly attemptId: string; readonly detail: string }
  | { readonly kind: "cancelled"; readonly attemptId: string }
  | { readonly kind: "superseded"; readonly attemptId: string };

export interface SessionStoreState {
  readonly baseUrl: string;
  /** True when a token is held in memory; the token itself is never in the snapshot. */
  readonly hasToken: boolean;
  readonly scene: SceneSummary | null;
  /** Why the last selection was refused locally (unreadable, bad JSON, wrong shape). */
  readonly intakeError: SceneIntakeIssue | null;
  readonly session: SessionRecord | null;
  readonly display: DisplayView;
  /** Summary of the browser-local saved record, offered for reopen; null when none. */
  readonly savedSession: SavedSessionSummary | null;
  /** Why the last reopen or import failed. The previous session stays untouched. */
  readonly restoreError: SceneIntakeIssue | null;
  /** A storage-level problem (quota, corrupt saved record) that is not a restore refusal. */
  readonly storageWarning: string | null;
}

export interface SessionStoreOptions {
  /** Injectable transport (tests use the contract fake). Defaults to globalThis.fetch. */
  readonly fetchImpl?: typeof fetch;
  readonly baseUrl?: string;
  /**
   * Injectable browser-local storage for the saved session (tests use an in-memory
   * map). Defaults to localStorage when the environment has one; pass null to disable
   * persistence.
   */
  readonly storage?: SessionPersistence | null;
}

/** Maps a client report onto the outcome the session layer applies. */
function outcomeFromReport(report: SubmitReport): AttemptOutcome {
  if (report.ok) {
    return report.outcome;
  }
  const { failure } = report;
  if (failure.kind === "aborted") {
    return { type: "cancelled" };
  }
  if (failure.kind === "network_error") {
    return { type: "failed", failure };
  }
  // server_error / unparseable_error: the server answered, so a response receipt exists.
  const response = report.exchange.response;
  return response === null ? { type: "failed", failure } : { type: "refused", response, failure };
}

function shortAttemptId(attemptId: string): string {
  const marker = attemptId.indexOf(":");
  return marker === -1 ? attemptId : attemptId.slice(marker + 1);
}

/** The newest attempt's view, or the quiet state before any attempt. */
export function displayedView(session: SessionRecord | null): DisplayView {
  if (session === null) {
    return { kind: "no_scene" };
  }
  const newest = session.attempts.at(-1);
  if (newest === undefined) {
    return { kind: "scene_ready" };
  }
  const attemptId = newest.attemptId;
  const outcome = newest.outcome;
  switch (newest.status) {
    case "submitting":
      return { kind: "submitting", attemptId, startedAt: newest.startedAt };
    case "bound":
      if (outcome?.type === "result") {
        return {
          kind: "bound",
          attemptId,
          result: outcome.result,
          additiveNotes: outcome.additiveNotes,
        };
      }
      break;
    case "mismatched":
      if (outcome?.type === "result" && outcome.binding.kind === "mismatched") {
        return {
          kind: "mismatched",
          attemptId,
          requestSha256: outcome.binding.requestSha256,
          resultSha256: outcome.binding.resultSha256,
        };
      }
      break;
    case "refused":
      if (outcome?.type === "refused") {
        return { kind: "refused", attemptId, failure: outcome.failure };
      }
      break;
    case "failed":
      if (outcome?.type === "unparseable_result") {
        return { kind: "unparseable_result", attemptId, detail: outcome.failure.detail };
      }
      if (outcome?.type === "failed") {
        return { kind: "failed", attemptId, failure: outcome.failure };
      }
      break;
    case "cancelled":
      return { kind: "cancelled", attemptId };
    case "stale":
      break;
  }
  // The status/outcome pairs above are exhaustive while the contract layer's
  // invariants hold; reaching here means the pair was inconsistent, so the panel
  // shows a neutral "superseded" notice rather than guessing at an answer.
  return { kind: "superseded", attemptId };
}

/** The one-line attempt status the page leads with. */
export function statusLine(display: DisplayView): string {
  switch (display.kind) {
    case "no_scene":
      return "No scene selected.";
    case "scene_ready":
      return "Scene ready. No attempts yet.";
    case "submitting":
      return `${shortAttemptId(display.attemptId)}: submitting...`;
    case "bound":
      return `${shortAttemptId(display.attemptId)}: bound, decision ${display.result.decision}.`;
    case "mismatched":
      return `${shortAttemptId(display.attemptId)}: the answer names different bytes; recorded as a witness, not displayed.`;
    case "refused":
      return `${shortAttemptId(display.attemptId)}: refused by the server.`;
    case "failed":
      return `${shortAttemptId(display.attemptId)}: failed.`;
    case "unparseable_result":
      return `${shortAttemptId(display.attemptId)}: the server's answer is not a readable result.`;
    case "cancelled":
      return `${shortAttemptId(display.attemptId)}: cancelled.`;
    case "superseded":
      return `${shortAttemptId(display.attemptId)}: superseded.`;
  }
}

export class SessionStore {
  private readonly fetchImpl: typeof fetch;
  private readonly listeners = new Set<() => void>();
  private baseUrl: string;
  /** In memory only: never persisted, never logged, never in a snapshot or receipt. */
  private token = "";
  private intake: SceneIntake | null = null;
  private session: SessionRecord | null = null;
  private sceneSummary: SceneSummary | null = null;
  private intakeError: SceneIntakeIssue | null = null;
  private inFlight: AbortController | null = null;
  private savedSession: SavedSessionSummary | null = null;
  private restoreError: SceneIntakeIssue | null = null;
  private storageWarning: string | null = null;
  private readonly storage: SessionPersistence | null;
  private current: SessionStoreState;

  constructor(options: SessionStoreOptions = {}) {
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
    this.storage = options.storage === undefined ? defaultSessionPersistence() : options.storage;
    this.savedSession = this.loadSavedSummary();
    this.current = {
      baseUrl: this.baseUrl,
      hasToken: false,
      scene: null,
      intakeError: null,
      session: null,
      display: { kind: "no_scene" },
      savedSession: this.savedSession,
      restoreError: null,
      storageWarning: this.storageWarning,
    };
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  getSnapshot(): SessionStoreState {
    return this.current;
  }

  setBaseUrl(url: string): void {
    this.baseUrl = url;
    this.publish();
  }

  /** Held in memory for the next submission only; pass "" to clear. */
  setToken(token: string): void {
    this.token = token;
    this.publish();
  }

  async selectSceneFile(file: SceneFileInput): Promise<void> {
    // A new selection supersedes anything in flight: abort the old request, then open
    // a fresh session for the new file. The abandoned attempt's completion finds a
    // different session id and is dropped, not applied across sessions.
    this.inFlight?.abort();
    this.inFlight = null;
    let text: string;
    try {
      text = await file.text();
    } catch (error) {
      this.intakeError = {
        code: "unreadable_file",
        message: `The file could not be read: ${(error as Error).message}`,
        path: null,
      };
      this.publish();
      return;
    }
    try {
      const intake = await intakeSceneText(text);
      this.intake = intake;
      this.session = createSession(`session-${crypto.randomUUID()}`, file.name, intake.sha256);
      this.sceneSummary = {
        fileName: file.name,
        sha256: intake.sha256,
        byteLength: intake.bytes.byteLength,
        version: intake.version,
        warnings: intake.warnings,
      };
      this.intakeError = null;
      this.publish();
    } catch (error) {
      // A refused selection changes nothing about a session already in progress.
      this.intakeError =
        error instanceof SceneIntakeError
          ? { code: error.code, message: error.message, path: error.path }
          : {
              code: "unreadable_file",
              message: (error as Error).message,
              path: null,
            };
      this.publish();
    }
  }

  async submit(): Promise<void> {
    const intake = this.intake;
    const session = this.session;
    if (intake === null || session === null) {
      return; // Nothing to submit: the page disables the control alongside this guard.
    }
    const client = new PlacementClient({
      baseUrl: this.baseUrl,
      fetchImpl: this.fetchImpl,
      ...(this.token === "" ? {} : { authToken: this.token }),
    });
    const sessionId = session.sessionId;
    const attemptId = newAttemptId(sessionId);
    // Synchronous prologue: the attempt is recorded and the page shows "submitting"
    // before the first await, so Cancel is live from the moment submission begins.
    this.session = startAttempt(session, attemptId, client.requestReceiptFor(attemptId, intake));
    const controller = new AbortController();
    this.inFlight = controller;
    this.publish();

    const report = await client.submitPlacement(intake, attemptId, controller.signal);
    if (this.inFlight === controller) {
      this.inFlight = null;
    }
    if (this.session === null || this.session.sessionId !== sessionId) {
      return; // The scene was re-selected mid-flight; that outcome belongs to a discarded session.
    }
    this.session = applyOutcome(this.session, attemptId, outcomeFromReport(report));
    this.publish();
  }

  cancel(): void {
    // Only an in-flight attempt may be cancelled; a settled attempt's record stands.
    const newest = this.session?.attempts.at(-1);
    if (newest === undefined || newest.status !== "submitting") {
      return;
    }
    this.inFlight?.abort();
  }

  /**
   * Restores the browser-local saved session: scene bytes re-intaked from the exact
   * persisted text (same bytes, same SHA-256), attempts, witnesses and receipts intact,
   * so the newest bound result displays again and a new attempt can start. The bearer
   * token is never restored — it lives in memory for the app lifetime only, so a
   * reopened session submits unauthenticated until a new one is entered.
   */
  async reopenSession(): Promise<void> {
    if (this.storage === null || this.savedSession === null) {
      return;
    }
    let raw: string | null = null;
    try {
      raw = this.storage.getItem(SESSION_STORAGE_KEY);
    } catch (error) {
      this.storageWarning = `The saved session could not be read: ${(error as Error).message}`;
      this.savedSession = null;
      this.publish();
      return;
    }
    if (raw === null) {
      this.savedSession = null;
      this.publish();
      return;
    }
    await this.restoreFromText(raw);
  }

  /**
   * Restores a record from JSON text (a user-chosen export file). Failures — malformed
   * JSON, wrong format, unreadable attempts — leave the current session untouched and
   * surface as restoreError.
   */
  async importSession(recordText: string): Promise<void> {
    await this.restoreFromText(recordText);
  }

  /**
   * The current session as redacted JSON, or null with no session to export. The
   * payload structurally has no credential field: the token is excluded by
   * construction, as are headers (the client never records them).
   */
  exportSession(): string | null {
    if (this.intake === null || this.session === null) {
      return null;
    }
    const payload = buildSessionPayload(
      this.sceneSummary?.fileName ?? "",
      this.intake.text,
      this.session,
    );
    return JSON.stringify(payload, null, 2);
  }

  /** Clears the saved record and its notice; the live session is untouched. */
  discardSavedSession(): void {
    if (this.storage !== null) {
      try {
        this.storage.removeItem(SESSION_STORAGE_KEY);
      } catch (error) {
        this.storageWarning = `The saved session could not be discarded: ${(error as Error).message}`;
      }
    }
    this.savedSession = null;
    this.publish();
  }

  /** Shared reopen/import pipeline: validate, re-intake the scene, rehydrate the record. */
  private async restoreFromText(raw: string): Promise<void> {
    const parsed = parseSessionPayload(raw);
    if (!parsed.ok) {
      this.restoreError = {
        code: parsed.failure.code,
        message: parsed.failure.message,
        path: null,
      };
      this.publish();
      return;
    }
    // An in-flight attempt belongs to the session being replaced: abort it, so its
    // late completion cannot argue with the restored record.
    this.inFlight?.abort();
    this.inFlight = null;
    let intake: SceneIntake;
    try {
      intake = await intakeSceneText(parsed.payload.scene.text);
    } catch (error) {
      this.restoreError =
        error instanceof SceneIntakeError
          ? { code: error.code, message: error.message, path: error.path }
          : { code: "unreadable_file", message: (error as Error).message, path: null };
      this.publish();
      return;
    }
    this.intake = intake;
    this.session = rehydrateSession(parsed.payload);
    this.sceneSummary = {
      fileName: parsed.payload.scene.fileName,
      sha256: intake.sha256,
      byteLength: intake.bytes.byteLength,
      version: intake.version,
      warnings: intake.warnings,
    };
    this.intakeError = null;
    this.restoreError = null;
    // The restored record is the live session now; publish re-saves it (with any
    // interrupted-attempt normalization) through the ordinary persistence path.
    this.savedSession = null;
    this.publish();
  }

  /** Reads the saved record (if any) into the summary the page offers for reopening. */
  private loadSavedSummary(): SavedSessionSummary | null {
    if (this.storage === null) {
      return null;
    }
    let raw: string | null;
    try {
      raw = this.storage.getItem(SESSION_STORAGE_KEY);
    } catch (error) {
      this.storageWarning = `The saved session could not be read: ${(error as Error).message}`;
      return null;
    }
    if (raw === null) {
      return null;
    }
    const parsed = parseSessionPayload(raw);
    if (!parsed.ok) {
      // Keep the stored bytes (they may be recoverable by hand); only the offer goes away.
      this.storageWarning = `A saved session record could not be read (${parsed.failure.message}); reopening stays unavailable and the saved bytes are kept.`;
      return null;
    }
    return savedSessionSummaryOf(parsed.payload);
  }

  /**
   * The write side of browser-local persistence: while a session is open, every
   * published state re-saves the redacted record. Starting a new session replaces the
   * saved one (one slot, like the page's one session at a time). The payload carries
   * no credential field, so no caller can put the token in storage through here.
   */
  private saveToStorage(): void {
    if (this.storage === null || this.intake === null || this.session === null) {
      return;
    }
    try {
      const payload = buildSessionPayload(
        this.sceneSummary?.fileName ?? "",
        this.intake.text,
        this.session,
      );
      this.storage.setItem(SESSION_STORAGE_KEY, JSON.stringify(payload));
      this.storageWarning = null;
    } catch (error) {
      this.storageWarning = `The session could not be saved in this browser: ${(error as Error).message}`;
    }
  }

  private publish(): void {
    this.saveToStorage();
    this.current = {
      baseUrl: this.baseUrl,
      hasToken: this.token !== "",
      scene: this.sceneSummary,
      intakeError: this.intakeError,
      session: this.session,
      display: displayedView(this.session),
      savedSession: this.savedSession,
      restoreError: this.restoreError,
      storageWarning: this.storageWarning,
    };
    for (const listener of this.listeners) {
      listener();
    }
  }
}
