// Schema-conformance tests: the contract folder's TypeScript mirror is checked against
// the real schema files the server publishes (server/schemas/*.schema.json), per the
// mirror's own header comment. A schema change that matters to this client fails here
// instead of drifting silently. The validator below implements the JSON Schema subset
// these two schemas use (draft 2020-12: type, const, enum, required, additionalProperties:
// false, items, minItems/maxItems, minimum/exclusiveMinimum/maximum, minLength, pattern,
// oneOf, if/then, $defs/$ref) — just enough to prove the fixtures honest; it is not a
// general validator. The schema files are read read-only; nothing under server/ changes.

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { RULES_SHA256, resultFixture, sceneFixture, sceneText } from "./contract-fake.ts";
import { sha256HexOfText } from "./hash.ts";
import { intakeResultBody, REQUIRED_RESULT_FIELDS } from "./result.ts";
import { intakeSceneText, parseSceneText, SceneIntakeError } from "./scene.ts";
import {
  RESULT_VERSION,
  reportResultVersion,
  reportSceneVersion,
  TESTED_SCENE_VERSION,
} from "./versions.ts";

interface SchemaNode {
  $ref?: string;
  $defs?: Record<string, SchemaNode>;
  type?: string | string[];
  const?: unknown;
  enum?: unknown[];
  required?: string[];
  properties?: Record<string, SchemaNode>;
  additionalProperties?: boolean;
  items?: SchemaNode;
  minItems?: number;
  maxItems?: number;
  minimum?: number;
  exclusiveMinimum?: number;
  maximum?: number;
  minLength?: number;
  pattern?: string;
  oneOf?: SchemaNode[];
  if?: SchemaNode;
  then?: SchemaNode;
}

function loadSchema(fileName: string): SchemaNode {
  const file = fileURLToPath(new URL(`../../../../server/schemas/${fileName}`, import.meta.url));
  return JSON.parse(readFileSync(file, "utf8")) as SchemaNode;
}

const sceneSchema = loadSchema("scene.schema.json");
const resultSchema = loadSchema("result.schema.json");

function validate(value: unknown, schema: SchemaNode, root: SchemaNode, path: string): string[] {
  if (schema.$ref !== undefined) {
    const name = schema.$ref.replace("#/$defs/", "");
    const def = root.$defs?.[name];
    if (def === undefined) {
      return [`${path}: unknown $ref ${schema.$ref}`];
    }
    return validate(value, def, root, path);
  }
  const errors: string[] = [];
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    const matches = types.some((type) => {
      switch (type) {
        case "object":
          return typeof value === "object" && value !== null && !Array.isArray(value);
        case "array":
          return Array.isArray(value);
        case "string":
          return typeof value === "string";
        case "integer":
          return typeof value === "number" && Number.isInteger(value);
        case "number":
          return typeof value === "number";
        case "boolean":
          return typeof value === "boolean";
        case "null":
          return value === null;
        default:
          return false;
      }
    });
    if (!matches) {
      return [`${path}: expected type ${types.join("|")}`];
    }
  }
  if (schema.const !== undefined && JSON.stringify(value) !== JSON.stringify(schema.const)) {
    errors.push(`${path}: expected const ${JSON.stringify(schema.const)}`);
  }
  if (
    schema.enum !== undefined &&
    !schema.enum.some((option) => JSON.stringify(option) === JSON.stringify(value))
  ) {
    errors.push(`${path}: ${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`);
  }
  if (
    typeof value === "string" &&
    schema.minLength !== undefined &&
    value.length < schema.minLength
  ) {
    errors.push(`${path}: shorter than minLength ${schema.minLength}`);
  }
  if (
    typeof value === "string" &&
    schema.pattern !== undefined &&
    !new RegExp(schema.pattern).test(value)
  ) {
    errors.push(`${path}: does not match pattern ${schema.pattern}`);
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) {
      errors.push(`${path}: below minimum ${schema.minimum}`);
    }
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) {
      errors.push(`${path}: at or below exclusiveMinimum ${schema.exclusiveMinimum}`);
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      errors.push(`${path}: above maximum ${schema.maximum}`);
    }
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push(`${path}: fewer than ${schema.minItems} items`);
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      errors.push(`${path}: more than ${schema.maxItems} items`);
    }
    if (schema.items !== undefined) {
      value.forEach((item, index) => {
        errors.push(...validate(item, schema.items as SchemaNode, root, `${path}/${index}`));
      });
    }
  }
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    for (const key of schema.required ?? []) {
      if (!(key in record)) {
        errors.push(`${path}: missing required ${key}`);
      }
    }
    for (const [key, child] of Object.entries(schema.properties ?? {})) {
      if (key in record) {
        errors.push(...validate(record[key], child, root, `${path}/${key}`));
      }
    }
    if (schema.additionalProperties === false && schema.properties !== undefined) {
      for (const key of Object.keys(record)) {
        if (!Object.hasOwn(schema.properties, key)) {
          errors.push(`${path}: unknown field ${key}`);
        }
      }
    }
  }
  if (schema.oneOf !== undefined) {
    const matching = schema.oneOf.filter(
      (branch) => validate(value, branch, root, path).length === 0,
    );
    if (matching.length !== 1) {
      errors.push(`${path}: matches ${matching.length} oneOf branches, expected exactly 1`);
    }
  }
  if (
    schema.if !== undefined &&
    schema.then !== undefined &&
    validate(value, schema.if, root, path).length === 0
  ) {
    errors.push(...validate(value, schema.then, root, path));
  }
  return errors;
}

