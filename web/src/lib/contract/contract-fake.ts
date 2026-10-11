// The contract fake: an in-memory stand-in for the placement server that answers per
// the documented contract, as a fetch implementation, so PlacementClient and the
// session layer run against it unchanged and every test sees a real HTTP-shaped
// exchange. It serves:
//
// - a 200 result whose stats.input_sha256 is the SHA-256 of the exact bytes it
//   received (the bound answer), or one that names other bytes (mismatched_sha) —
//   the answer a lying or mixed-up server would give;
// - 200s that are not JSON at all, and results with a foreign schema_version;
// - the uniform {"error": {code, message, path}} refusal envelope with any status;
// - error bodies that are not the envelope (raw text/HTML);
// - transport faults thrown out of fetch, and cancellation respected mid-flight.
//
// It records every request it receives and the text of its last response, so tests can
// assert on the exchange receipts, not on memories of them. Its /v1/schemas payloads
// are stubs (the real schema files are read by the schema-conformance tests directly).
//
// This module also holds the shared fixtures: a scene and a result built from the
// TypeScript mirror types and schema-faithful in every field they set — the
// schema-conformance tests validate them against server/schemas/*.schema.json, so one
// honest example backs every test instead of hand-rolled literals. All values are
// synthetic; the repository is public.

import { sha256Hex } from "./hash.ts";
import type { PlacementResult, Scene } from "./types.ts";

/** A 64-hex digest that is not the fixture's, for mismatch witnesses. */
export const MISMATCHED_SHA256 = "cd".repeat(32);

/** 64 hex characters for the fixture's rules_sha256 (pattern ^[0-9a-f]{64}$). */
export const RULES_SHA256 = "ab".repeat(32);

/** The scene the tests share: one wall, one object, full coverage, no keyframes (the inspection client uploads no bundle). */
export function sceneFixture(): Scene {
  return {
    meter: { pos: [0, 0, 0], wall_id: "north" },
    walls: [
      {
        id: "north",
        baseline: [
          [-10, 0],
          [10, 0],
        ],
        height_ft: 9,
        source: "tap",
        plus_minus_ft: 0.2,
      },
    ],
    objects: [
      {
        type: "gas_meter",
        wall_id: "north",
        span_ft: [6, 7],
        bottom_ft: 0.5,
        top_ft: 1.5,
        attrs: { operable: true },
        source: "tap",
        plus_minus_ft: 0.1,
      },
    ],
    ground: [
      {
        type: "concrete",
        polygon: [
          [-10, 0],
          [10, 0],
          [10, 6],
          [-10, 6],
        ],
        plus_minus_ft: 0.3,
      },
    ],
    overheads: [{ wall_id: "north", span_ft: [-2, 2], clearance_ft: 9, plus_minus_ft: 0.4 }],
    facing: [{ wall_id: "north", span_ft: [-10, 10], depth_ft: 12, plus_minus_ft: 0.5 }],
    coverage: {
      ends: {
        left: { kind: "limit", note: "fence" },
        right: { kind: "unexplored", note: "corner" },
      },
      observed: [
        { band: "wall", span_ft: [-10, 10], out_ft: 9 },
        { band: "ground", span_ft: [-10, 10], out_ft: 6, camera_pos_ft: [0, 4] },
      ],
    },
    stills: { meter_close: "stills/meter_close.jpg" },
    gps: { lat: 30.2, lon: -97.7 },
    heading: { deg: 270, accuracy_deg: 2 },
  };
}

/** The fixture scene's exact text — what the intake hashes and what the upload sends. */
export function sceneText(): string {
  return JSON.stringify(sceneFixture(), null, 2);
}

