// The placement session page. All state lives in the session store (one SessionRecord
// at a time); React subscribes through useSyncExternalStore and renders the store's
// snapshot. The page owns the M2 controls (server fields, the scene picker, the attempt
// status line, run/cancel, the outcome panel that can only ever show the newest
// attempt's bound outcome or refusal) and the M3 controls: the placement inspector
// (the newest bound result in full, plus attempt history and witnesses kept clearly
// separate from the current answer) and the session record controls (browser-local
// persistence with reopen, and redacted export/import).

import { useCallback, useRef, useSyncExternalStore } from "react";
import { Inspector } from "./components/Inspector.tsx";
import { OutcomePanel } from "./components/OutcomePanel.tsx";
import { ScenePicker } from "./components/ScenePicker.tsx";
import { ServerFields } from "./components/ServerFields.tsx";
import { SessionPanel } from "./components/SessionPanel.tsx";
import { SessionStore, statusLine } from "./lib/session/store.ts";

export function App() {
  const storeRef = useRef<SessionStore | null>(null);
  if (storeRef.current === null) {
    storeRef.current = new SessionStore();
  }
  const store = storeRef.current;
  const subscribe = useCallback((listener: () => void) => store.subscribe(listener), [store]);
  const getSnapshot = useCallback(() => store.getSnapshot(), [store]);
  const state = useSyncExternalStore(subscribe, getSnapshot);

  const submitting = state.display.kind === "submitting";
  const canSubmit = state.scene !== null && !submitting;

  const handleExport = useCallback(() => {
    const json = store.exportSession();
    if (json === null) {
      return;
    }
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `basescanning-session-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }, [store]);

  return (
    <main>
      <h1>House Scan: web capture</h1>
      <p>Browser capture experiments live here.</p>
      <section aria-labelledby="session-heading">
        <h2 id="session-heading">Placement session</h2>
        <ServerFields
          baseUrl={state.baseUrl}
          hasToken={state.hasToken}
          onBaseUrlChange={(url) => store.setBaseUrl(url)}
          onTokenChange={(token) => store.setToken(token)}
        />
        <ScenePicker
          scene={state.scene}
          intakeError={state.intakeError}
          onSelect={(file) => void store.selectSceneFile(file)}
        />
        <SessionPanel
          saved={state.savedSession}
          restoreError={state.restoreError}
          storageWarning={state.storageWarning}
          canExport={state.session !== null}
          busy={submitting}
          onReopen={() => void store.reopenSession()}
          onDiscard={() => store.discardSavedSession()}
          onExport={handleExport}
          onImport={(file) => {
            void file.text().then((text) => store.importSession(text));
          }}
        />
        <p role="status">{statusLine(state.display)}</p>
        <p>
          <button type="button" disabled={!canSubmit} onClick={() => void store.submit()}>
            Run placement
          </button>{" "}
          <button type="button" disabled={!submitting} onClick={() => store.cancel()}>
            Cancel
          </button>
        </p>
        <OutcomePanel display={state.display} />
        <Inspector display={state.display} session={state.session} />
      </section>
    </main>
  );
}
