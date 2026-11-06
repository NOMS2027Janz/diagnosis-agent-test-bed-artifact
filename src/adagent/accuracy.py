"""What a check's answer can resolve: one standard for the agent and the harness (amendment 4).

A check's answer is compared with a prediction of it. The agent compares the answer it got with the twin's prediction
under each contender (tools/interpret.py) and groups contenders whose predicted answers are alike (tools/advisor.py);
the harness asks whether a check would have told two causes apart (harness/separability.py). All three use the misfit
below, with the accuracies the operator documents (knowledge/practice/twin.yaml, check_accuracy):

    sigma = sqrt(instrument**2 + twin**2 + (rel * |predicted|)**2 + (change * |predicted - no-fault|)**2)

instrument: the instrument's accuracy, calibration included; twin: the twin's absolute accuracy on that quantity
(input and calculation error); rel: an error proportional to the value; change: the twin's error on a predicted
change, a fraction of the change from the no-fault answer. A numeric field costs 0.5 z**2 (at most 8 nats), a
categorical difference MISMATCH_NATS. An answer's misfit is that of its worst field: the fields of one answer share
their errors (margin, OSNR and BER follow one signal-to-noise ratio), so adding them would count one error many times.
A series of readings of one quantity (the one-minute bins of a register) is compared as its errors arise: the offset
common to every reading (the instrument's calibration and the twin's absolute error, the same in every bin) once,
against those two accuracies; and each reading's departure from that common offset against what varies from reading
to reading (the instrument's reading noise, the error proportional to the value and the twin's error on a predicted
change). The worst of n departures is judged as one reading as rare would be (its probability under n readings mapped to
one), so that a series is not judged more harshly for being long; a single reading is judged exactly as one field. Two answers are alike when their misfit is below SAME_NATS (2 nats: every field within two standard
deviations).
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

MISMATCH_NATS = 5.0
SAME_NATS = 2.0
Z2_CAP = 16.0
SKIP = {"t", "position_km", "length_km", "span", "trx", "oms", "unit", "switch", "path", "lightpath", "receiver",
        "port", "reason", "recorded_loss_db"}
FALLBACK = {"scale": "lin", "instrument": 0.2, "twin": 0.5, "rel": 0.0, "change": 0.2}


@lru_cache(maxsize=1)
def table() -> dict:
    p = Path(__file__).resolve().parents[2] / "knowledge" / "practice" / "twin.yaml"
    doc = yaml.safe_load(p.read_text()) or {}
    return doc.get("check_accuracy") or {}


def sigma(field: str, predicted: float, base: Optional[float] = None) -> Optional[tuple]:
    """(scale, sigma) for a numeric field, in the scale's units; None if the field is not compared."""
    if field in SKIP:
        return None
    d = {**FALLBACK, **(table().get(field) or {})}
    scale = d["scale"]
    f = (lambda x: math.log10(max(abs(x), 1e-30))) if scale == "log10" else (lambda x: x)
    p = f(predicted)
    ch = abs(p - f(base)) if base is not None and math.isfinite(base) else 0.0
    s = math.sqrt(d["instrument"] ** 2 + d["twin"] ** 2 + (d["rel"] * abs(predicted)) ** 2 + (d["change"] * ch) ** 2)
    return scale, max(s, 1e-6)


