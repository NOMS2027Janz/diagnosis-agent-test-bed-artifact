"""Scoring a reconciliation against a gold standard.

The metric families follow Part II: reliability (precision, recall, surviving
false cognates), mechanical work, residual and closure, and scaling. Cognitive
effort is left blank here; it is filled in only when a language-model stack runs,
from the effort signals the model exposes.
"""
from __future__ import annotations

from reconcile.model import Gold
from reconcile.stacks.base import Reconciliation


def _safe_div(n: int, d: int) -> float:
    return n / d if d else 0.0


# A proposal at or above this confidence is treated as "committed with conviction"; a wrong
# proposal at or above it is a *confident error*, the outcome Part II and the ontology-
# reconciliation draft warn costs more than an honest deferral.
CONFIDENT_TAU = 0.8

_CONF_KEYS = ("conf_correct_mean", "conf_wrong_mean", "calibration_gap", "brier",
              "confident_errors", "confident_error_rate")


def _confidence_metrics(proposed: set, correct: set, rec: Reconciliation) -> dict:
    """Confidence-sensitive reliability, computed only when a stack attaches per-pair
    confidence (LM stacks). Blank otherwise, so model-agnostic controls are unaffected.

    - conf_correct_mean / conf_wrong_mean: mean confidence on right vs wrong proposals.
    - calibration_gap: conf_correct_mean - conf_wrong_mean. Near zero is the pathology Brad
      names -- "no less confident on a wrong merge than a right one".
    - brier: mean (confidence - correct?)^2 over scored proposals; lower is better-calibrated.
    - confident_errors / rate: wrong proposals asserted at >= CONFIDENT_TAU, reported apart
      from honest deferrals (which are the false_negatives).
    """
    conf = getattr(rec, "confidence", None) or {}
    scored = [(p, float(conf[p])) for p in proposed if p in conf]
    if not scored:
        return {k: "" for k in _CONF_KEYS}
    right = [c for p, c in scored if p in correct]
    wrong = [c for p, c in scored if p not in correct]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None
    mc, mw = mean(right), mean(wrong)
    brier = sum((c - (1.0 if p in correct else 0.0)) ** 2 for p, c in scored) / len(scored)
    confident_errors = sum(1 for p, c in scored if p not in correct and c >= CONFIDENT_TAU)
    return {
        "conf_correct_mean": round(mc, 3) if mc is not None else "",
        "conf_wrong_mean": round(mw, 3) if mw is not None else "",
        "calibration_gap": round(mc - mw, 3) if (mc is not None and mw is not None) else "",
        "brier": round(brier, 3),
        "confident_errors": confident_errors,
        "confident_error_rate": round(confident_errors / len(scored), 3),
    }


def score(rec: Reconciliation, gold: Gold) -> dict:
    proposed = set(rec.proposed)
    correct = gold.correct_pairs
    false_cog = gold.false_cognate_pairs

    tp = len(proposed & correct)
    fp = len(proposed - correct)
    fn = len(correct - proposed)
    surviving_fc = len(proposed & false_cog)

    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall) if (precision + recall) else 0.0

    scaling = "~N" if rec.uses_reference else "~N^2"
    effort = rec.effort or {}

    return {
        "stack": rec.stack,
        "uses_reference": rec.uses_reference,
        "placement": rec.placement,
        "proposed": len(proposed),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "surviving_false_cognates": surviving_fc,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "residual": len(rec.residual_a) + len(rec.residual_b),
        "candidates": rec.work.get("candidates", 0),
        "bilateral_checks": rec.work.get("bilateral_checks", 0),
        "binding_ops": rec.work.get("binding_ops", 0),
        "scaling": scaling,
        # cognitive effort: blank for model-agnostic stacks, filled by an LM stack
        "total_tokens": effort.get("total_tokens", ""),
        "reasoning_tokens": effort.get("reasoning_tokens", "") if effort.get("reasoning_tokens") is not None else "",
        "latency_s": effort.get("latency_s", ""),
        "model": effort.get("model", ""),
        **_confidence_metrics(proposed, correct, rec),
    }


METRIC_COLUMNS = [
    "case", "stack", "uses_reference", "placement",
    "proposed", "true_positives", "false_positives", "false_negatives",
    "surviving_false_cognates", "precision", "recall", "f1",
    "conf_correct_mean", "conf_wrong_mean", "calibration_gap", "brier",
    "confident_errors", "confident_error_rate",
    "residual", "candidates", "bilateral_checks", "binding_ops", "scaling",
    "total_tokens", "reasoning_tokens", "latency_s", "model",
]
