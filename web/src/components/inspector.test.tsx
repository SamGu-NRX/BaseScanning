// Inspector render tests: the component is rendered with react-dom/server against
// snapshots produced by driving the session store through the contract fake — the
// only server surface here (no real network, no DOM). The assertions check what the
// page would actually show: every section of the newest bound result quoted from the
// response, the attempt history labelled as history, and witnesses kept visibly
// separate from the current answer — never promoted to it.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { createContractFake, RULES_SHA256, sceneText } from "../lib/contract/contract-fake.ts";
import { sha256HexOfText } from "../lib/contract/hash.ts";
import type { SessionRecord } from "../lib/contract/session.ts";
import { type DisplayView, type SceneFileInput, SessionStore } from "../lib/session/store.ts";
import { Inspector } from "./Inspector.tsx";

function fileNamed(name: string, text: string = sceneText()): SceneFileInput {
  return { name, text: async () => text };
}

/**
 * One session carrying the witnesses the inspector must keep separate: a refusal, a
 * mismatched answer, an unreadable 200 body, a bound answer, and a superseded late
 * arrival — ending on a bound attempt so the display has a current result.
 */
async function sessionWithWitnessHistory(): Promise<{
  display: DisplayView & { readonly kind: "bound" };
  session: SessionRecord | null;
}> {
  let current = createContractFake({
    behavior: {
      kind: "refused",
      status: 422,
      code: "policy_violation",
      message: "No placement for this scene.",
      path: null,
    },
  });
  const fetchImpl: typeof fetch = (input, init) => current.fetch(input, init);
  const store = new SessionStore({ fetchImpl, storage: null });
  await store.selectSceneFile(fileNamed("scene.json"));

  await store.submit(); // 1: refused
  current = createContractFake({ behavior: { kind: "mismatched_sha" } });
  await store.submit(); // 2: mismatched answer, witnessed
  current = createContractFake({ behavior: { kind: "unparseable_200" } });
  await store.submit(); // 3: unreadable 200 body, witnessed
  current = createContractFake();
  await store.submit(); // 4: bound — the current answer
  current = createContractFake({ behavior: { kind: "delayed_then_abort", delayMs: 40 } });
  const late = store.submit(); // 5: superseded mid-flight by 6
  current = createContractFake();
  await store.submit(); // 6: bound — still the newest
  await late; // the late outcome lands: witnessed as stale

  const snap = store.getSnapshot();
  if (snap.display.kind !== "bound") throw new Error("the final attempt should be bound");
  return { display: snap.display, session: snap.session };
}

