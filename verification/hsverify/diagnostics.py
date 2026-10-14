"""Bound diagnostic output independently of geometry work and input size."""

import json

# Operational output limits, not measured scan maxima: 1,000 messages and 256 KiB
# leave a readable report without multiplying input-sized strings across cases.
# Reserve one message and 128 characters for the omitted-message count.
MAX_DIAGNOSTIC_COUNT = 1_000
MAX_DIAGNOSTIC_CHARACTERS = 256 * 1024
SUMMARY_RESERVE = 128
MAX_EXPECTATION_EXAMPLES = 3


class DiagnosticBudget:
    def __init__(self) -> None:
        self.count = 0
        self.characters = 0
        self.omitted = 0

    def accept(self, message: str) -> bool:
        prefix = "further problems omitted ("
        if message.startswith(prefix) and message.endswith(")"):
            count = message[len(prefix) : -1]
            if len(count) <= 20 and count.isdecimal():
                self.omitted += int(count)
                return False
        if (
            self.omitted
            or self.count >= MAX_DIAGNOSTIC_COUNT - 1
            or self.characters + len(message) > MAX_DIAGNOSTIC_CHARACTERS - SUMMARY_RESERVE
        ):
            self.omitted += 1
            return False
        # JSON escapes control and non-ASCII characters; budget the saved text,
        # not only the shorter Python string. The raw-length check above bounds
        # this temporary encoding too.
        characters = len(json.dumps(message))
        if self.characters + characters > MAX_DIAGNOSTIC_CHARACTERS - SUMMARY_RESERVE:
            self.omitted += 1
            return False
        self.count += 1
        self.characters += characters
        return True

    def summary(self) -> list[str]:
        return [f"further problems omitted ({self.omitted})"] if self.omitted else []


class DiagnosticMessages:
    def __init__(self) -> None:
        self.budget = DiagnosticBudget()
        self.messages: list[str] = []

    def append(self, message: str, *values: object) -> None:
        if self.budget.omitted:
            self.budget.omitted += 1
            return
        if values:
            message = message.format(*values)
        if self.budget.accept(message):
            self.messages.append(message)

    def finish(self) -> list[str]:
        return self.messages + self.budget.summary()


def bounded_report_records(records: list[dict]) -> tuple[list[dict], list[str]]:
    """Use one budget across cases and duplicate diagnostic fields in the JSON report."""
    budget = DiagnosticBudget()
    bounded = []
    for record in records:
        copy = dict(record)
        # Markdown renders problems; retain its explanations before their JSON duplicates.
        for field in (
            "problems",
            "contract_problems",
            "expectation_problems",
            "assumption_mismatches",
            "assumption_warnings",
        ):
            if field in copy:
                copy[field] = [message for message in copy[field] if budget.accept(message)]
        if copy.get("skip_reason") and not budget.accept(copy["skip_reason"]):
            copy["skip_reason"] = ""
        bounded.append(copy)
    return bounded, budget.summary()