/** A schema-faithful result answering for the given scene hash. */
export function resultFixture(inputSha256: string): PlacementResult {
  return {
    schema_version: "1.0",
    decision: "pass",
    summary: "The north wall fits a battery between 3.0 ft and 5.5 ft from the meter.",
    reasons: [
      {
        code: "all_checks_pass",
        message: "Every check at the chosen spot passes with the observed evidence.",
      },
    ],
    policy: {
      id: "public-demo",
      version: "2026-09-25",
      auto_approve: true,
      allow_reject: true,
      sources: ["public"],
      rules_sha256: RULES_SHA256,
      notice: "Demo rules, not the utility's.",
    },
    spot: {
      outcome: "pass",
      wall_id: "north",
      segment: 0,
      span_ft: [3, 5.5],
      width_ft: 2.5,
      depth_ft: 1.2,
      height_ft: 2,
      footprint: [
        [3, 0],
        [5.5, 0],
        [5.5, 1.2],
        [3, 1.2],
      ],
      center: [4.25, 0.6],
      along: [1, 0],
      outward: [0, 1],
      meter_offset_ft: [4.25, 0.6],
      route_length_ft: 4.25,
    },
    route: {
      outcome: "pass",
      length_ft: 4.25,
      plus_minus_ft: 0.3,
      height_ft: 1,
      polyline: [
        [0, 0],
        [4.25, 0],
        [4.25, 1.2],
      ],
      detours: [],
      crossings: [],
      length_is_lower_bound: false,
    },
    checks: [
      {
        id: "gas_clearance",
        label: "Gas clearance",
        outcome: "pass",
        reason: "The nearest gas meter is 2.0 ft away, 1.0 ft past the required clearance.",
        measured_ft: 2,
        plus_minus_ft: 0.1,
        threshold_ft: 1,
        comparison: "at_least",
        subject: "objects[0] gas_meter",
        rule: { key: "clearances.gas_ft", source: "synthetic fixture value", placeholder: true },
      },
    ],
    nearest_considered: null,
    missing_evidence: [],
    objects_not_used: [],
    ends: {
      left: { kind: "limit", s_ft: -10, point: [-10, 0] },
      right: { kind: "unexplored", s_ft: 10, point: [10, 0] },
    },
    sweep: [{ wall_id: "north", start_ft: [3, 5.5], outcome: "pass", failing: [], unsure: [] }],
    stats: {
      candidates: 1,
      pass: 1,
      unsure: 0,
      fail: 0,
      elapsed_ms: 12,
      input_sha256: inputSha256,
    },
  };
}

/** One request the fake received, with its headers and body bytes. */
export interface FakeRequest {
  readonly method: string;
  readonly url: string;
  readonly headers: Record<string, string>;
  readonly body: Uint8Array | null;
}

export type FakeBehavior =
  /** Default: 200 with a result bound to the received bytes. */
  | { readonly kind: "bound" }
  /** 200 with a result whose input_sha256 names other bytes. */
  | { readonly kind: "mismatched_sha" }
  /** 200 with a body that is not JSON. */
  | { readonly kind: "unparseable_200" }
  /** 200 with a result carrying a foreign schema_version. */
  | { readonly kind: "wrong_version_200" }
  /** The uniform refusal envelope with any status and code. */
  | {
      readonly kind: "refused";
      readonly status: number;
      readonly code: string;
      readonly message: string;
      readonly path: string | null;
    }
  /** An error body that is not the envelope (HTML, empty, plain text). */
  | {
      readonly kind: "raw";
      readonly status: number;
      readonly contentType: string;
      readonly bodyText: string;
    }
  /** fetch throws — the transport-level fault. */
  | { readonly kind: "throws"; readonly detail: string }
  /** The fetch promise settles only after delayMs, then honours an abort. */
  | { readonly kind: "delayed_then_abort"; readonly delayMs: number };

export interface ContractFakeOptions {
  readonly behavior?: FakeBehavior;
  /**
   * Only requests whose URL contains this substring get the behavior; the rest are
   * served normally. Empty (default) matches every request.
   */
  readonly applyTo?: string;
}

export interface ContractFake {
  readonly fetch: typeof fetch;
  readonly requests: readonly FakeRequest[];
  /** The text of the most recent response body, for hashing it independently. */
  readonly lastResponseText: string | null;
}

function abortError(): DOMException {
  return new DOMException("This operation was aborted.", "AbortError");
}

