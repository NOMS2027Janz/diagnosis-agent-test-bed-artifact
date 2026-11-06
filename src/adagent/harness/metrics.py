"""Tie-aware ranking metrics.

Generators often give several candidates exactly the same score (for example every span of a line
when only receiver telemetry is available). Ranking such ties by list order makes coverage@k depend
on an arbitrary tie-break. These functions instead give the expected value under a uniformly random
ordering of tied candidates, which is what an unbiased consumer of the list would experience.
"""
from __future__ import annotations

from typing import Optional

REL_TOL = 1e-9


def tie_stats(scores: dict[str, float], target: str) -> Optional[tuple[int, int]]:
    """(number strictly better, size of the tie group including the target), or None if absent."""
    if target not in scores:
        return None
    s = scores[target]
    tol = REL_TOL * max(1.0, abs(s))
    better = sum(1 for v in scores.values() if v > s + tol)
    ties = sum(1 for v in scores.values() if abs(v - s) <= tol)
    return better, ties


def expected_hit(scores: dict[str, float], target: str, k: int) -> float:
    """Probability that the target is in the top k under random tie-breaking (0 if absent)."""
    st = tie_stats(scores, target)
    if st is None:
        return 0.0
    better, ties = st
    return max(0.0, min(1.0, (k - better) / ties))


def expected_rank(scores: dict[str, float], target: str) -> Optional[float]:
    st = tie_stats(scores, target)
    if st is None:
        return None
    better, ties = st
    return better + (ties + 1) / 2


def expected_hit_any(scores: dict[str, float], targets: set[str], k: int) -> float:
    """Class-level coverage: probability that at least one member of a class is in the top k.
    Computed exactly for the common case of one tie group straddling the cut-off."""
    present = [t for t in targets if t in scores]
    if not present:
        return 0.0
    best = max(scores[t] for t in present)
    better, ties = tie_stats(scores, max(present, key=lambda t: scores[t]))
    slots = k - better
    if slots <= 0:
        return 0.0
    members_in_group = sum(1 for t in present if abs(scores[t] - best) <= REL_TOL * max(1.0, abs(best)))
    if slots >= ties:
        return 1.0
    # probability that none of the class members in the tie group lands in the free slots
    p_none = 1.0
    for i in range(slots):
        p_none *= (ties - members_in_group - i) / (ties - i)
    return 1.0 - max(0.0, p_none)
