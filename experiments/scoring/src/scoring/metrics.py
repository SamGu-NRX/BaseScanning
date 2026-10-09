"""Compare one pipeline run with the tape survey of the same house.

Pure functions over the loaded inputs. Every figure is exact Decimal arithmetic on the numbers as
typed, so a tie with a threshold or with a reported uncertainty resolves the same way on every
machine. Errors are reported in inches; survey values, uncertainties and thresholds stay in feet.
"""

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Literal

from scoring.inputs import (
    Check,
    Outcome,
    PipelineMeasurement,
    Results,
    SurveyMeasurement,
    Threshold,
    Truth,
)

INCHES_PER_FOOT = Decimal(12)

MeasurementStatus = Literal[
    "scored",  # both the survey and the run have a value
    "scale_reference",  # the house's declared scale distance: never scored
    "not_surveyed",  # the survey could not reach it, so nothing can be said about the run
    "missing_unsupported",  # the run has no way to produce this distance
    "missing_failed",  # the run tried and produced nothing
    "false_absent",  # the run says the feature does not exist; the survey measured it
    "phantom",  # the run gives a distance to a feature the survey found absent
    "absent_agreed",  # both say the feature does not exist
]

# pass and fail are outside the survey's uncertainty. borderline: the survey itself is unsure,
# because its ± reaches a check's single threshold. review: the survey value, or its ±, lies in a
# check's review band. unknown: the survey could not measure the deciding distance.
TruthOutcome = Literal["pass", "fail", "borderline", "review", "unknown"]
Abstention = Literal["justified", "avoidable"]
# The error ratio when the survey sits exactly on a threshold with no uncertainty: no denominator.
AtThreshold = Literal["at_threshold"]
AT_THRESHOLD: AtThreshold = "at_threshold"


@dataclass(frozen=True)
class MeasurementScore:
    """One survey measurement paired with the run's entry for the same id.

    `status` sorts the pair into one of the buckets METRICS.md scores under "Distances". Only
    a `scored` pair carries an error or a truth_within_reported verdict, so the distance figures
    in the summary and measurements.csv come from the scored pairs.
    """

    survey: SurveyMeasurement
    reported: PipelineMeasurement | None
    status: MeasurementStatus
    # Run minus survey, so a positive error means the run overestimated. None unless scored.
    error_ft: Decimal | None
    # |run - survey| <= the run's reported uncertainty. None when not scored or none reported.
    truth_within_reported: bool | None

    @property
    def signed_error_in(self) -> Decimal | None:
        """The error in inches, sign kept. None when the pair was not scored."""
        return None if self.error_ft is None else self.error_ft * INCHES_PER_FOOT

    @property
    def abs_error_in(self) -> Decimal | None:
        """The error's size in inches, sign dropped. None when the pair was not scored."""
        return None if self.error_ft is None else abs(self.error_ft) * INCHES_PER_FOOT


def score_measurement(
    survey: SurveyMeasurement, reported: PipelineMeasurement | None, *, scale_reference: bool
) -> MeasurementScore:
    """Pair a survey measurement with the run's entry for it and classify the pair.

    `scale_reference` marks the house's scale distance, which is never scored: a run told to use
    it can match it exactly, so its error says nothing about accuracy (METRICS.md, "Distances").
    Raises ValueError when the survey has a definite result, measured or absent, and the run has
    no entry for it.
    """
    status = _measurement_status(survey, reported, scale_reference=scale_reference)
    if status != "scored":
        return MeasurementScore(survey, reported, status, None, None)
    assert reported is not None and reported.value_ft is not None
    assert survey.value_ft is not None
    error_ft = reported.value_ft - survey.value_ft
    within = None if reported.plus_minus_ft is None else abs(error_ft) <= reported.plus_minus_ft
    return MeasurementScore(survey, reported, status, error_ft, within)


def _measurement_status(
    survey: SurveyMeasurement, reported: PipelineMeasurement | None, *, scale_reference: bool
) -> MeasurementStatus:
    if scale_reference:
        return "scale_reference"
    if survey.status == "not_measured":
        return "not_surveyed"
    if reported is None:
        raise ValueError(f"run has no entry for survey measurement {survey.id!r}")
    if reported.missing == "unsupported":
        return "missing_unsupported"
    if reported.missing == "failed":
        return "missing_failed"
    run_says_absent = reported.missing == "absent"
    if survey.status == "absent":
        return "absent_agreed" if run_says_absent else "phantom"
    return "false_absent" if run_says_absent else "scored"


