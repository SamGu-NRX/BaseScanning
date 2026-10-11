// The placement session page. All state lives in the session store (one SessionRecord
// at a time); React subscribes through useSyncExternalStore and renders the store's
// snapshot. The page owns exactly the M2 controls: server fields, the scene picker,
// the attempt status line, the run/cancel buttons, and the outcome panel that can
// only ever show the newest attempt's bound outcome or refusal.

import { useCallback, useRef, useSyncExternalStore } from "react";
import { OutcomePanel } from "./components/OutcomePanel.tsx";
import { ScenePicker } from "./components/ScenePicker.tsx";
import { ServerFields } from "./components/ServerFields.tsx";
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
      </section>
    </main>
  );
}
