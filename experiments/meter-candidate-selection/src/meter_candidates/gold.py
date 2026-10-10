"""Gold labels, kept outside the reader input.

gold/labels.json is the only file that says which synthetic serial a case carries (or that
it carries none). The reader arms and the candidate and ranking code never read it: they
see only the frozen images and observation sets. Scoring joins a case's reader output to
its label by case id after ranking.

meter_eval.match does the same join through a keyed HMAC digest, because CONTRIBUTING.md
forbids committing real meter numbers and an unkeyed hash of a short number can be
reversed. This experiment has no real number — every serial is synthetic — so plaintext
labels are safe, no HMAC key exists or is read here, and meter_eval itself is neither
imported nor modified.
"""

import json

from meter_candidates.paths import GOLD_PATH


def load(path=GOLD_PATH) -> dict:
    """Case id -> {'serial': str | None, 'present': bool, 'origin': str}."""
    labels = json.loads(path.read_text())
    for case_id, gold in labels.items():
        if gold["present"] != bool(gold["serial"]):
            raise ValueError(f"{case_id}: present flag disagrees with its serial")
    return labels