def truth_outcome(
    survey: SurveyMeasurement, threshold: Threshold, review: Threshold | None = None
) -> TruthOutcome:
    """The outcome the tape survey supports under the rules.

    Margins are signed so that positive is the passing side, and u is the survey uncertainty.
    The check passes when the margin to the pass line is larger than u and fails when the margin
    to the fail line is below -u. The fail line is `threshold`. The pass line is `review` when
    the check has a review band and `threshold` otherwise. Anything else is borderline for a
    single threshold, or review for a band, as the plan's Lane C sends a route past the confident
    reach to UNSURE (https://github.com/SamGu-NRX/house-scanning-master/blob/9737e3f0eefe90f2a12a190bf8750e7fed64413f/docs/02-implementation-plan.md#L114).

    Both comparisons are strict, following Lane C: "A check answers PASS if the margin is larger
    than the error." A value exactly on a line, or exactly u from it, is borderline or review,
    never pass or fail, including when u = 0. The solver uses the same rule, so a correct
    pipeline agrees with this outcome. A missing feature clears every at_least rule.
    """
    if survey.status == "not_measured":
        return "unknown"
    if survey.status == "absent":
        if threshold.pass_when != "at_least":
            raise ValueError(f"absent measurement {survey.id!r} cannot decide an at_most rule")
        return "pass"
    assert survey.value_ft is not None and survey.plus_minus_ft is not None
    return decide(survey.value_ft, survey.plus_minus_ft, threshold, review)


def decide(
    value_ft: Decimal, plus_minus_ft: Decimal, threshold: Threshold, review: Threshold | None
) -> Literal["pass", "fail", "borderline", "review"]:
    """The strict Lane C rule for one value and its uncertainty; truth_outcome explains it.

    The survey outcome and the Measure Lab importer's --decide row both call this, so a run
    that emulates the rule and the survey it is scored against can never disagree about ties.
    """
    pass_margin = margin_ft(value_ft, review or threshold)
    if pass_margin > plus_minus_ft:
        return "pass"
    if margin_ft(value_ft, threshold) < -plus_minus_ft:
        return "fail"
    return "review" if review else "borderline"


def margin_ft(value_ft: Decimal, threshold: Threshold) -> Decimal:
    """Value minus threshold, signed so that positive is the passing side."""
    if threshold.pass_when == "at_least":
        return value_ft - threshold.value_ft
    return threshold.value_ft - value_ft


def passing_margin_ft(survey: SurveyMeasurement, threshold: Threshold) -> Decimal | None:
    """The survey value's margin_ft, or None when the survey has no value."""
    return None if survey.value_ft is None else margin_ft(survey.value_ft, threshold)


def expected_outcome(truth: TruthOutcome) -> Outcome | None:
    """What a correct run reports: unsure for a borderline or review survey outcome."""
    return {
        "pass": "pass",
        "fail": "fail",
        "borderline": "unsure",
        "review": "unsure",
        "unknown": None,
    }[truth]


def error_to_margin(
    abs_error_ft: Decimal, margins_ft: tuple[Decimal, ...], survey_plus_minus_ft: Decimal
) -> Decimal | AtThreshold:
    """error / max(m, survey uncertainty). At 1 or above, the run's error could flip the check.

    m is the survey value's distance to the nearest threshold in `margins_ft`: the one threshold,
    or either edge of a review band, since crossing either edge changes the outcome.
    """
    scale = max(min(abs(margin) for margin in margins_ft), survey_plus_minus_ft)
    if scale == 0:
        return AT_THRESHOLD
    return abs_error_ft / scale


def could_flip(ratio: Decimal | AtThreshold, abs_error_ft: Decimal) -> bool:
    """An error equal to the margin can put the run exactly on a line, where the strict rule
    neither passes nor fails it, so a ratio of exactly 1 can flip the check."""
    if isinstance(ratio, str):
        return abs_error_ft > 0
    return ratio >= 1