function healthPayload(): Record<string, unknown> {
  return { status: "ok", schema_version: "1.0", policy: { id: "public-demo", auto_approve: true } };
}

export function createContractFake(options: ContractFakeOptions = {}): ContractFake {
  const behavior: FakeBehavior = options.behavior ?? { kind: "bound" };
  const applyTo = options.applyTo ?? "";
  const requests: FakeRequest[] = [];
  let lastResponseText: string | null = null;

  const jsonResponse = (status: number, payload: unknown): Response => {
    const text = JSON.stringify(payload);
    lastResponseText = text;
    return new Response(text, { status, headers: { "content-type": "application/json" } });
  };

  const textResponse = (status: number, contentType: string, bodyText: string): Response => {
    lastResponseText = bodyText;
    return new Response(bodyText, { status, headers: { "content-type": contentType } });
  };

  const errorResponse = (
    status: number,
    code: string,
    message: string,
    path: string | null,
  ): Response => jsonResponse(status, { error: { code, message, path } });

  /** The normal answer for a URL when no behavior targets it. */
  const serveDefault = async (
    url: string,
    method: string,
    body: Uint8Array | null,
  ): Promise<Response> => {
    if (url.includes("/v1/placements")) {
      if (method !== "POST") {
        return errorResponse(
          405,
          "method_not_allowed",
          `${method} is not how scenes are submitted.`,
          null,
        );
      }
      const received = body ?? new Uint8Array();
      return jsonResponse(200, resultFixture(await sha256Hex(received)));
    }
    if (url.includes("/v1/schemas/scene.json")) {
      return jsonResponse(200, { type: "object", required: ["meter", "walls"] });
    }
    if (url.includes("/v1/schemas/result.json")) {
      return jsonResponse(200, { type: "object", required: ["schema_version"] });
    }
    if (url.includes("/health")) {
      if (method !== "GET") {
        return errorResponse(
          405,
          "method_not_allowed",
          `${method} is not how health is read.`,
          null,
        );
      }
      return jsonResponse(200, healthPayload());
    }
    return errorResponse(404, "not_found", `No contract route for ${method} ${url}.`, null);
  };

  const fakeFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init?.method ?? "GET").toUpperCase();
    const headers: Record<string, string> = {};
    new Headers(init?.headers).forEach((value, key) => {
      headers[key] = value;
    });
    const rawBody = init?.body;
    const body =
      rawBody === undefined || rawBody === null
        ? null
        : rawBody instanceof Uint8Array
          ? rawBody
          : new TextEncoder().encode(String(rawBody));
    const signal = init?.signal;

    // A real fetch rejects immediately when the signal is already aborted; the request
    // never reaches the wire, so nothing is recorded as received.
    if (signal?.aborted) {
      throw abortError();
    }

    requests.push({ method, url, headers, body });

    if (!url.includes(applyTo)) {
      return serveDefault(url, method, body);
    }
    switch (behavior.kind) {
      case "bound":
        return serveDefault(url, method, body);
      case "mismatched_sha":
        return jsonResponse(200, resultFixture(MISMATCHED_SHA256));
      case "unparseable_200":
        return textResponse(200, "application/json", "This is not JSON at all.");
      case "wrong_version_200": {
        const received = body ?? new Uint8Array();
        const wrong = { ...resultFixture(await sha256Hex(received)), schema_version: "2.0" };
        return jsonResponse(200, wrong);
      }
      case "refused":
        return errorResponse(behavior.status, behavior.code, behavior.message, behavior.path);
      case "raw":
        return textResponse(behavior.status, behavior.contentType, behavior.bodyText);
      case "throws":
        throw new TypeError(behavior.detail);
      case "delayed_then_abort": {
        await new Promise((resolve) => {
          setTimeout(resolve, behavior.delayMs);
        });
        if (signal?.aborted) {
          throw abortError();
        }
        return serveDefault(url, method, body);
      }
    }
  };

  return {
    fetch: fakeFetch,
    requests,
    get lastResponseText() {
      return lastResponseText;
    },
  };
}
