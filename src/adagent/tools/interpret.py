"""Instrument interpreters: what a check's answer says about each contender ("The Diagnosis Agent Test Bed" §9.1-9.2).

A check (knowledge/practice/checks.yaml) returns an answer: an OTDR trace, a power reading, a list of mismatched
cross-connects, "errors clear on the spare port". Its interpreter turns the answer into a likelihood term over the
contenders still in play. Two ways, used together:

1. Prediction by the twin. For each contender the twin has fitted (tools/twin.py), the twin predicts the answer
   the check would give were that contender the cause, and the actual answer is scored against the prediction
   by the standard of src/adagent/accuracy.py (the instrument's and the twin's documented accuracy; an answer
   costs what its worst field costs). Fields that the contender does not determine (an event's position along a span,
   times) are not scored.
2. The check's own meaning, for contenders the twin cannot simulate. Some answers speak directly about a place:
   replacing a unit cleared the symptoms or did not; a connector was found dirty or clean; a loopback failed; a
   switch's cross-connects differ from provisioning; errors clear on a spare receiver. Contenders located there
   are supported or contradicted by the answer as the catalogue describes it, whatever their fault mode.

A check that failed or was not available says nothing: no term. Every result is also reported as three-valued for
each contender (supported, contradicted or unresolved), with the check, its source and time.
"""
from __future__ import annotations

import math
from typing import Optional

from ..schemas import Hypothesis, LikelihoodPayload, LikelihoodValue

from ..accuracy import MISMATCH_NATS, SAME_NATS, SKIP, misfit  # noqa: F401  (one standard with the harness)

DIRECT_NATS = 4.0            # a direct answer about a place against a contender located there (or not)
RECEIVE_SIDE = {"RX_DEGRADATION", "CARD_FAIL"}   # faults a spare receiver port would clear (C4: margin erosion is
                                                 # documented as ageing along the path, so a spare port does not clear it)


def compare(actual, predicted, name: str = "", base=None) -> float:
    """Misfit in nats between an answer and a predicted answer (accuracy.misfit); base: the no-fault prediction."""
    return misfit(actual, predicted, name, base)


def _base(loc: str) -> str:
    return loc.split("|")[0]


LINE_LOSS = {"FIBRE_CUT", "CONNECTOR_PULLED", "AMP_PUMP_FAIL", "TX_LASER_FAIL", "CARD_FAIL", "WSS_PORT_FAIL"}
EQUIPMENT = {"XC_MISCONNECT", "OTN_SWITCH_FAIL"}


SWAP_REPAIRS = {"AMP_PUMP_FAIL", "AMP_GAIN_DROP", "AMP_NF_RISE", "AMP_GAIN_TILT", "PUMP_CURRENT_DRIFT", "TX_LASER_FAIL",
                "CARD_FAIL", "RX_DEGRADATION", "LASER_BIAS_DRIFT", "CLIENT_PORT_FAIL"}   # faults of a unit itself


def _hop_direction(kg, lp: str, a: str) -> str:
    return "fwd" if (kg.g.nodes[lp].get("nodes") or [None])[0] == a else "rev"


def _on_signal(kg, lp: str, d: str, loc: str, mode: str) -> bool:
    """Whether a fault at `loc` lies on a lightpath's signal in direction d: its transmitter, the ROADMs, OMSs,
    amplifiers and spans it passes, and its receiver (a transmitter fault at the receiving end does not count)."""
    if kg is None or not kg.has(lp):
        return False
    base = _base(loc)
    path = kg.signal_path(lp, d)
    els = set(path) | {lp} | {o for o, e in kg.out(lp, "carriedBy") if e.get("direction") == d}
    if mode == "TX_LASER_FAIL" and path and base == path[-1]:
        return False
    return base in els


def _failed_segment(kg, path: str, loc: str, mode: str) -> Optional[object]:
    """The segment (index) of an ODU path that a fault would fail, per the documentation (a segment fails when the
    signal into its far-end switch is lost or that switch, or its cross-connect for the path, fails); -1 if it
    would fail none; None if the documentation does not say (a soft fault may or may not fail a segment; a
    misconnection placed on a switch without a path may or may not be on this path)."""
    if kg is None or not kg.has(path):
        return None
    hops = kg.odu_hops(path)
    if mode in EQUIPMENT:
        sw = _base(loc)
        if mode == "XC_MISCONNECT":
            if "|" not in loc:
                return None
            if loc.split("|", 1)[1] != path:
                return -1
        hit = [i for i, h in enumerate(hops) if f"otn.{h['z']}" == sw]
        return hit[0] if hit else -1
    on = [i for i, h in enumerate(hops) if _on_signal(kg, h["lightpath"], _hop_direction(kg, h["lightpath"], h["a"]),
                                                       loc, mode)]
    if not on:
        return -1
    return on[0] if mode in LINE_LOSS else None


