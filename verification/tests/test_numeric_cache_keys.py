import sys

import pytest

from hsverify.intervalindex import SweepIndex, numeric_key, probe_starts
from hsverify.resultcheck import WorkMeter, observed


class ComparedInt(int):
    """Count equality work while retaining the hash of the JSON integer it represents."""

    comparisons = 0
    __hash__ = int.__hash__

    def __eq__(self, other):
        type(self).comparisons += 1
        if type(self).comparisons > 100_000:
            raise AssertionError("numeric keys exceeded 100,000 equality comparisons")
        return super().__eq__(other)


def values(colliding, count=2_000):
    step = sys.hash_info.modulus if colliding else 1
    return [ComparedInt(step * i) for i in range(1, count + 1)]


def equality_work(operation, colliding):
    numbers = values(colliding)
    ComparedInt.comparisons = 0
    operation(numbers)
    return ComparedInt.comparisons


def test_numeric_keys_preserve_exact_numeric_equivalence():
    assert numeric_key(1) == numeric_key(1.0)
    assert numeric_key(0) == numeric_key(-0.0)
    assert numeric_key(2**60) == numeric_key(float(2**60))
    assert numeric_key(2**53 + 1) != numeric_key(float(2**53 + 1))
    assert numeric_key(0.1) != numeric_key(1)


@pytest.mark.parametrize("operation", ["observed", "input", "index", "probes"])
def test_modulus_multiple_response_numbers_do_not_multiply_equality_work(operation):
    def exercise(numbers):
        meter = WorkMeter()
        if operation == "observed":
            scene = {"coverage": {"observed": []}}
            for number in numbers:
                assert observed(scene, "ground", number, meter=meter) == []
            assert len(meter.cache) == len(numbers)
        elif operation == "input":
            for number in numbers:
                meter.input_key({"out_ft": number})
            assert len(meter.input_values) == len(numbers)
        else:
            result = {
                "sweep": [
                    {"wall_id": "w1", "start_ft": [number, number], "outcome": "fail"}
                    for number in numbers
                ]
            }
            if operation == "index":
                index = SweepIndex(result)
                assert len(index.walls["w1"].starts) == len(numbers)
            else:
                assert len(probe_starts(result)) >= len(numbers)

    ordinary = equality_work(exercise, False)
    colliding = equality_work(exercise, True)
    # Count comparisons rather than wall time: sorting/deduplication may do a small
    # multiple of the ordinary workload, but a numeric hash table does millions.
    assert colliding <= 4 * ordinary + 20 * 2_000
