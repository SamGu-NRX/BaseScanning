// The outcome panel: renders exactly what the store's displayed view carries. It is
// never handed an attempt list, so it cannot show an older attempt's answer even by
// accident — the store's displayedView already restricted the view to the newest
// attempt, and the result in it only when that attempt is bound. A mismatched answer
// shows as the fact of a mismatch (hashes, witness note), never as a result; a
// refusal shows the server's own message with the JSON pointer of the field at fault.

import { failureHeadline } from "../lib/contract/errors.ts";
import type { PlacementResult } from "../lib/contract/types.ts";
import type { DisplayView } from "../lib/session/store.ts";

function ft(n: number): string {
  return `${n} ft`;
}

function SpanText({ span }: { readonly span: readonly [number, number] }) {
  return (
    <>
      {ft(span[0])} to {ft(span[1])}
    </>
  );
}

function BoundResult({ view }: { view: Extract<DisplayView, { kind: "bound" }> }) {
  const result = view.result;
  return <BoundResultBody result={result} additiveNotes={view.additiveNotes} />;
}

function BoundResultBody({
  result,
  additiveNotes,
}: {
  result: PlacementResult;
  additiveNotes: readonly string[];
}) {
  return (
    <div>
      <p>
        Decision: <strong>{result.decision}</strong>
      </p>
      <p>{result.summary}</p>
      <h3>Reasons</h3>
      <ul>
        {result.reasons.map((reason) => (
          <li key={reason.code}>
            <code>{reason.code}</code>: {reason.message}
          </li>
        ))}
      </ul>
      <h3>Spot</h3>
      {result.spot === null ? (
        <p>No spot fits the observed evidence.</p>
      ) : (
        <p>
          Wall <code>{result.spot.wall_id}</code> segment {result.spot.segment}, span{" "}
          <SpanText span={result.spot.span_ft} />, width {ft(result.spot.width_ft)}, depth{" "}
          {ft(result.spot.depth_ft)}, height {ft(result.spot.height_ft)}, center (
          {result.spot.center[0]}, {result.spot.center[1]}), outcome{" "}
          <strong>{result.spot.outcome}</strong>.
        </p>
      )}
      <h3>Route</h3>
      {result.route === null ? (
        <p>No route was produced.</p>
      ) : (
        <p>
          Length {ft(result.route.length_ft)} plus or minus {ft(result.route.plus_minus_ft)}, height{" "}
          {ft(result.route.height_ft)}, outcome <strong>{result.route.outcome}</strong>
          {result.route.length_is_lower_bound === true
            ? " (a lower bound; the true length may be longer)"
            : null}
          .
        </p>
      )}
      <h3>Checks</h3>
      {result.checks.length === 0 ? (
        <p>No checks ran.</p>
      ) : (
        <ul>
          {result.checks.map((check) => (
            <li key={check.id}>
              <strong>{check.label}</strong>: {check.outcome}. {check.reason}
              {check.measured_ft === null || check.threshold_ft === null
                ? null
                : check.comparison === null
                  ? ""
                  : check.comparison === "at_least"
                    ? ` Measured ${ft(check.measured_ft)} against a minimum of ${ft(check.threshold_ft)}.`
                    : ` Measured ${ft(check.measured_ft)} against a maximum of ${ft(check.threshold_ft)}.`}
            </li>
          ))}
        </ul>
      )}
      <h3>Policy</h3>
      <p>
        {result.policy.id === null ? "No named policy" : <code>{result.policy.id}</code>}
        {result.policy.version === null ? null : <> version {result.policy.version}</>}, sources{" "}
        {result.policy.sources.join(", ")}, rules <code>{result.policy.rules_sha256}</code>.
        {result.policy.notice === null ? null : <> {result.policy.notice}</>}
      </p>
      {result.missing_evidence.length === 0 ? null : (
        <>
          <h3>Missing evidence</h3>
          <ul>
            {result.missing_evidence.map((missing) => (
              <li key={missing.message}>{missing.message}</li>
            ))}
          </ul>
        </>
      )}
      {additiveNotes.length === 0 ? null : (
        <>
          <h3>Notes on this answer</h3>
          <ul>
            {additiveNotes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </>
      )}
      <h3>Statistics</h3>
      <p>
        {result.stats.candidates} candidate{result.stats.candidates === 1 ? "" : "s"}:{" "}
        {result.stats.pass} pass, {result.stats.unsure} unsure, {result.stats.fail} fail, in{" "}
        {result.stats.elapsed_ms} ms.
      </p>
      <p>
        Bound to upload <code>{result.stats.input_sha256}</code>
      </p>
    </div>
  );
}

export function OutcomePanel({ display }: { display: DisplayView }) {
  switch (display.kind) {
    case "no_scene":
      return (
        <div>
          <h3>Outcome</h3>
          <p>Select a scene.json file to begin a session.</p>
        </div>
      );
    case "scene_ready":
      return (
        <div>
          <h3>Outcome</h3>
          <p>The scene is ready. No attempts have run yet.</p>
        </div>
      );
    case "submitting":
      return (
        <div aria-busy="true">
          <h3>Outcome</h3>
          <p>The attempt is in flight. Cancelling aborts the request.</p>
        </div>
      );
    case "bound":
      return (
        <div>
          <h3>Result</h3>
          <BoundResult view={display} />
        </div>
      );
    case "mismatched":
      return (
        <div>
          <h3>Mismatched answer</h3>
          <p>
            The server answered 200 with a result that names different scene bytes. It is recorded
            on the attempt as a witness, and is not displayed as this submission's answer.
          </p>
          <p>
            The request sent <code>{display.requestSha256}</code>; the answer claims{" "}
            <code>{display.resultSha256}</code>.
          </p>
        </div>
      );
    case "refused": {
      const failure = display.failure;
      return (
        <div>
          <h3>Refusal</h3>
          <p role="alert">{failureHeadline(failure)}</p>
          {failure.kind === "server_error" ? (
            <p>
              The server said (<code>{failure.envelope.code}</code>, HTTP {failure.status}):{" "}
              {failure.envelope.message}
              {failure.envelope.path === null ? null : (
                <>
                  {" "}
                  At <code>{failure.envelope.path}</code>.
                </>
              )}
            </p>
          ) : failure.kind === "unparseable_error" ? (
            <p>{failure.detail}</p>
          ) : null}
        </div>
      );
    }
    case "failed": {
      const failure = display.failure;
      return (
        <div>
          <h3>Failure</h3>
          <p role="alert">{failureHeadline(failure)}</p>
          {failure.kind === "network_error" ? <p>{failure.detail}</p> : null}
        </div>
      );
    }
    case "unparseable_result":
      return (
        <div>
          <h3>Unreadable answer</h3>
          <p role="alert">
            The server answered 200 with something this client cannot read as a result. The body is
            recorded on the attempt as a witness, and nothing is displayed as the answer.
          </p>
          <p>{display.detail}</p>
        </div>
      );
    case "cancelled":
      return (
        <div>
          <h3>Cancelled</h3>
          <p>
            The attempt was cancelled before an answer arrived. The request it sent stays in the
            session's history with its receipt.
          </p>
        </div>
      );
    case "superseded":
      return (
        <div>
          <h3>Superseded</h3>
          <p>This attempt was superseded; its record stays in the session's history.</p>
        </div>
      );
  }
}