def direct(check: str, params: dict, value: dict, loc: str, mode: str, kg=None) -> Optional[float]:
    """Misfit (nats) from a check's own meaning, as the catalogue documents it, for a contender at `loc`; None if
    the check says nothing about it. A positive finding at one place counts against contenders elsewhere; a negative
    finding there is what a contender elsewhere expects (0 nats). Every contender a check speaks of is so given a
    value, which matters to the ledger: a contender a term declines takes the mean of the values the term gives, so a
    check that valued only the contender it contradicts would move no probability at all."""
    if check == "swap_unit":
        at = _base(loc) == params.get("unit") and mode in SWAP_REPAIRS
        cleared = value.get("symptoms_after_swap") == "cleared"
        if at:
            return 0.0 if cleared else DIRECT_NATS
        return DIRECT_NATS if cleared else 0.0
    if check == "inspect_clean":
        at = _base(loc) == params.get("oms") and mode.startswith("CONNECTOR_")
        found = value.get("found", "")
        bad = "dirty" in found or "unseated" in found
        if at:
            return 0.0 if bad else DIRECT_NATS
        return DIRECT_NATS if bad else 0.0
    if check == "loopback":
        at = _base(loc) == params.get("port") and mode == "CLIENT_PORT_FAIL"
        failed = value.get("result") == "fail"
        if at:
            return 0.0 if failed else DIRECT_NATS
        return DIRECT_NATS if failed else 0.0
    if check == "xc_audit":
        sw = params.get("switch")
        reach = value.get("reachable", True)
        mism = value.get("mismatches") or []
        at = _base(loc) == sw
        if mode == "XC_MISCONNECT" and at:
            if not reach:
                return None
            path = loc.split("|", 1)[1] if "|" in loc else None
            return 0.0 if (path in mism if path else mism) else DIRECT_NATS
        if mode == "OTN_SWITCH_FAIL" and at:
            return 0.0 if reach is False else DIRECT_NATS
        return DIRECT_NATS if (reach is False or mism) else 0.0
    if check == "retune_spare":
        rx = value.get("receiver")
        local = value.get("errors_on_spare") == "clear"
        if mode not in RECEIVE_SIDE:                 # clear: the receiver; persist: the line
            return DIRECT_NATS if local else 0.0
        at = _base(loc) == rx
        if at:
            return 0.0 if local else DIRECT_NATS
        return DIRECT_NATS if local else 0.0
    if check == "xcheck_reading":              # the capability ladder, rung L6 (tools/ladder.py)
        from .ladder import xcheck_direct
        return xcheck_direct(params, value, loc, mode, kg)
    if check == "tcm_segments":
        seg = _failed_segment(kg, params.get("path", ""), loc, mode)
        if seg is None:
            return None
        failed = [i for i, x in enumerate(value.get("segments") or []) if x.get("status") == "failed"]
        if seg == -1:
            return DIRECT_NATS if failed else 0.0
        return 0.0 if seg in failed else DIRECT_NATS
    return None


# A hypothesis of several causes read by a check's documented meaning (peeling arm): the check is read for the one
# cause it concerns, as for that cause alone; for none of them, as for a cause elsewhere; for more than one, not at
# all. Off in the main arms, where a compound is read only through the twin.
COMPOUND_READING = False


def _check_scope(kg, check: str, params: dict) -> Optional[set]:
    if kg is None:
        return None
    out = set()
    lps = []
    if "lightpath" in params:
        lps.append(params["lightpath"])
    if "trx" in params:
        out.add(params["trx"])
        lps += [lp for lp, _ in kg.inn(params["trx"], "endsAt")]
    for lp in lps:
        if not kg.has(lp):
            continue
        out.add(lp)
        for d in ("fwd", "rev"):
            out |= set(kg.signal_path(lp, d))
        out |= set(kg.lightpath_ends(lp).values())
    for k in ("span", "oms", "unit", "switch", "port"):
        if k in params:
            out.add(params[k])
    if "path" in params and kg.has(params["path"]):
        out.add(params["path"])
        for hp in kg.odu_hops(params["path"]):
            out |= {f"otn.{hp['a']}", f"otn.{hp['z']}"}
    return out or None


def compound_cause(kg, check: str, params: dict, h) -> object:
    """The one cause of a compound that the check concerns, "elsewhere" if none, or None if several."""
    scope = _check_scope(kg, check, params)
    if scope is None:
        return None
    rel = []
    for c in h.causes:
        loc = c.location.element if c.location.kind == "element" else getattr(c.location, "span", None)
        if loc is None:
            return None
        if loc in scope or _base(loc) in scope or loc.split("|")[0] in scope:
            rel.append(c)
    if not rel:
        return "elsewhere"
    return rel[0] if len(rel) == 1 else None


