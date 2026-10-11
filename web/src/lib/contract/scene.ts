// Scene intake: turn a local scene file's exact text into the bytes this client will
// upload, a typed view for rendering, and a version report. The placement server is the
// authority on whether a scene is valid — its refusals carry the code and the JSON
// pointer of the field at fault. This module only catches what is cheap and unambiguous
// locally (unreadable JSON, wrong top-level shape, an unsupported schema version), so a
// clearly broken file never makes a pointless round trip, and everything else is sent
// byte-for-byte so the server's answer binds to the exact upload.

import { sha256HexOfText } from "./hash.ts";
import type { Scene } from "./types.ts";
import { reportSceneVersion, type SceneVersionReport } from "./versions.ts";

export interface SceneIntakeIssue {
  /** Mirrors the server's envelope: a code, a message, and a JSON pointer when one field is at fault. */
  readonly code: string;
  readonly message: string;
  readonly path: string | null;
}

export class SceneIntakeError extends Error implements SceneIntakeIssue {
  readonly code: string;
  readonly path: string | null;

  constructor(issue: SceneIntakeIssue) {
    super(issue.message);
    this.name = "SceneIntakeError";
    this.code = issue.code;
    this.path = issue.path;
  }
}

export interface SceneIntake {
  readonly scene: Scene;
  /** The file's exact text; the upload sends these bytes, never a re-serialization. */
  readonly text: string;
  readonly bytes: Uint8Array;
  /** SHA-256 of the exact upload bytes — what stats.input_sha256 must equal for a match. */
  readonly sha256: string;
  readonly version: SceneVersionReport;
  /**
   * Findings that do not block submission but the reviewer should see: a minor schema
   * version this build has not tested against, for example. Backed by the advisory
   * shape check below.
   */
  readonly warnings: readonly string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNumberArray(value: unknown, length: number): value is number[] {
  return (
    Array.isArray(value) &&
    value.length === length &&
    value.every((n) => typeof n === "number" && Number.isFinite(n))
  );
}

/**
 * Local checks on the fields the client itself renders. Strictly advisory: the server
 * re-checks everything (unknown fields are rejected server-side, so nothing here can
 * widen the contract). Each finding is a refusal with the same code/message/path shape
 * the server uses.
 */
function advisoryChecks(raw: unknown): SceneIntakeIssue | null {
  if (!isRecord(raw)) {
    return { code: "invalid_scene", message: "The scene is not a JSON object.", path: null };
  }
  if (!isRecord(raw.meter)) {
    return { code: "invalid_scene", message: "The scene has no meter object.", path: "/meter" };
  }
  if (!isNumberArray(raw.meter.pos, 3)) {
    return {
      code: "invalid_scene",
      message: "The meter's pos is not three finite numbers [x, y, z] in feet.",
      path: "/meter/pos",
    };
  }
  if (typeof raw.meter.wall_id !== "string" || raw.meter.wall_id.length === 0) {
    return {
      code: "invalid_scene",
      message: "The meter names no wall_id.",
      path: "/meter/wall_id",
    };
  }
  if (!Array.isArray(raw.walls) || raw.walls.length === 0) {
    return { code: "invalid_scene", message: "The scene lists no walls.", path: "/walls" };
  }
  for (const [index, wall] of raw.walls.entries()) {
    if (!isRecord(wall) || typeof wall.id !== "string" || wall.id.length === 0) {
      return {
        code: "invalid_scene",
        message: `Wall ${index} has no id.`,
        path: `/walls/${index}/id`,
      };
    }
    if (
      !isRecord(wall) ||
      !Array.isArray(wall.baseline) ||
      wall.baseline.length < 2 ||
      !wall.baseline.every((p) => isNumberArray(p, 2))
    ) {
      return {
        code: "invalid_scene",
        message: `Wall ${wall?.id ?? index}'s baseline is not at least two [x, z] points.`,
        path: `/walls/${index}/baseline`,
      };
    }
  }
  return null;
}

/**
 * Parses a scene file's exact text. Throws SceneIntakeError for anything the client
 * refuses locally; otherwise the intake carries the exact bytes to upload and their
 * hash. `sha256` is computed asynchronously, so use `intakeSceneText` when the hash is
 * needed immediately (the common case).
 */
export function parseSceneText(text: string): {
  scene: Scene;
  version: SceneVersionReport;
  warnings: string[];
} {
  let raw: unknown;
  try {
    raw = JSON.parse(text) as unknown;
  } catch (error) {
    throw new SceneIntakeError({
      code: "invalid_json",
      message: `The file is not valid JSON: ${(error as Error).message}`,
      path: null,
    });
  }
  const unsupported = refuseUnsupportedVersion(raw);
  if (unsupported !== null) {
    throw new SceneIntakeError(unsupported);
  }
  const advisory = advisoryChecks(raw);
  if (advisory !== null) {
    throw new SceneIntakeError(advisory);
  }
  const version = reportSceneVersion((raw as { schema_version?: unknown }).schema_version);
  const warnings: string[] = [];
  if (version.kind === "compatible_untested_minor") {
    warnings.push(
      `Scene schema version ${version.version} is newer than the ${version.testedVersion} this build was tested ` +
        "against. Minor versions only add optional fields, so it should read correctly; nothing here has verified that.",
    );
  }
  return { scene: raw as Scene, version, warnings };
}

function refuseUnsupportedVersion(raw: unknown): SceneIntakeIssue | null {
  const version = isRecord(raw) ? raw.schema_version : undefined;
  if (version === undefined) {
    return null; // Absent means "1.0" per the schema.
  }
  if (typeof version !== "string" || !/^1\.[0-9]+$/.test(version)) {
    return {
      code: "unsupported_schema_version",
      message: `The scene names schema_version ${JSON.stringify(version)}; this client reads 1.x scenes only.`,
      path: "/schema_version",
    };
  }
  return null;
}

export async function intakeSceneText(text: string): Promise<SceneIntake> {
  const { scene, version, warnings } = parseSceneText(text);
  const bytes = new TextEncoder().encode(text);
  return { scene, text, bytes, sha256: await sha256HexOfText(text), version, warnings };
}