def misfit(actual, predicted, name: str = "", base=None) -> float:
    """Misfit in nats between an answer and a predicted answer; base: the no-fault answer, if known."""
    if name in SKIP:
        return 0.0
    if isinstance(actual, dict) and isinstance(predicted, dict):
        b = base if isinstance(base, dict) else {}
        return max((misfit(actual.get(k), predicted.get(k), k, b.get(k)) for k in actual.keys() | predicted.keys()),
                   default=0.0)
    if isinstance(actual, list) and isinstance(predicted, list):
        if (name == "bins" and actual and len(actual) == len(predicted)
                and all(isinstance(x, dict) for x in actual + predicted)):
            return _series(actual, predicted, base if isinstance(base, list) and len(base) == len(predicted) else None)
        if name == "events":                 # OTDR events: by type, then the size of matched loss events
            ta = sorted(e.get("type") for e in actual)
            tp = sorted(e.get("type") for e in predicted)
            if ta != tp:
                return MISMATCH_NATS
            la = [e.get("loss_db") for e in actual if e.get("type") == "loss"]
            lp = [e.get("loss_db") for e in predicted if e.get("type") == "loss"]
            return max((misfit(x, y, "loss_db") for x, y in zip(sorted(la), sorted(lp))), default=0.0)
        if len(actual) != len(predicted):
            return MISMATCH_NATS
        bl = base if isinstance(base, list) and len(base) == len(predicted) else [None] * len(predicted)
        return max((misfit(a, p, name, b) for a, p, b in zip(actual, predicted, bl)), default=0.0)
    if actual is None and predicted is None:
        return 0.0
    if actual is None or predicted is None:
        return MISMATCH_NATS
    if isinstance(actual, bool) or isinstance(predicted, bool) or isinstance(actual, str) or isinstance(predicted, str):
        return 0.0 if actual == predicted else MISMATCH_NATS
    if isinstance(actual, (int, float)) and isinstance(predicted, (int, float)):
        a, p = float(actual), float(predicted)
        if math.isinf(a) or math.isinf(p) or math.isnan(a) or math.isnan(p):
            return 0.0 if a == p else MISMATCH_NATS
        b = float(base) if isinstance(base, (int, float)) and not isinstance(base, bool) else None
        s = sigma(name, p, b)
        if s is None:
            return 0.0
        scale, sig = s
        if scale == "log10":
            a, p = math.log10(max(abs(a), 1e-30)), math.log10(max(abs(p), 1e-30))
        z = (a - p) / sig
        return 0.5 * min(z * z, Z2_CAP)
    return 0.0 if actual == predicted else MISMATCH_NATS


def alike(a, b, base=None) -> bool:
    """Whether answers a and b (b the one predicted) are too close for the check to tell apart."""
    return misfit(a, b, "", base) < SAME_NATS


def _as_single(zmax: float, n: int) -> float:
    """The deviate of one reading as rare as the largest of n readings' deviates (|z| at the same probability), so
    that the worst of n departures is judged as one reading would be."""
    from statistics import NormalDist
    nd = NormalDist()
    if n <= 1:
        return zmax
    q = (2.0 * nd.cdf(min(zmax, 8.0)) - 1.0) ** n          # P(the largest of n |deviates| is below zmax)
    q = min(q, 1.0 - 1e-15)
    return nd.inv_cdf((1.0 + q) / 2.0)


def _series(actual: list, predicted: list, base: Optional[list]) -> float:
    """Misfit of a series of readings (one-minute bins): see the module's description."""
    bl = base or [None] * len(predicted)
    keys = {k for x in actual + predicted for k in x} - SKIP
    worst = 0.0
    for k in keys:
        d = {**FALLBACK, **(table().get(k) or {})}
        trip = [(a.get(k), p.get(k), (b or {}).get(k) if isinstance(b, dict) else None)
                for a, p, b in zip(actual, predicted, bl)]
        num = all(isinstance(a, (int, float)) and isinstance(p, (int, float)) and not isinstance(a, bool)
                  and not isinstance(p, bool) and math.isfinite(a) and math.isfinite(p) for a, p, _ in trip)
        if "noise" not in d or not num:
            worst = max(worst, max((misfit(a, p, k, b) for a, p, b in trip), default=0.0))
            continue
        f = (lambda x: math.log10(max(abs(x), 1e-30))) if d["scale"] == "log10" else (lambda x: x)
        diffs = [f(a) - f(p) for a, p, _ in trip]
        off = sorted(diffs)[len(diffs) // 2]                       # the common offset (median: robust to an event bin)
        shared = math.sqrt(max(d["instrument"] ** 2 - d["noise"] ** 2, 0.0) + d["twin"] ** 2)
        worst = max(worst, 0.5 * min((off / max(shared, 1e-6)) ** 2, Z2_CAP))
        zmax2 = 0.0
        for (a, p, b), x in zip(trip, diffs):
            ch = abs(f(p) - f(b)) if isinstance(b, (int, float)) and not isinstance(b, bool) and math.isfinite(b) else 0.0
            sr = math.sqrt(d["noise"] ** 2 + (d["rel"] * abs(p)) ** 2 + (d["change"] * ch) ** 2)
            zmax2 = max(zmax2, ((x - off) / max(sr, 1e-6)) ** 2)
        worst = max(worst, 0.5 * min(_as_single(math.sqrt(zmax2), len(trip)) ** 2, Z2_CAP))
    return worst