describe("REQUIRED_RESULT_FIELDS against result.schema.json", () => {
  it("names exactly the top-level fields the schema requires", () => {
    const required = resultSchema.required ?? [];
    expect([...REQUIRED_RESULT_FIELDS].sort()).toEqual([...required].sort());
    expect(new Set(REQUIRED_RESULT_FIELDS).size).toBe(REQUIRED_RESULT_FIELDS.length);
  });
});

describe("version constants against the schemas", () => {
  it("RESULT_VERSION is the result schema's const for schema_version", () => {
    expect(resultSchema.properties?.schema_version?.const).toBe(RESULT_VERSION);
  });

  it("TESTED_SCENE_VERSION follows the scene schema's version pattern", () => {
    const pattern = sceneSchema.properties?.schema_version?.pattern;
    expect(pattern).toBeDefined();
    const regex = new RegExp(pattern ?? "");
    expect(regex.test(TESTED_SCENE_VERSION)).toBe(true);
    expect(regex.test("1.9")).toBe(true);
    expect(regex.test("2.0")).toBe(false);
  });
});

describe("the TypeScript mirror against the real schemas", () => {
  it("a scene built from the mirror types validates against scene.schema.json", () => {
    expect(validate(sceneFixture(), sceneSchema, sceneSchema, "")).toEqual([]);
  });

  it("a result built from the mirror types validates against result.schema.json", () => {
    expect(validate(resultFixture(RULES_SHA256), resultSchema, resultSchema, "")).toEqual([]);
  });

  it("the scene intake accepts the fixture and hashes the exact bytes (node:crypto oracle)", async () => {
    const text = sceneText();
    const intake = await intakeSceneText(text);
    expect(intake.version).toEqual({ kind: "supported", version: "1.0" });
    expect(intake.warnings).toEqual([]);
    expect(intake.sha256).toBe(createHash("sha256").update(text, "utf8").digest("hex"));
    expect(intake.text).toBe(text);
  });

  it("the result intake accepts the fixture and binds it to its own hash", () => {
    const body: unknown = JSON.parse(JSON.stringify(resultFixture(RULES_SHA256)));
    const intake = intakeResultBody(body);
    expect(intake.ok).toBe(true);
    if (!intake.ok) return;
    expect(intake.additiveNotes).toEqual([]);
    expect(intake.result.stats.input_sha256).toBe(RULES_SHA256);
  });

  it("the intake's hash equals hash.ts's text hash, byte for byte", async () => {
    const text = sceneText();
    const intake = await intakeSceneText(text);
    expect(intake.sha256).toBe(await sha256HexOfText(text));
  });

  it("a scene without walls fails validation", () => {
    const broken = JSON.parse(sceneText()) as Record<string, unknown>;
    Reflect.deleteProperty(broken, "walls");
    const errors = validate(broken, sceneSchema, sceneSchema, "");
    expect(errors.some((error) => error.includes("missing required walls"))).toBe(true);
  });

  it("a one-point wall baseline fails validation at /walls/0/baseline", () => {
    const broken = JSON.parse(sceneText()) as { walls?: Array<Record<string, unknown>> };
    const wall = broken.walls?.[0];
    if (wall === undefined) throw new Error("scene fixture lost its wall");
    wall.baseline = [[0, 0]];
    const errors = validate(broken, sceneSchema, sceneSchema, "");
    expect(errors.some((error) => error.startsWith("/walls/0/baseline"))).toBe(true);
  });

  it("an unknown result field fails (additionalProperties: false)", () => {
    const body: unknown = { ...resultFixture(RULES_SHA256), extra: true };
    const errors = validate(body, resultSchema, resultSchema, "");
    expect(errors.some((error) => error.includes("unknown field extra"))).toBe(true);
  });

  it("a result decision outside the enum fails at /decision", () => {
    const body: unknown = { ...resultFixture(RULES_SHA256), decision: "maybe" };
    const errors = validate(body, resultSchema, resultSchema, "");
    expect(errors.some((error) => error.includes("/decision"))).toBe(true);
  });

  it("a result hash that is not 64 hex fails at /stats/input_sha256", () => {
    const body: unknown = { ...resultFixture("short") };
    const errors = validate(body, resultSchema, resultSchema, "");
    expect(errors.some((error) => error.startsWith("/stats/input_sha256"))).toBe(true);
  });
});