@dataclass(frozen=True)
class CheckScore:
    """One check judged: what the survey supports, what the run reported, and how they differ.

    `truth` is truth_outcome's verdict for the survey value and `expected` the answer a correct
    run would give: `agrees` compares the run with `expected`, and the wrong-answer flags below
    are METRICS.md's "Decisions" categories. error_to_margin and could_flip carry that page's
    error-ratio warning sign, filled only when the measurement was scored.
    """

    check: Check
    threshold: Threshold
    review: Threshold | None
    survey: SurveyMeasurement
    measurement: MeasurementScore
    truth: TruthOutcome
    # Signed survey margins to the fail threshold and to the review threshold; positive passes.
    margin_ft: Decimal | None
    review_margin_ft: Decimal | None
    error_to_margin: Decimal | AtThreshold | None
    could_flip: bool | None
    reported: Outcome | None
    expected: Outcome | None
    # The following are None when the run makes no decisions or the survey outcome is unknown.
    agrees: bool | None
    # The three wrong-answer categories mean the same for every check, banded or not.
    # A pass where the survey fails.
    unsafe_pass: bool | None
    # A pass where the survey is unsure (borderline) or in a review band.
    missed_review: bool | None
    # An unsure or fail where the survey passes.
    over_caution: bool | None
    false_rejection: bool | None
    # A pass or fail without a deciding distance. Counted whatever the survey outcome.
    decided_without_measurement: bool | None
    abstention: Abstention | None


def _decided_without_measurement(
    reported: Outcome, measurement: MeasurementScore, threshold: Threshold
) -> bool:
    """Absence supports only a pass on an at_least clearance."""
    missing = measurement.reported.missing if measurement.reported else None
    return reported in ("pass", "fail") and (
        missing in ("failed", "unsupported")
        or (missing == "absent" and (threshold.pass_when == "at_most" or reported == "fail"))
    )


def score_check(
    check: Check,
    threshold: Threshold,
    measurement: MeasurementScore,
    reported: Outcome | None,
    review: Threshold | None = None,
) -> CheckScore:
    """Judge one check: survey truth, run answer, agreement, and the wrong-answer categories.

    `threshold` is the fail line and `review` the second line when the check has a review band.
    `reported` is the run's outcome for this check, or None for a run that makes no decisions.
    The wrong-answer categories need a known survey outcome, while decided_without_measurement
    is set for any reported pass or fail. An unsure is a justified abstention when the survey is
    itself unsure or the deciding measurement is missing as failed or unsupported, and avoidable
    otherwise, including when the run claimed the feature absent (METRICS.md, "Decisions").
    """
    survey = measurement.survey
    truth = truth_outcome(survey, threshold, review)
    margin = passing_margin_ft(survey, threshold)
    review_margin = None if review is None else passing_margin_ft(survey, review)

    ratio: Decimal | AtThreshold | None = None
    flip: bool | None = None
    if measurement.error_ft is not None:
        assert margin is not None and survey.plus_minus_ft is not None
        abs_error_ft = abs(measurement.error_ft)
        margins = (margin,) if review_margin is None else (margin, review_margin)
        ratio = error_to_margin(abs_error_ft, margins, survey.plus_minus_ft)
        flip = could_flip(ratio, abs_error_ft)

    expected = expected_outcome(truth)
    agrees = unsafe = missed = over_caution = rejection = without_measurement = None
    abstention: Abstention | None = None
    if reported is not None:
        without_measurement = _decided_without_measurement(reported, measurement, threshold)
    if reported is not None and truth != "unknown":
        agrees = reported == expected
        unsafe = reported == "pass" and truth == "fail"
        missed = reported == "pass" and truth in ("borderline", "review")
        over_caution = reported in ("unsure", "fail") and truth == "pass"
        rejection = reported == "fail" and truth == "pass"
        if reported == "unsure":
            evidence_missing = measurement.status in ("missing_unsupported", "missing_failed")
            justified = truth in ("borderline", "review") or evidence_missing
            abstention = "justified" if justified else "avoidable"

    return CheckScore(
        check=check,
        threshold=threshold,
        review=review,
        survey=survey,
        measurement=measurement,
        truth=truth,
        margin_ft=margin,
        review_margin_ft=review_margin,
        error_to_margin=ratio,
        could_flip=flip,
        reported=reported,
        expected=expected,
        agrees=agrees,
        unsafe_pass=unsafe,
        missed_review=missed,
        over_caution=over_caution,
        false_rejection=rejection,
        decided_without_measurement=without_measurement,
        abstention=abstention,
    )


