// The placement inspector: everything the newest bound result actually says, plus the
// full attempt history and the session's witnesses, in one reviewable view.
//
// Two rules shape the copy here:
//
// - The inspector quotes the placement response; it never adds an opinion of its own.
//   The decision word is presented as the result's own claim ("the result's own
//   decision: pass"), and nothing in this component calls a placement safe, approved
//   or verified. Where the response is silent (a null spot or route), it says so
//   instead of filling the gap.
// - The attempt history is history. Entries for stale, mismatched, unreadable and
//   cancelled attempts carry their receipts and outcomes, each labelled as not the
//   current answer; a result that stayed in history (a stale or mismatched one) is
//   never rendered as a result here — only the fact that one exists and why it is not
//   displayed. Witnesses get their own block for the same reason.

import { failureHeadline } from "../lib/contract/errors.ts";
import type {
  SessionRecord,
  SessionWitness,
  SubmissionAttempt,
  SubmissionStatus,
} from "../lib/contract/session.ts";
import type {
  MissingEvidence,
  PlacementResult,
  ResultCheck,
  ResultRoute,
  ResultSpot,
} from "../lib/contract/types.ts";
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

function PointText({ point }: { readonly point: readonly [number, number] }) {
  return (
    <>
      [{point[0]}, {point[1]}]
    </>
  );
}

function shortAttemptId(attemptId: string): string {
  const marker = attemptId.indexOf(":");
  return marker === -1 ? attemptId : attemptId.slice(marker + 1);
}

const STATUS_PHRASES: Readonly<Record<SubmissionStatus, string>> = {
  submitting: "in flight",
  bound: "bound — the answer matches the uploaded bytes",
  mismatched: "mismatched — the answer named different bytes; recorded, never displayed",
  refused: "refused by the server",
  failed: "failed",
  cancelled: "cancelled before an answer arrived",
  stale: "superseded by a newer attempt",
};

function witnessLabel(kind: SessionWitness["kind"]): string {
  switch (kind) {
    case "mismatched_result":
      return "mismatched result";
    case "stale_outcome":
      return "late arrival for a superseded attempt";
    case "unparseable_result":
      return "unreadable 200 body";
    case "refused":
      return "refusal";
  }
}

function witnessDetail(witness: SessionWitness): string {
  switch (witness.kind) {
    case "mismatched_result":
      return `the request sent ${witness.requestSha256}; the answer claims ${witness.resultSha256}.`;
    case "stale_outcome":
      return `the outcome belongs to ${shortAttemptId(witness.attemptId)}, but ${shortAttemptId(witness.newestAttemptId)} had become the newest attempt.`;
    case "unparseable_result":
      return witness.reason;
    case "refused":
      return `${witness.code}: ${witness.detail}`;
  }
}

function outcomeLine(attempt: SubmissionAttempt, isCurrent: boolean): string {
  const outcome = attempt.outcome;
  if (outcome === null) {
    return "No outcome recorded yet.";
  }
  switch (outcome.type) {
    case "result":
      return outcome.binding.kind === "bound"
        ? "Carries a result bound to the exact uploaded bytes." +
            (isCurrent ? "" : " Its result stays in history and is not displayed as the answer.")
        : `Carries a result naming different bytes (${outcome.binding.resultSha256}) — kept as a witness, never displayed.`;
    case "unparseable_result":
      return `The 200 body could not be read as a result (${outcome.failure.reason}); kept as a witness, never displayed.`;
    case "refused":
      return `Refused with HTTP ${outcome.response.status} (${outcome.failure.kind === "server_error" ? outcome.failure.envelope.code : "non-envelope body"}).`;
    case "failed":
      return failureHeadline(outcome.failure);
    case "cancelled":
      return "No answer arrived; the receipt of what was sent is kept.";
  }
}

function CheckRow({ check }: { readonly check: ResultCheck }) {
  const measured =
    check.measured_ft === null
      ? null
      : check.comparison === null || check.threshold_ft === null
        ? ` Measured ${ft(check.measured_ft)}${check.plus_minus_ft === null ? "" : ` ± ${ft(check.plus_minus_ft)}`}.`
        : ` Measured ${ft(check.measured_ft)}${check.plus_minus_ft === null ? "" : ` ± ${ft(check.plus_minus_ft)}`} against a ${
            check.comparison === "at_least" ? "minimum" : "maximum"
          } of ${ft(check.threshold_ft)}.`;
  return (
    <li>
      <strong>{check.label}</strong> (<code>{check.id}</code>): <strong>{check.outcome}</strong>.{" "}
      {check.reason}
      {measured}
      {check.review_threshold_ft === undefined ? null : (
        <> A review threshold applies at {ft(check.review_threshold_ft)}.</>
      )}
      {check.subject === null ? null : (
        <>
          {" "}
          Subject: <code>{check.subject}</code>.
        </>
      )}{" "}
      Rule <code>{check.rule.key}</code> — source: {check.rule.source}
      {check.rule.placeholder ? " (placeholder value with no public source)" : ""}.
    </li>
  );
}