def answer_set(kg, check: str, params: dict, paths_at_switch: tuple = ()) -> list:
    """The answers a check can give, as its catalogue entry describes them (for planning by documented meaning)."""
    if check == "swap_unit":
        return [{"symptoms_after_swap": v} for v in ("cleared", "unchanged")]
    if check == "inspect_clean":
        return [{"found": v} for v in ("dirty connector, cleaned", "connector unseated, reseated", "connectors clean")]
    if check == "loopback":
        return [{"result": v} for v in ("fail", "pass")]
    if check == "retune_spare":
        rx = kg.lightpath_ends(params["lightpath"]).get(params.get("end", "z")) if kg is not None else None
        return [{"receiver": rx, "errors_on_spare": v} for v in ("clear", "persist")]
    if check == "xc_audit":
        return ([{"reachable": False}, {"reachable": True, "mismatches": []}]
                + [{"reachable": True, "mismatches": [p]} for p in paths_at_switch])
    if check == "xcheck_reading":
        return [{"unit": params.get("unit"), "agree": v} for v in (True, False)]
    if check == "tcm_segments" and kg is not None and kg.has(params.get("path", "")):
        hops = kg.odu_hops(params["path"])
        segs = lambda k: [{"from": h["a"], "to": h["z"], "status": "failed" if i == k else "ok"}  # noqa: E731
                          for i, h in enumerate(hops)]
        return [{"segments": segs(k)} for k in range(-1, len(hops))]
    return []


def interpret(check: str, params: dict, result: dict, contenders: list[Hypothesis], twin=None,
              seed: int = 0, twin_keys: Optional[set] = None, kg=None) -> tuple[Optional[LikelihoodPayload], dict]:
    """(likelihood payload or None, {hypothesis key: verdict}) for one check's result. With twin_keys, the twin
    predicts only for those hypotheses (the ones it has already fitted); the rest are read by the check's meaning."""
    if result.get("status") != "done":
        return None, {h.key(): "unresolved" for h in contenders}
    value = result.get("value") or {}
    vals, verdicts = {}, {}
    base = None
    if twin is not None:
        b = twin.predict_check([], check, params, seed, t=result.get("t"))
        base = b["value"] if b["status"] == "done" else None
    for h in contenders:
        key = h.key()
        cost = None
        use_twin = twin is not None and (twin_keys is None or key in twin_keys)
        fit = twin.fit(h) if use_twin else {"declined": "no twin"}
        if "declined" not in fit:
            pred = twin.predict_check(fit["events"], check, params, seed, t=result.get("t"))
            if pred["status"] == "done":
                cost = compare(value, pred["value"], "", base)
        kgx = kg if kg is not None else getattr(twin, "kg", None)
        if cost is None and len(h.causes) == 1:
            c = h.causes[0]
            loc = c.location.element if c.location.kind == "element" else getattr(c.location, "span", None)
            if loc is not None:
                cost = direct(check, params, value, loc, c.fault_mode, kgx)
        elif cost is None and COMPOUND_READING and len(h.causes) > 1:
            c = compound_cause(kgx, check, params, h)
            if c == "elsewhere":
                cost = direct(check, params, value, "__elsewhere__", "NONE_OF_THESE", kgx)
            elif c is not None:
                loc = c.location.element if c.location.kind == "element" else getattr(c.location, "span", None)
                cost = direct(check, params, value, loc, c.fault_mode, kgx)
        if cost is None:
            vals[key] = LikelihoodValue(declined=True, decline_reason="the check says nothing about this contender")
            verdicts[key] = "unresolved"
        else:
            vals[key] = LikelihoodValue(log_likelihood=-round(cost, 3))
            verdicts[key] = "contradicted" if cost >= 3.0 else "supported" if cost <= 1.0 else "unresolved"
    if not any(not v.declined for v in vals.values()):
        # the check speaks of no contender yet: a term with no values moves nothing (it is kept so that the answer
        # can be read again for contenders the twin comes to cover)
        return LikelihoodPayload(values=vals, background_log_likelihood=None), verdicts
    # "none of these": what a cause nobody proposed would make of the answer. A check the twin predicts: such a
    # cause is charged as fitting the answer only to within two standard deviations (SAME_NATS), so an answer the
    # leader predicted lowers it and an answer no contender predicted raises it. A check read by its documented
    # meaning: such a cause lies elsewhere, so a finding at the place checked counts against it and a negative one
    # does not. The planner (advisor) values confirming checks on the same footing.
    if base is not None:
        bg = -SAME_NATS
    else:
        bg = -(direct(check, params, value, "__elsewhere__", "NONE_OF_THESE",
                      kg if kg is not None else getattr(twin, "kg", None)) or 0.0)
    return LikelihoodPayload(values=vals, background_log_likelihood=round(bg, 3)), verdicts
