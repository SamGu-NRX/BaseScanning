import math
from collections.abc import Sequence


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def share(hits: int, n: int) -> str:
    """ "hits/n = rate (95% Wilson interval)", or a dash when there is nothing to count."""
    if n == 0:
        return "–"
    low, high = wilson(hits, n)
    return f"{hits}/{n} = {hits / n:.0%} ({low:.0%}–{high:.0%})"


def auc(scores: Sequence[float], labels: Sequence[bool]) -> float | None:
    """Probability that a random positive scores above a random negative (ties count half)."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y]
    neg = [s for s, y in zip(scores, labels, strict=True) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))