function SpotSummary({ spot }: { readonly spot: ResultSpot }) {
  return (
    <p>
      Wall <code>{spot.wall_id}</code> segment {spot.segment}, span <SpanText span={spot.span_ft} />
      , width {ft(spot.width_ft)}, depth {ft(spot.depth_ft)}, height {ft(spot.height_ft)}, center{" "}
      <PointText point={spot.center} />, outcome <strong>{spot.outcome}</strong>
      {spot.route_length_ft === null ? null : <>, route {ft(spot.route_length_ft)}</>}.
    </p>
  );
}

function RouteSummary({ route }: { readonly route: ResultRoute }) {
  return (
    <div>
      <p>
        Length {ft(route.length_ft)} ± {ft(route.plus_minus_ft)}, height {ft(route.height_ft)},
        outcome <strong>{route.outcome}</strong>
        {route.length_is_lower_bound === true
          ? " (a lower bound; the true length may be longer)"
          : ""}
        .
      </p>
      {route.detours === undefined || route.detours.length === 0 ? null : (
        <p>
          Detours:{" "}
          {route.detours.map((detour, index) => (
            <span key={`${detour.subject}-${detour.extra_ft}`}>
              {index === 0 ? "" : "; "}
              {detour.subject} adds {ft(detour.extra_ft)}
            </span>
          ))}
          .
        </p>
      )}
      {route.crossings === undefined || route.crossings.length === 0 ? null : (
        <p>
          Crossings:{" "}
          {route.crossings.map((crossing, index) => (
            <span key={`${crossing.subject}-${crossing.span_ft.join("-")}`}>
              {index === 0 ? "" : "; "}
              {crossing.subject} (<SpanText span={crossing.span_ft} />
              ): {crossing.effect}
            </span>
          ))}
          .
        </p>
      )}
    </div>
  );
}

function MissingEvidenceRow({ missing }: { readonly missing: MissingEvidence }) {
  return (
    <li>
      <code>{missing.kind}</code>
      {missing.band === undefined ? null : <> band {missing.band}</>}
      {missing.side === undefined ? null : <>, {missing.side} side</>}
      {missing.span_ft === undefined ? null : (
        <>
          , span <SpanText span={missing.span_ft} />
        </>
      )}
      {missing.out_ft === undefined ? null : <>, out to {ft(missing.out_ft)}</>}
      {missing.checks === undefined || missing.checks.length === 0 ? null : (
        <>; checks: {missing.checks.join(", ")}</>
      )}
      : {missing.message}
    </li>
  );
}

function ResultDetail({ result }: { readonly result: PlacementResult }) {
  return (
    <div>
      <p>
        The result's own decision: <strong>{result.decision}</strong>
      </p>
      <p>Summary: {result.summary}</p>
      <h4>Reasons</h4>
      {result.reasons.length === 0 ? (
        <p>The result gives no reasons.</p>
      ) : (
        <ul>
          {result.reasons.map((reason) => (
            <li key={reason.code}>
              <code>{reason.code}</code>: {reason.message}
              {reason.checks === undefined || reason.checks.length === 0 ? null : (
                <> (checks: {reason.checks.join(", ")})</>
              )}
            </li>
          ))}
        </ul>
      )}
      <h4>Spot</h4>
      {result.spot === null ? (
        <p>No spot fits the observed evidence.</p>
      ) : (
        <SpotSummary spot={result.spot} />
      )}
      {result.nearest_considered === undefined || result.nearest_considered === null ? null : (
        <p>
          Nearest considered spot, not chosen: wall <code>{result.nearest_considered.wall_id}</code>{" "}
          segment {result.nearest_considered.segment}, outcome{" "}
          <strong>{result.nearest_considered.outcome}</strong>.
        </p>
      )}
      <h4>Route</h4>
      {result.route === null ? (
        <p>No route was produced.</p>
      ) : (
        <RouteSummary route={result.route} />
      )}
      <h4>Checks</h4>
      {result.checks.length === 0 ? (
        <p>No checks ran.</p>
      ) : (
        <ul>
          {result.checks.map((check) => (
            <CheckRow key={check.id} check={check} />
          ))}
        </ul>
      )}
      <h4>Ends</h4>
      <p>
        Left end: {result.ends.left.kind} at {ft(result.ends.left.s_ft)} (point{" "}
        <PointText point={result.ends.left.point} />)
        {result.ends.left.beyond_reach === true ? ", beyond reach" : ""}; right end:{" "}
        {result.ends.right.kind} at {ft(result.ends.right.s_ft)} (point{" "}
        <PointText point={result.ends.right.point} />)
        {result.ends.right.beyond_reach === true ? ", beyond reach" : ""}.
      </p>
      {result.missing_evidence.length === 0 ? null : (
        <>
          <h4>Missing evidence</h4>
          <ul>
            {result.missing_evidence.map((missing) => (
              <MissingEvidenceRow key={`${missing.kind}-${missing.message}`} missing={missing} />
            ))}
          </ul>
        </>
      )}
      <h4>Policy</h4>
      <p>
        {result.policy.id === null ? "No named policy" : <code>{result.policy.id}</code>}
        {result.policy.version === null ? null : <> version {result.policy.version}</>},
        auto-approve {result.policy.auto_approve ? "on" : "off"}
        {result.policy.allow_reject === undefined ? null : (
          <> (allow_reject {result.policy.allow_reject ? "on" : "off"})</>
        )}
        , sources {result.policy.sources.join(", ")}, rules{" "}
        <code>{result.policy.rules_sha256}</code>.
        {result.policy.notice === null ? null : <> {result.policy.notice}</>}
      </p>
      {result.sweep.length === 0 ? null : (
        <>
          <h4>Sweep</h4>
          <ul>
            {result.sweep.map((run) => (
              <li key={`${run.wall_id}-${run.start_ft.join("-")}-${run.segment ?? ""}`}>
                Wall <code>{run.wall_id}</code>
                {run.segment === undefined ? null : <> segment {run.segment}</>}, start{" "}
                <SpanText span={run.start_ft} />: <strong>{run.outcome}</strong>
                {run.failing.length === 0 ? null : <>; failing {run.failing.join(", ")}</>}
                {run.unsure.length === 0 ? null : <>; unsure {run.unsure.join(", ")}</>}
              </li>
            ))}
          </ul>
        </>
      )}
      <h4>Statistics</h4>
      <p>
        {result.stats.candidates} candidate{result.stats.candidates === 1 ? "" : "s"}:{" "}
        {result.stats.pass} pass, {result.stats.unsure} unsure, {result.stats.fail} fail, in{" "}
        {result.stats.elapsed_ms} ms. Bound to upload <code>{result.stats.input_sha256}</code>.
      </p>
    </div>
  );
}

