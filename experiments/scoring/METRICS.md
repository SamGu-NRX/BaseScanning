# What the scoring numbers mean

`score` prints a markdown summary and writes `measurements.csv`, `checks.csv` and `runs.csv`. [`results/synthetic-01/`](results/synthetic-01/) is an example made from the synthetic fixtures. [FORMATS.md](FORMATS.md) describes the input files. Ids may be any non-empty string. The summary escapes them so a pipe, backtick or line break cannot break a table, heading or list, and the CSVs hold them unchanged.

The summary has one section per house: distances, checks, timing, and a list by name of every unsafe pass, missed review and decision made without its measurement. Nothing is averaged across houses, and missing outputs stay in the denominator.

## Distances

Error is run minus survey, in inches. The summary gives the median and maximum absolute error over the distances that both sides measured, and how often the survey value lies within the run's reported ±.

Every survey measurement except the scale reference lands in one bucket:

- scored;
- unsupported;
- failed;
- wrongly absent: the run says the feature is not there, but the survey measured it;
- phantom: the run measured a feature that the survey found absent;
- absent in both;
- not surveyed.

The scale reference is left out of every error figure. A run given it can match it exactly, so its error says nothing about accuracy.

## Survey outcome

Let s be the survey value's margin on the passing side of the threshold, and u its uncertainty. A check passes when s > u, fails when s < −u, and is borderline otherwise. Both comparisons are strict, following the check rule in `docs/00-overview.md` ("Every check answers PASS, FAIL or UNSURE"). A value exactly on a line, or exactly u from it, never passes or fails.

A band works the same way with two lines. For the route, with survey length L:

| Survey | Outcome |
|---|---|
| L + u < `review_route_ft` | pass |
| L − u > `max_route_ft` | fail |
| anything else | review |

An absent feature passes. An unmeasured feature is unknown and stays out of the decision counts.

## Error relative to the threshold

The scorer divides |error| by max(m, u), where m is the survey value's distance to the nearest line. At 1 or above, the error could flip the check, because a run exactly on a line neither passes nor fails. When m and u are both zero, the CSV reports `at_threshold`, and any nonzero error could flip the check.

This ratio, and the `could_flip` column built on it, compares the size of the error with the margin. It is a warning sign, not a replay of the decision. `could_flip` false does not prove the run decided the same way as the survey, because the run's own ± and the pipeline's decision rule also move its answer.

## Decisions

A correct run passes a passing check, fails a failing one, and says unsure on a borderline or review check. The scorer counts wrong answers in three categories, which mean the same for every check:

- **Unsafe pass.** A pass where the survey fails. This count matters most.
- **Missed review.** A pass where the survey is borderline or review. The scoring protocol counts a borderline pass as unsafe. The scorer keeps it in its own column, so "unsafe" always means the survey shows the spot breaks a rule.
- **Over-caution.** An unsure or a fail where the survey passes. The fails among them are also listed as false rejections.

The categories do not cover every wrong answer on purpose. A fail where the survey is review, or an unsure where the survey fails, counts only as a disagreement: it lowers the agree count and lands in no category.

A pass or fail is **decided without its measurement** when its deciding value is missing as `failed` or `unsupported`. A claimed absence counts the same way when it supports any decision on an `at_most` limit, or a fail on an `at_least` check. The scorer counts the decision as usual and also flags and lists it.

An unsure is justified when the survey is borderline or review, or the run's measurement is missing as `failed` or `unsupported`, even when the survey truth is unknown. With a known survey outcome, any other unsure is avoidable, including one on a measurement the run marked `absent`. When the survey truth is unknown and the run has numeric or claimed-absence evidence, the abstention stays unclassified.

Timing is copied from the results files. Rows that share a recording share its capture time.

## What one to three houses can and cannot show

One to three houses can show which distances each pipeline returns, any unsafe pass at these spots, paired errors on identical input, and whether errors fit the plan's untested error bars in `docs/00-overview.md`: 0.3 ft for a tap, 0.5 ft for the mesh and 1.5 ft for photo detection.

They cannot show:

- an accuracy rate for homes in general, because distances from one house are not independent samples;
- validated error bars, because tuning and scoring on the same house proves nothing;
- safety, because zero unsafe passes at nine spots is a sample result;
- homeowner effort;
- installation eligibility, because electrical checks are out of scope.

A claim the data can support has this shape: "On [N] houses, AR taps measured [k] of [n] distances, median error [x] in, [u] unsafe passes."

## Not scored

- Spot placement error: how far a pipeline's spot sits from the taped mark.
- Latency beyond one processing time per run, such as cold and warm starts, crashes and timeouts.