describe("scene intake refusals mirror the server's envelope shape", () => {
  function refusalOf(text: string): SceneIntakeError {
    try {
      parseSceneText(text);
    } catch (error) {
      if (error instanceof SceneIntakeError) return error;
      throw error;
    }
    throw new Error("expected the scene intake to refuse this text");
  }

  function mutateScene(apply: (scene: Record<string, unknown>) => void): string {
    const scene = JSON.parse(sceneText()) as Record<string, unknown>;
    apply(scene);
    return JSON.stringify(scene);
  }

  it("refuses text that is not JSON", () => {
    const refusal = refusalOf("this is not json");
    expect(refusal.code).toBe("invalid_json");
    expect(refusal.path).toBeNull();
  });

  it("refuses a top-level array", () => {
    const refusal = refusalOf("[]");
    expect(refusal.code).toBe("invalid_scene");
    expect(refusal.path).toBeNull();
  });

  it("refuses a scene without a meter at /meter", () => {
    const refusal = refusalOf(mutateScene((scene) => Reflect.deleteProperty(scene, "meter")));
    expect(refusal.code).toBe("invalid_scene");
    expect(refusal.path).toBe("/meter");
  });

  it("refuses a meter pos that is not three finite numbers at /meter/pos", () => {
    const refusal = refusalOf(
      mutateScene((scene) => {
        const meter = scene.meter as Record<string, unknown>;
        meter.pos = [1, 2];
      }),
    );
    expect(refusal.path).toBe("/meter/pos");
  });

  it("refuses a meter that names no wall at /meter/wall_id", () => {
    const refusal = refusalOf(
      mutateScene((scene) => {
        const meter = scene.meter as Record<string, unknown>;
        meter.wall_id = "";
      }),
    );
    expect(refusal.path).toBe("/meter/wall_id");
  });

  it("refuses a scene with no walls at /walls", () => {
    const refusal = refusalOf(mutateScene((scene) => Reflect.deleteProperty(scene, "walls")));
    expect(refusal.path).toBe("/walls");
  });

  it("refuses a wall without an id at /walls/0/id", () => {
    const refusal = refusalOf(
      mutateScene((scene) => {
        const walls = scene.walls as Array<Record<string, unknown>>;
        const wall = walls[0];
        if (wall === undefined) throw new Error("scene fixture lost its wall");
        wall.id = "";
      }),
    );
    expect(refusal.path).toBe("/walls/0/id");
  });

  it("refuses a one-point baseline at /walls/0/baseline", () => {
    const refusal = refusalOf(
      mutateScene((scene) => {
        const walls = scene.walls as Array<Record<string, unknown>>;
        const wall = walls[0];
        if (wall === undefined) throw new Error("scene fixture lost its wall");
        wall.baseline = [[0, 0]];
      }),
    );
    expect(refusal.path).toBe("/walls/0/baseline");
  });

  it("refuses a schema_version outside 1.x at /schema_version", () => {
    const refusal = refusalOf(
      mutateScene((scene) => {
        scene.schema_version = "2.0";
      }),
    );
    expect(refusal.code).toBe("unsupported_schema_version");
    expect(refusal.path).toBe("/schema_version");
  });

  it("accepts a newer 1.x minor with an untested-version warning", () => {
    const parsed = parseSceneText(
      mutateScene((scene) => {
        scene.schema_version = "1.1";
      }),
    );
    expect(parsed.version).toEqual({
      kind: "compatible_untested_minor",
      version: "1.1",
      testedVersion: "1.0",
    });
    expect(parsed.warnings).toHaveLength(1);
  });
});

describe("schema version classification", () => {
  it.each([
    [undefined, { kind: "supported", version: "1.0" }],
    ["1.0", { kind: "supported", version: "1.0" }],
    ["", { kind: "supported", version: "1.0" }],
    ["1.9", { kind: "compatible_untested_minor", version: "1.9", testedVersion: "1.0" }],
    ["2.0", { kind: "unsupported", version: "2.0" }],
    ["one", { kind: "unsupported", version: "one" }],
  ])("reportSceneVersion(%j) is %j", (input, expected) => {
    expect(reportSceneVersion(input)).toEqual(expected);
  });

  it("a result version other than the contract's one is refused, not guessed", () => {
    expect(reportResultVersion("1.0")).toEqual({ ok: true, version: "1.0" });
    expect(reportResultVersion("1.1")).toEqual({ ok: false, version: "1.1" });
    expect(reportResultVersion(undefined).ok).toBe(false);
  });
});
