"""Generic change detection on lake streams.

Pure statistics, with no knowledge of optics or of any fault: a stream changed at the onset if its level
after differs from its level before by much more than its own scatter before, or if it made an excursion
far outside that scatter, or if it went silent or lost (no value, or the lake's loss marker). The noise
scale is measured from the stream itself, so no observable needs a hand-set threshold.
"""
from __future__ import annotations

import math
import statistics as st
from dataclasses import dataclass
from typing import Optional

LOST = -999.0


@dataclass
class Change:
    observable: str
    element: str
    kind: str                 # level | excursion | lost | restored
    t: float                  # first sample after onset that shows it
    before: Optional[float]
    after: Optional[float]
    delta: Optional[float]
    z: Optional[float]
    evidence: list


def _is_lost(v) -> bool:
    return v is None or (isinstance(v, float) and (math.isinf(v) or v <= LOST + 1))


LOG_UNITS = ("ratio", "rad/s")     # quantities that span decades are compared on a log scale


def _scale(xs) -> float:
    """Robust scatter: 1.4826 times the median absolute deviation, but not below half the standard
    deviation (short records make the MAD unreliable), with a small floor."""
    m = st.median(xs)
    mad = st.median([abs(x - m) for x in xs])
    return max(1.4826 * mad, 0.5 * st.pstdev(xs), 1e-3 * max(1.0, abs(m)), 1e-6)


def _first_persistent(seq, test, k: int = 3):
    """First item of the first run of k consecutive items passing `test` (or of the whole run if shorter)."""
    run = []
    for item in seq:
        if test(item):
            run.append(item)
            if len(run) >= k:
                return run[0]
        else:
            run = []
    return run[0] if run and len(run) == len(seq[-len(run):]) and run[-1] is seq[-1] else None


def detect(samples, observable: str, element: str, onset: float, guard: float = 5.0,
           z_level: float = 6.0, z_exc: float = 10.0, log_scale: bool = False) -> Optional[Change]:
    """samples: list of (t, value, evidence_id). Returns the change at `onset`, if any."""
    before = [(t, v, e) for t, v, e in samples if t < onset - guard]
    after = [(t, v, e) for t, v, e in samples if t > onset + guard]
    if not before or not after:
        return None
    b_lost = [x for x in before if _is_lost(x[1])]
    a_lost = [x for x in after if _is_lost(x[1])]
    if len(a_lost) > 0.5 * len(after) and len(b_lost) < 0.5 * len(before):
        return Change(observable, element, "lost", a_lost[0][0], None, None, None, None,
                      sorted({a_lost[0][2], before[-1][2]}))
    if len(b_lost) > 0.5 * len(before) and len(a_lost) < 0.5 * len(after):
        return Change(observable, element, "restored", after[0][0], None, None, None, None, [after[0][2]])
    bv = [v for _, v, _ in before if not _is_lost(v)]
    av = [(t, v, e) for t, v, e in after if not _is_lost(v)]
    if len(bv) < 5 or not av:
        return None
    f = (lambda x: math.log10(max(x, 1e-30))) if log_scale else (lambda x: x)
    b = [f(v) for v in bv]
    a = [(t, f(v), e) for t, v, e in av]
    mb = st.median(b)
    sd = _scale(b)
    ma = st.median([v for _, v, _ in a])
    se = sd * math.sqrt(1 / len(b) + 1 / len(a))
    z = (ma - mb) / se
    if abs(z) > z_level and abs(ma - mb) > 4 * sd:
        sign = 1 if ma > mb else -1
        first = _first_persistent(a, lambda x: sign * (x[1] - mb) > 3 * sd) or a[0]
        return Change(observable, element, "level", first[0], mb, ma, ma - mb, z, [first[2], before[-1][2]])
    # on a log scale, values near zero make downward departures meaningless: only upward excursions count
    peak = max(a, key=(lambda x: x[1] - mb) if log_scale else (lambda x: abs(x[1] - mb)))
    if len(b) >= 10 and abs(peak[1] - mb) > z_exc * sd and (not log_scale or peak[1] > mb):
        return Change(observable, element, "excursion", peak[0], mb, peak[1], peak[1] - mb,
                      (peak[1] - mb) / sd, [peak[2], before[-1][2]])
    return None


def detect_onset(sub, streams: list[tuple[str, str, bool]], around: float, span: float = 300.0) -> Optional[float]:
    """Earliest time in [around-span, around+span] at which any listed stream (observable, element,
    log scale) leaves its earlier scatter persistently (three samples in a row) or is lost."""
    best = None
    for ob, el, logs in streams:
        f = (lambda x: math.log10(max(x, 1e-30))) if logs else (lambda x: x)
        s = [(x.t, x.value) for x in sub.samples(ob, el, around - span - 300, around + span)]
        pre = [f(v) for t, v in s if t < around - span and not _is_lost(v)]
        if len(pre) < 8:
            continue
        m, sd = st.median(pre), _scale(pre)
        cand = [(t, v) for t, v in s if t >= around - span]
        hit = _first_persistent(cand, lambda x: _is_lost(x[1]) or abs(f(x[1]) - m) > 8 * sd)
        if hit:
            best = hit[0] if best is None else min(best, hit[0])
    return best
