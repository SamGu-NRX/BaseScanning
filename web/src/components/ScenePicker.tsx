// The scene picker: a file input whose selection the store intakes. The chosen file's
// exact text is what gets hashed and uploaded, so the summary here reports the intake
// facts (schema version, byte length, SHA-256) rather than re-serializing anything.
// A selection the client refuses locally is shown with the same code/message/path
// shape the server uses; it never clears a session already in progress.

import type { SceneIntakeIssue } from "../lib/contract/scene.ts";
import type { SceneFileInput, SceneSummary } from "../lib/session/store.ts";

function versionText(version: SceneSummary["version"]): string {
  switch (version.kind) {
    case "supported":
      return `${version.version} (tested by this build)`;
    case "compatible_untested_minor":
      return `${version.version} (newer minor; this build tested ${version.testedVersion})`;
    case "unsupported":
      return `${version.version} (unsupported by this client)`;
  }
}

interface ScenePickerProps {
  readonly scene: SceneSummary | null;
  readonly intakeError: SceneIntakeIssue | null;
  readonly onSelect: (file: SceneFileInput) => void;
}

export function ScenePicker({ scene, intakeError, onSelect }: ScenePickerProps) {
  return (
    <fieldset>
      <legend>Scene</legend>
      <p>
        <label htmlFor="scene-file">Scene file (.json)</label>
        <br />
        <input
          id="scene-file"
          type="file"
          accept=".json,application/json"
          onChange={(event) => {
            const file = event.target.files?.[0];
            // Reset so picking the same file again still fires a change.
            event.currentTarget.value = "";
            if (file !== undefined) {
              onSelect(file);
            }
          }}
        />
      </p>
      {intakeError === null ? null : (
        <p role="alert">
          The scene file was refused: {intakeError.message}
          {intakeError.path === null ? null : (
            <>
              {" "}
              At <code>{intakeError.path}</code>.
            </>
          )}{" "}
          (<code>{intakeError.code}</code>)
        </p>
      )}
      {scene === null ? null : (
        <div>
          <p>
            Loaded <strong>{scene.fileName}</strong>, schema version {versionText(scene.version)},{" "}
            {scene.byteLength} bytes.
          </p>
          <p>
            Upload SHA-256: <code>{scene.sha256}</code>
          </p>
          {scene.warnings.length === 0 ? null : (
            <ul>
              {scene.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </fieldset>
  );
}
