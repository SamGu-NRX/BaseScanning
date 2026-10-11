// The portable placement client: one fetch-based transport for the browser and Node
// (vitest), with the fetch implementation injected so tests can point it at the local
// server, the contract fake, or a failing transport. Every call produces receipts —
// what was sent (bounded), what came back (bounded) — because the evidence trail needs
// the actual exchange, not a memory of it.
//
// Scope: this client sends a bare scene.json (application/json), which is what the
// solver reads and what the public deployments accept. Zip bundles and multipart forms
// exist in the contract for image-carrying uploads from the capture flow; this
// inspection client does not upload images and does not use them (see web/README.md).

import type { PlacementFailure } from "./errors.ts";
import { parseErrorEnvelope } from "./errors.ts";
import { sha256Hex } from "./hash.ts";
import { intakeResultBody } from "./result.ts";
import type { SceneIntake } from "./scene.ts";
import type { AttemptOutcome, RequestReceipt, ResponseReceipt } from "./session.ts";

/** Response bodies are hashed and excerpted but never stored whole above this cap. */
const DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const EXCERPT_BYTES = 512;

export interface PlacementClientOptions {
  /** Origin of the placement server, e.g. "http://localhost:8000". */
  readonly baseUrl: string;
  /** Injectable transport (tests, contract fake). Defaults to globalThis.fetch. */
  readonly fetchImpl?: typeof fetch;
  /** Bearer token for servers that loaded private rules. Omitted unless configured. */
  readonly authToken?: string;
  readonly maxResponseBytes?: number;
}

export interface ExchangeRecord {
  readonly request: RequestReceipt;
  readonly response: ResponseReceipt | null;
}

export interface SubmitSuccess {
  readonly ok: true;
  readonly outcome: AttemptOutcome;
  readonly exchange: ExchangeRecord;
}

export interface SubmitFailure {
  readonly ok: false;
  /** Always present: the transport-level failure (network/abort). */
  readonly failure: PlacementFailure;
  readonly exchange: ExchangeRecord;
}

export type SubmitReport = SubmitSuccess | SubmitFailure;

export interface ServerHealth {
  readonly status: string;
  readonly schemaVersion: string;
  readonly policy: unknown;
}

function isAbort(error: unknown, signal: AbortSignal): boolean {
  return signal.aborted && error instanceof DOMException && error.name === "AbortError";
}