@dataclass(frozen=True)
class RunScore:
    """All scores for one run: paired measurements, judged checks, and the rollups below.

    The measurement properties describe the distances and the decision properties sum CheckScore
    flags over the checks. report.py turns both into the runs.csv row and the summary tables.
    """

    truth: Truth
    results: Results
    measurements: tuple[MeasurementScore, ...]
    checks: tuple[CheckScore, ...]

    def count(self, status: MeasurementStatus) -> int:
        """Measurements in one status bucket. The scale reference counts, unlike denominator."""
        return sum(score.status == status for score in self.measurements)

    @property
    def denominator(self) -> int:
        """Every survey measurement except the scale reference, whether or not the run answered."""
        return sum(score.status != "scale_reference" for score in self.measurements)

    @property
    def scored_errors_in(self) -> list[Decimal]:
        """Absolute errors in inches for every scored pair, feeding the median and maximum."""
        return [s.abs_error_in for s in self.measurements if s.abs_error_in is not None]

    @property
    def median_abs_error_in(self) -> Decimal | None:
        """Median absolute error in inches over the distances both sides measured. None when
        nothing was scored."""
        errors = self.scored_errors_in
        return median(errors) if errors else None

    @property
    def max_abs_error_in(self) -> Decimal | None:
        """Largest absolute error in inches over the scored pairs. None when nothing was scored."""
        errors = self.scored_errors_in
        return max(errors) if errors else None

    @property
    def within_reported(self) -> tuple[int, int]:
        """(scored distances inside the run's ±, scored distances where the run gave a ±)."""
        flags = [s.truth_within_reported for s in self.measurements]
        return sum(flag is True for flag in flags), sum(flag is not None for flag in flags)

    @property
    def makes_decisions(self) -> bool:
        """Whether the run reported any check outcomes. False marks a distances-only run, and
        every decision count below is 0."""
        return self.results.outcomes is not None

    @property
    def judged(self) -> int:
        """Checks the survey can settle: its outcome is not unknown."""
        return sum(score.truth != "unknown" for score in self.checks)

    @property
    def agreements(self) -> int:
        """Checks whose answer matches the expected outcome. Unknown-outcome checks can neither
        agree nor disagree, so the summary shows this over judged."""
        return sum(score.agrees is True for score in self.checks)

    @property
    def unsafe_passes(self) -> int:
        """Passes on checks the survey fails: per the scoring protocol, the number that matters
        most (METRICS.md, "Decisions")."""
        return sum(score.unsafe_pass is True for score in self.checks)

    @property
    def missed_reviews(self) -> int:
        """Passes on checks the survey leaves borderline or in a review band. Kept out of
        unsafe_passes so unsafe always means the survey shows the spot breaks a rule."""
        return sum(score.missed_review is True for score in self.checks)

    @property
    def over_cautious(self) -> int:
        """Unsure or fail answers on checks the survey passes. The fails among them also count
        as false_rejections."""
        return sum(score.over_caution is True for score in self.checks)

    @property
    def decided_without_measurement(self) -> int:
        """Pass or fail answers made without a deciding distance, even when the survey outcome
        is unknown. Either the deciding value is missing as failed or unsupported, or the run
        claimed the feature absent, which cannot support an at_most decision or an at_least
        fail (METRICS.md, "Decisions")."""
        return sum(score.decided_without_measurement is True for score in self.checks)

    @property
    def false_rejections(self) -> int:
        """Fails where the survey passes: the fail subset of over_caution."""
        return sum(score.false_rejection is True for score in self.checks)

    @property
    def could_flip(self) -> int:
        """Checks whose error ratio reaches 1 against max(margin, uncertainty), the warning sign
        from METRICS.md "Error relative to the threshold". A survey value exactly on a line with
        no uncertainty has no ratio, and any nonzero error could flip it."""
        return sum(score.could_flip is True for score in self.checks)

    def abstentions(self, kind: Abstention) -> int:
        """Unsure answers of the given kind. An unsure on a check with an unknown survey outcome
        counts in neither kind. See score_check for what separates justified from avoidable."""
        return sum(score.abstention == kind for score in self.checks)


def score_run(truth: Truth, results: Results, thresholds: dict[str, Threshold]) -> RunScore:
    """Score a whole run against one house's survey.

    Pairs every survey measurement with the run's entry, then judges every check against its
    thresholds and the run's outcome. The truth file's scale reference is marked and never
    scored. `thresholds` is the rules file's table, and each check's threshold and
    review_threshold must be keys in it.
    """
    measurements = tuple(
        score_measurement(
            survey,
            results.measurements.get(survey.id),
            scale_reference=survey.id == truth.scale_reference,
        )
        for survey in truth.measurements.values()
    )
    by_id = {score.survey.id: score for score in measurements}
    checks = tuple(
        score_check(
            check,
            thresholds[check.threshold],
            by_id[check.measurement],
            None if results.outcomes is None else results.outcomes[(check.candidate, check.check)],
            None if check.review_threshold is None else thresholds[check.review_threshold],
        )
        for check in truth.checks
    )
    return RunScore(truth, results, measurements, checks)