describe("inspector renders the newest bound result from the response", () => {
  it("shows the decision as the result's own claim, with checks, spot, route, ends and policy", async () => {
    const snap = await sessionWithWitnessHistory();
    const html = renderToStaticMarkup(<Inspector display={snap.display} session={snap.session} />);

    // The decision is quoted, attributed to the result, once.
    expect(html).toContain("Placement inspector");
    expect(html).toContain("The result&#x27;s own decision");
    expect(html.match(/The result&#x27;s own decision/gu)?.length).toBe(1);
    expect(html).toContain("<strong>pass</strong>");
    expect(html).toContain(
      "Summary: The north wall fits a battery between 3.0 ft and 5.5 ft from the meter.",
    );

    // Reasons with their codes.
    expect(html).toContain("all_checks_pass");
    expect(html).toContain("Every check at the chosen spot passes with the observed evidence.");

    // The check: label, id, outcome, reason, measurement vs threshold, subject, rule citation.
    expect(html).toContain("Gas clearance");
    expect(html).toContain("gas_clearance");
    expect(html).toContain(
      "The nearest gas meter is 2.0 ft away, 1.0 ft past the required clearance.",
    );
    expect(html).toContain("Measured 2 ft ± 0.1 ft against a minimum of 1 ft.");
    expect(html).toContain("objects[0] gas_meter");
    expect(html).toContain("clearances.gas_ft");
    expect(html).toContain("synthetic fixture value");
    expect(html).toContain("(placeholder value with no public source)");

    // The spot summary when present.
    expect(html).toContain("Wall <code>north</code> segment 0");
    expect(html).toContain("span 3 ft to 5.5 ft");
    expect(html).toContain("width 2.5 ft, depth 1.2 ft, height 2 ft");
    expect(html).toContain("center [4.25, 0.6]");
    expect(html).toContain("route 4.25 ft");

    // The route summary when present.
    expect(html).toContain("Length 4.25 ft ± 0.3 ft, height 1 ft");

    // The ends, with their points.
    expect(html).toContain("Left end: limit at -10 ft (point [-10, 0])");
    expect(html).toContain("right end: unexplored at 10 ft (point [10, 0])");

    // The policy block: id, auto-approve, sources, rules hash, notice.
    expect(html).toContain("public-demo");
    expect(html).toContain("auto-approve on");
    expect(html).toContain("sources public");
    expect(html).toContain(RULES_SHA256);
    expect(html).toContain("Demo rules, not the utility&#x27;s.");

    // Statistics, bound to the upload's own hash.
    expect(html).toContain("1 candidate: 1 pass, 0 unsure, 0 fail, in 12 ms");
    expect(html).toContain(await sha256HexOfText(sceneText()));
  });

  it("renders missing-evidence entries with their band, span and referenced checks", async () => {
    const snap = await sessionWithWitnessHistory();
    const withMissing = {
      ...snap.display,
      result: {
        ...snap.display.result,
        missing_evidence: [
          {
            kind: "band" as const,
            band: "ground" as const,
            span_ft: [-6, 2] as [number, number],
            out_ft: 4,
            checks: ["clearance_gas_ft", "setback_ft"],
            message: "The ground beyond 6 ft was not observed; the span there is unverified.",
          },
          {
            kind: "past_end" as const,
            band: "overhead" as const,
            side: "left" as const,
            message: "No overhead clearance was observed on the left end.",
          },
        ],
      },
    };
    const html = renderToStaticMarkup(<Inspector display={withMissing} session={snap.session} />);
    expect(html).toContain("Missing evidence");
    expect(html).toContain("<code>band</code> band ground");
    expect(html).toContain("span -6 ft to 2 ft");
    expect(html).toContain("out to 4 ft");
    expect(html).toContain("checks: clearance_gas_ft, setback_ft");
    expect(html).toContain(
      "The ground beyond 6 ft was not observed; the span there is unverified.",
    );
    expect(html).toContain("<code>past_end</code> band overhead, left side");
    expect(html).toContain("No overhead clearance was observed on the left end.");
  });

  it("says so when the response has no spot or no route instead of filling the gap", async () => {
    const snap = await sessionWithWitnessHistory();
    const emptyish = {
      ...snap.display,
      result: { ...snap.display.result, spot: null, route: null, nearest_considered: null },
    };
    const html = renderToStaticMarkup(<Inspector display={emptyish} session={snap.session} />);
    expect(html).toContain("No spot fits the observed evidence.");
    expect(html).toContain("No route was produced.");
  });
});

describe("inspector keeps history and witnesses separate from the current answer", () => {
  it("records refused, mismatched, unreadable and stale attempts without promoting any of them", async () => {
    const snap = await sessionWithWitnessHistory();
    const html = renderToStaticMarkup(<Inspector display={snap.display} session={snap.session} />);

    // The attempt history block exists and labels itself as history.
    expect(html).toContain("Attempt history");
    expect(html).toContain("These entries are history, not answers");

    // Each non-current attempt is explicitly not the current answer.
    expect(html.match(/Not the current answer\./gu)?.length).toBe(5);

    // The refusal: status phrase, the server's code and message.
    expect(html).toContain("refused by the server");
    expect(html).toContain("policy_violation");
    expect(html).toContain("No placement for this scene.");

    // The mismatched answer: recorded with both hashes, never displayed as a result.
    expect(html).toContain(
      "mismatched — the answer named different bytes; recorded, never displayed",
    );
    expect(html).toContain("kept as a witness, never displayed");
    expect(html).toContain("mismatched result");
    expect(html).toContain("the request sent");
    expect(html).toContain("the answer claims");

    // The unreadable 200 body.
    expect(html).toContain("failed</strong>");
    expect(html).toContain("unreadable 200 body");
    expect(html).toContain("not_json");

    // The superseded late arrival: its outcome is history, the newer attempt is current.
    expect(html).toContain("superseded by a newer attempt");
    expect(html).toContain("late arrival for a superseded attempt");

    // The witnesses block: present, with the non-promotion rule stated.
    expect(html).toContain("Witnesses");
    expect(html).toContain("it is never displayed as an answer");

    // Exactly one decision quote — the current result's own claim.
    expect(html.match(/The result&#x27;s own decision/gu)?.length).toBe(1);
    expect(html).toContain("the attempt whose bound result is displayed above");
  });

  it("shows the history with no bound result when the newest attempt never produced one", async () => {
    const fake = createContractFake({ behavior: { kind: "unparseable_200" } });
    const store = new SessionStore({ fetchImpl: fake.fetch, storage: null });
    await store.selectSceneFile(fileNamed("scene.json"));
    await store.submit();
    const snap = store.getSnapshot();
    expect(snap.display.kind).toBe("unparseable_result");
    const html = renderToStaticMarkup(<Inspector display={snap.display} session={snap.session} />);
    expect(html).toContain("No bound result is displayed.");
    expect(html).toContain("The attempt history below is still the record of what happened.");
    expect(html).toContain("unreadable 200 body");
    // And no decision quote exists anywhere: there is no result to quote.
    expect(html).not.toContain("The result&#x27;s own decision");
  });
});