function AttemptRow({
  attempt,
  isCurrent,
}: {
  readonly attempt: SubmissionAttempt;
  readonly isCurrent: boolean;
}) {
  return (
    <li>
      <strong>
        Attempt {shortAttemptId(attempt.attemptId)} — {STATUS_PHRASES[attempt.status]}
      </strong>
      {isCurrent
        ? " (the attempt whose bound result is displayed above)"
        : ". Not the current answer."}
      <br />
      Sent {attempt.request.method} {attempt.request.url}, {attempt.request.byteLength} bytes,
      SHA-256 <code>{attempt.request.requestSha256}</code>.
      <br />
      {outcomeLine(attempt, isCurrent)}
    </li>
  );
}

export function Inspector({
  display,
  session,
}: {
  readonly display: DisplayView;
  readonly session: SessionRecord | null;
}) {
  const newest = session?.attempts.at(-1) ?? null;
  return (
    <section aria-labelledby="inspector-heading">
      <h3 id="inspector-heading">Placement inspector</h3>
      {display.kind === "bound" && newest?.outcome?.type === "result" ? (
        <div>
          <h4>Current result</h4>
          <p>
            Everything below is quoted from the newest attempt's placement response. This app adds
            no opinion of its own: the decision word is the result's own claim, not an assurance by
            this page.
          </p>
          <ResultDetail result={display.result} />
        </div>
      ) : (
        <p>
          No bound result is displayed. The newest attempt's state: {display.kind}. The attempt
          history below is still the record of what happened.
        </p>
      )}
      {session === null ? null : (
        <>
          <h4>Attempt history</h4>
          <p>
            Every attempt is recorded with the receipts of what was sent and received. These entries
            are history, not answers: only the newest attempt's bound result is ever presented as
            current, and an entry here never promotes itself to one.
          </p>
          {session.attempts.length === 0 ? (
            <p>No attempts have run in this session.</p>
          ) : (
            <ol>
              {session.attempts.map((attempt) => (
                <AttemptRow
                  key={attempt.attemptId}
                  attempt={attempt}
                  isCurrent={
                    attempt.attemptId === newest?.attemptId &&
                    attempt.status === "bound" &&
                    display.kind === "bound"
                  }
                />
              ))}
            </ol>
          )}
          {session.witnesses.length === 0 ? null : (
            <>
              <h4>Witnesses</h4>
              <p>
                Answers and failures that were seen but not applied, with the reason. A witness is a
                record for review; it is never displayed as an answer.
              </p>
              <ul>
                {session.witnesses.map((witness) => (
                  <li key={`${witness.kind}-${witness.attemptId}`}>
                    <strong>{witnessLabel(witness.kind)}</strong> (attempt{" "}
                    {shortAttemptId(witness.attemptId)}): {witnessDetail(witness)}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}
