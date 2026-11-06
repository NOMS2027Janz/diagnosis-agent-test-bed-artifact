"""Scoring a case frame against the harness's truth (harness side only)."""
from __future__ import annotations


def truth_location(fault) -> str:
    t = fault.targets[0]
    return t.split("|")[0]


def score_frame(frame, truth) -> dict:
    loc = truth_location(truth.fault)
    down_f = {s.service for s in frame.services if s.state == "down"}
    hit_f = {s.service for s in frame.services if s.state == "hit"}
    deg_f = {s.service for s in frame.services if s.state in ("errored", "degraded")}
    down_t, hit_t, deg_t = set(truth.services_down), set(truth.services_hit), set(truth.services_degraded)

    def pr(f, t):
        p = len(f & t) / len(f) if f else (1.0 if not t else 0.0)
        r = len(f & t) / len(t) if t else 1.0
        return round(p, 3), round(r, 3)
    return {
        "onset_error_s": round(abs(frame.onset_estimate - truth.fault.onset_s), 1),
        "location_in_scope": loc in frame.scope,
        "location_affected": loc in frame.affected_elements,
        "location_in_shared": loc in frame.shared_elements if frame.shared_elements else None,
        "down_precision_recall": pr(down_f, down_t),
        "hit_precision_recall": pr(hit_f, hit_t),
        "degraded_precision_recall": pr(deg_f, deg_t),
        "n_symptoms": len(frame.symptoms), "n_scope": len(frame.scope),
        "n_quiet": len(frame.quiet_instruments),
    }
