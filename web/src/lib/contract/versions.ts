// Which schema versions this client build understands. The server publishes the real
// schemas (GET /v1/schemas/scene.json and /v1/schemas/result.json); this module only
// decides whether a received version is supported, compatible, or refused.
//
// scene.schema.json: "Contract version. Absent means \"1.0\". Minor versions only add
// optional fields." So any 1.x scene is parseable; a minor above the tested one is
// accepted but must be reported as not verified by this build (the export names it).
// result.schema.json pins schema_version to "1.0", so a result with any other version
// is refused, not guessed at.

/** The scene minor version this build's tests cover. */
export const TESTED_SCENE_VERSION = "1.0";

/** The only result version the contract defines. */
export const RESULT_VERSION = "1.0";

export type SceneVersionReport =
  | { kind: "supported"; version: string }
  | { kind: "compatible_untested_minor"; version: string; testedVersion: string }
  | { kind: "unsupported"; version: string };

const SCENE_VERSION_PATTERN = /^1\.[0-9]+$/;

/**
 * Classifies a scene's schema_version the way the schema defines it: absent means
 * "1.0"; anything outside the 1.x pattern or above the tested minor is reported,
 * never silently accepted or silently dropped.
 */
export function reportSceneVersion(schemaVersion: unknown): SceneVersionReport {
  const version =
    typeof schemaVersion === "string" && schemaVersion !== ""
      ? schemaVersion
      : TESTED_SCENE_VERSION;
  if (!SCENE_VERSION_PATTERN.test(version)) {
    return { kind: "unsupported", version };
  }
  if (version === TESTED_SCENE_VERSION) {
    return { kind: "supported", version };
  }
  return { kind: "compatible_untested_minor", version, testedVersion: TESTED_SCENE_VERSION };
}

/** A result must carry exactly the one version the contract defines. */
export function reportResultVersion(schemaVersion: unknown): { ok: boolean; version: unknown } {
  return { ok: schemaVersion === RESULT_VERSION, version: schemaVersion };
}
