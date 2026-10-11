// The placement server's error contract: every refusal is the same JSON envelope,
// {"error": {"code", "message", "path"}}, where path is a JSON pointer into scene.json
// when one field is to blame (server/api.py module docstring). Codes this client knows
// are named; anything else is surfaced as an unknown code instead of being mashed into
// a generic failure, so a new server code is visible, not swallowed.

export type PlacementErrorCode =
  // 400: the body or bundle cannot be read at all.
  | "empty_body"
  | "unreadable_zip"
  | "unsafe_zip_entry"
  | "missing_scene_json"
  | "unreadable_multipart"
  | "missing_bundle"
  // 413: size limits.
  | "body_too_large"
  | "scene_too_large"
  | "bundle_too_many_entries"
  | "bundle_directory_too_large"
  | "bundle_too_large"
  // 415: wrong content type.
  | "unsupported_media_type"
  // 422: the scene is rejected as-is.
  | "invalid_json"
  | "invalid_scene"
  | "scene_too_complex"
  | "missing_bundle_file"
  // Auth (only while private rules are loaded).
  | "unauthorized"
  | "no_api_key"
  // Fallbacks for Starlette-raised HTTP errors and unhandled faults.
  | "bad_request"
  | "not_found"
  | "method_not_allowed"
  | "internal_error";

export interface ErrorEnvelope {
  readonly code: string;
  readonly message: string;
  readonly path: string | null;
}

export type PlacementFailure =
  /** The server answered with its error envelope. */
  | {
      readonly kind: "server_error";
      readonly status: number;
      readonly envelope: ErrorEnvelope;
      readonly knownCode: boolean;
    }
  /** The server answered, but not with the contract's envelope. */
  | { readonly kind: "unparseable_error"; readonly status: number; readonly detail: string }
  /** The request never completed. */
  | { readonly kind: "network_error"; readonly detail: string }
  /** The caller aborted the request (fetch threw AbortError). */
  | { readonly kind: "aborted" };

export function parseErrorEnvelope(status: number, body: unknown): PlacementFailure {
  const candidate = body as {
    error?: { code?: unknown; message?: unknown; path?: unknown };
  } | null;
  const err = candidate?.error;
  if (
    err === undefined ||
    err === null ||
    typeof err !== "object" ||
    typeof err.code !== "string" ||
    typeof err.message !== "string"
  ) {
    return {
      kind: "unparseable_error",
      status,
      detail: `HTTP ${status} with a body that is not the documented {"error": {code, message, path}} envelope`,
    };
  }
  const path = typeof err.path === "string" ? err.path : null;
  const envelope: ErrorEnvelope = { code: err.code, message: err.message, path };
  return { kind: "server_error", status, envelope, knownCode: isKnownErrorCode(err.code) };
}

const KNOWN_CODES: readonly PlacementErrorCode[] = [
  "empty_body",
  "unreadable_zip",
  "unsafe_zip_entry",
  "missing_scene_json",
  "unreadable_multipart",
  "missing_bundle",
  "body_too_large",
  "scene_too_large",
  "bundle_too_many_entries",
  "bundle_directory_too_large",
  "bundle_too_large",
  "unsupported_media_type",
  "invalid_json",
  "invalid_scene",
  "scene_too_complex",
  "missing_bundle_file",
  "unauthorized",
  "no_api_key",
  "bad_request",
  "not_found",
  "method_not_allowed",
  "internal_error",
];

export function isKnownErrorCode(code: string): code is PlacementErrorCode {
  return (KNOWN_CODES as readonly string[]).includes(code);
}

/**
 * The single-line verdict a user interface should lead with, per failure kind. The
 * server's own message stays available; this classifies before quoting.
 */
export function failureHeadline(failure: PlacementFailure): string {
  switch (failure.kind) {
    case "server_error":
      return failure.knownCode
        ? `The server refused the scene (${failure.envelope.code}).`
        : `The server refused the scene with an error code this client does not know (${failure.envelope.code}).`;
    case "unparseable_error":
      return "The server's answer was not the documented error envelope.";
    case "network_error":
      return "The server could not be reached.";
    case "aborted":
      return "The submission was cancelled.";
  }
}
