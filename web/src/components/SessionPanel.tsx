// The session record controls: reopen the browser-local saved session, discard it,
// export the current session as redacted JSON, and import a previously exported one.
// The panel states the redaction boundary where the record is offered: the saved and
// exported shapes carry no credential field at all, and reopening never restores a
// bearer token (tokens are in-memory for the page's lifetime only).

import type { SceneIntakeIssue } from "../lib/contract/scene.ts";
import type { SavedSessionSummary } from "../lib/session/persist.ts";
import type { SceneFileInput } from "../lib/session/store.ts";

interface SessionPanelProps {
  readonly saved: SavedSessionSummary | null;
  readonly restoreError: SceneIntakeIssue | null;
  readonly storageWarning: string | null;
  readonly canExport: boolean;
  readonly busy: boolean;
  readonly onReopen: () => void;
  readonly onDiscard: () => void;
  readonly onExport: () => void;
  readonly onImport: (file: SceneFileInput) => void;
}

export function SessionPanel({
  saved,
  restoreError,
  storageWarning,
  canExport,
  busy,
  onReopen,
  onDiscard,
  onExport,
  onImport,
}: SessionPanelProps) {
  return (
    <fieldset>
      <legend>Saved session</legend>
      <p>
        While a session is open, this browser keeps its record locally (scene bytes, attempts,
        receipts, witnesses) so it can be reopened after a reload. The record holds no bearer token
        and no headers — reopening never restores a token.
      </p>
      {storageWarning === null ? null : <p role="alert">{storageWarning}</p>}
      {restoreError === null ? null : (
        <p role="alert">
          The record was refused: {restoreError.message} (<code>{restoreError.code}</code>)
          {restoreError.path === null ? null : (
            <>
              {" "}
              At <code>{restoreError.path}</code>.
            </>
          )}
        </p>
      )}
      {saved === null ? null : (
        <p>
          A saved session exists: <strong>{saved.label}</strong>, {saved.attemptCount} attempt
          {saved.attemptCount === 1 ? "" : "s"}, saved {new Date(saved.savedAt).toISOString()}.
        </p>
      )}
      <p>
        {saved === null ? null : (
          <>
            <button type="button" disabled={busy} onClick={onReopen}>
              Reopen saved session
            </button>{" "}
            <button type="button" onClick={onDiscard}>
              Discard saved session
            </button>{" "}
          </>
        )}
        <button type="button" disabled={!canExport} onClick={onExport}>
          Export session (redacted JSON)
        </button>{" "}
        <label htmlFor="session-import">Import session record (.json)</label>{" "}
        <input
          id="session-import"
          type="file"
          accept=".json,application/json"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.currentTarget.value = "";
            if (file !== undefined) {
              onImport(file);
            }
          }}
        />
      </p>
    </fieldset>
  );
}