async function readBodyCapped(
  response: Response,
  maxBytes: number,
): Promise<{ bytes: Uint8Array; truncated: boolean }> {
  if (response.body === null) {
    return { bytes: new Uint8Array(), truncated: false };
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let truncated = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    if (value === undefined) {
      continue;
    }
    total += value.byteLength;
    if (total > maxBytes) {
      truncated = true;
      await reader.cancel();
      break;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(
    total > maxBytes ? chunks.reduce((n, c) => n + c.byteLength, 0) : total,
  );
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { bytes, truncated };
}

function excerptOf(bytes: Uint8Array): string | null {
  if (bytes.length === 0) {
    return null;
  }
  const slice = bytes.slice(0, Math.min(EXCERPT_BYTES, bytes.length));
  return new TextDecoder("utf-8", { fatal: false }).decode(slice);
}

export class PlacementClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly maxResponseBytes: number;
  private readonly authToken: string | undefined;

  constructor(options: PlacementClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    // Native fetch is receiver-sensitive: in a browser, calling it with any object
    // other than the global as `this` throws "Illegal invocation", and a member
    // call like `this.fetchImpl(...)` would do exactly that. Strip the receiver
    // from whatever function was provided; bind the default to the global.
    const provided = options.fetchImpl;
    this.fetchImpl = provided
      ? (...args: Parameters<typeof fetch>) => provided(...args)
      : fetch.bind(globalThis);
    this.maxResponseBytes = options.maxResponseBytes ?? DEFAULT_MAX_RESPONSE_BYTES;
    this.authToken = options.authToken;
  }

  /**
   * The receipt for a planned submission, built synchronously from an intake so the
   * session layer can record the attempt before its first await.
   */
  requestReceiptFor(
    attemptId: string,
    intake: SceneIntake,
    startedAt: number = Date.now(),
  ): RequestReceipt {
    return {
      attemptId,
      url: `${this.baseUrl}/v1/placements`,
      method: "POST",
      contentType: "application/json",
      byteLength: intake.bytes.byteLength,
      requestSha256: intake.sha256,
      startedAt,
    };
  }

  /**
   * Posts the scene's exact bytes to /v1/placements. Resolves with a SubmitReport — it
   * does not throw for server refusals or network failure; only for caller misuse.
   * Cancellation is cooperative: pass an AbortSignal and the resolved report carries an
   * "aborted" failure.
   */
  async submitPlacement(
    intake: SceneIntake,
    attemptId: string,
    signal: AbortSignal,
  ): Promise<SubmitReport> {
    const startedAt = Date.now();
    const request = this.requestReceiptFor(attemptId, intake, startedAt);
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.authToken !== undefined) {
      headers.Authorization = `Bearer ${this.authToken}`;
    }

    let response: Response;
    try {
      response = await this.fetchImpl(request.url, {
        method: "POST",
        headers,
        body: intake.bytes as unknown as BodyInit,
        signal,
      });
    } catch (error) {
      const failure: PlacementFailure = isAbort(error, signal)
        ? { kind: "aborted" }
        : { kind: "network_error", detail: (error as Error).message };
      return { ok: false, failure, exchange: { request, response: null } };
    }

    const finishedAt = Date.now();
    const { bytes, truncated } = await readBodyCapped(response, this.maxResponseBytes);
    const responseReceipt: ResponseReceipt = {
      attemptId,
      status: response.status,
      contentType: response.headers.get("content-type") ?? "",
      bodyByteLength: bytes.byteLength,
      bodySha256: await sha256Hex(bytes),
      bodyExcerpt: excerptOf(bytes),
      bodyTruncated: truncated,
      elapsedMs: finishedAt - startedAt,
      finishedAt,
    };

    if (response.status !== 200) {
      let parsed: unknown = null;
      let jsonbroke: string | null = null;
      try {
        parsed = JSON.parse(new TextDecoder().decode(bytes)) as unknown;
      } catch (error) {
        jsonbroke = (error as Error).message;
      }
      if (jsonbroke !== null || parsed === null) {
        const failure: PlacementFailure = {
          kind: "unparseable_error",
          status: response.status,
          detail:
            jsonbroke !== null
              ? `HTTP ${response.status} body is not JSON: ${jsonbroke}`
              : `HTTP ${response.status} body is empty`,
        };
        return { ok: false, failure, exchange: { request, response: responseReceipt } };
      }
      const failure = parseErrorEnvelope(response.status, parsed);
      return { ok: false, failure, exchange: { request, response: responseReceipt } };
    }

    // 200: parse as a result and bind it to the request bytes.
    let parsed: unknown;
    try {
      parsed = JSON.parse(new TextDecoder().decode(bytes)) as unknown;
    } catch (error) {
      return {
        ok: true,
        outcome: {
          type: "unparseable_result",
          response: responseReceipt,
          failure: {
            reason: "not_json",
            detail: `A 200 body that is not JSON: ${(error as Error).message}`,
          },
        },
        exchange: { request, response: responseReceipt },
      };
    }
    const intakeResult = intakeResultBody(parsed);
    if (!intakeResult.ok) {
      return {
        ok: true,
        outcome: {
          type: "unparseable_result",
          response: responseReceipt,
          failure: intakeResult.failure,
        },
        exchange: { request, response: responseReceipt },
      };
    }
    const outcome: AttemptOutcome = {
      type: "result",
      response: responseReceipt,
      result: intakeResult.result,
      binding: { kind: "bound" },
      additiveNotes: intakeResult.additiveNotes,
    };
    // The binding check lives in one place; run it here too so a client-only caller
    // (without the session layer) still gets the mismatch witness.
    if (intakeResult.result.stats.input_sha256 !== request.requestSha256) {
      return {
        ok: true,
        outcome: {
          ...outcome,
          binding: {
            kind: "mismatched",
            requestSha256: request.requestSha256,
            resultSha256: intakeResult.result.stats.input_sha256,
          },
        },
        exchange: { request, response: responseReceipt },
      };
    }
    return { ok: true, outcome, exchange: { request, response: responseReceipt } };
  }

  /** GET /health — which rules are loaded and their hash. */
  async getHealth(
    signal?: AbortSignal,
  ): Promise<{ health: ServerHealth | null; failure: PlacementFailure | null }> {
    try {
      const response = await this.fetchImpl(`${this.baseUrl}/health`, {
        method: "GET",
        signal: signal ?? null,
      });
      if (response.status !== 200) {
        const { bytes } = await readBodyCapped(response, this.maxResponseBytes);
        let parsed: unknown = null;
        try {
          parsed = JSON.parse(new TextDecoder().decode(bytes)) as unknown;
        } catch {
          parsed = null;
        }
        return {
          health: null,
          failure:
            parsed === null
              ? {
                  kind: "unparseable_error",
                  status: response.status,
                  detail: `HTTP ${response.status}`,
                }
              : parseErrorEnvelope(response.status, parsed),
        };
      }
      const body = (await response.json()) as {
        status?: unknown;
        schema_version?: unknown;
        policy?: unknown;
      };
      return {
        health: {
          status: typeof body.status === "string" ? body.status : "unknown",
          schemaVersion: typeof body.schema_version === "string" ? body.schema_version : "unknown",
          policy: body.policy ?? null,
        },
        failure: null,
      };
    } catch (error) {
      if (signal !== undefined && isAbort(error, signal)) {
        return { health: null, failure: { kind: "aborted" } };
      }
      return { health: null, failure: { kind: "network_error", detail: (error as Error).message } };
    }
  }

  /** GET /v1/schemas/{scene,result}.json — the contract this server honours, for display. */
  async getSchemas(signal?: AbortSignal): Promise<{
    schemas: { scene: unknown; result: unknown } | null;
    failure: PlacementFailure | null;
  }> {
    try {
      const [scene, result] = await Promise.all([
        this.fetchImpl(`${this.baseUrl}/v1/schemas/scene.json`, {
          method: "GET",
          signal: signal ?? null,
        }),
        this.fetchImpl(`${this.baseUrl}/v1/schemas/result.json`, {
          method: "GET",
          signal: signal ?? null,
        }),
      ]);
      if (scene.status !== 200 || result.status !== 200) {
        return {
          schemas: null,
          failure: {
            kind: "unparseable_error",
            status: scene.status !== 200 ? scene.status : result.status,
            detail: "A schema endpoint did not answer 200",
          },
        };
      }
      return { schemas: { scene: await scene.json(), result: await result.json() }, failure: null };
    } catch (error) {
      if (signal !== undefined && isAbort(error, signal)) {
        return { schemas: null, failure: { kind: "aborted" } };
      }
      return {
        schemas: null,
        failure: { kind: "network_error", detail: (error as Error).message },
      };
    }
  }
}
