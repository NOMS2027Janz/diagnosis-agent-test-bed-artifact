"""Commitment at a coarser grain (C4, amendment 8).

When no single cause is probable enough to commit to, the candidates still in contention may agree on where the
trouble is, or on what kind of trouble it is: three contenders at one receiver (a slow loss of margin, a misreporting
power reading, a frozen one) say "the trouble is at this receiver" with near certainty, though not which of the three
it is. Causes are fractal: a board, then a part on it. An operator can act on the coarser answer.

At the end of the loop, when the ledger's decision is a set, the posterior is summed over the place each named
candidate is about, at successively coarser grains (the element, then the OMS for an element on one, then the site),
and then over its kind of fault (the fault mode). The finest grain whose mass reaches the commitment threshold, with
"none of these" below the threshold for an open set, is reported as a commitment at that grain. A candidate counts
toward a place only if all its causes are at that place (a compound spread over two places supports neither alone).
The ledger's own decision is unchanged; the coarse commitment is reported beside it and scored as its own outcome.

The finest grain of all is a single cause shared by the candidates (C4b): when the contenders are one cause alone and
that cause with various possible companions ("the port has failed, and perhaps something else on another line"), the
mass of every candidate containing the cause is summed, and the cause is committed to without its companions. It is
scored as a commitment to that one cause: right if it is a true fault (partly right when there were others).
"""
from __future__ import annotations

from typing import Optional

from ..ledger import NONE_KEY

GRAINS = ("cause", "element", "oms", "site", "mode")
CAUSE_ALONE_MIN = float(__import__("os").environ.get("ADAGENT_CAUSE_ALONE_MIN", "0.2"))   # C4b (0 = off)


def owner(kg, loc_key: str) -> Optional[str]:
    """The equipment element a candidate location is about (an instrument is about the element it measures; a WSS
    channel or port "oms|x" is about the ROADM the OMS ends at)."""
    el = loc_key.split(":", 1)[1] if loc_key.startswith("el:") else loc_key
    el = el.split("(")[0]
    if "|" in el:
        a, _ = el.split("|", 1)
        if kg.has(a) and kg.cls(a) == "OMS":
            return f"roadm.{kg.g.nodes[a]['dst']}"
        return a if kg.has(a) else None
    if kg.has(el) and kg.cls(el) == "Instrument":
        return kg.g.nodes[el].get("lake_element", el)
    return el if kg.has(el) else None


def place(kg, el: str, grain: str) -> Optional[str]:
    if el is None or not kg.has(el):
        return None
    a = kg.g.nodes[el]
    if grain == "element":
        return el
    if grain == "oms":
        return a.get("oms") or (el if a.get("cls") == "OMS" else None)
    if grain == "site":
        s = a.get("site")
        if s is None and a.get("oms") and kg.has(a["oms"]):
            return None                       # a span or an in-line unit lies between sites
        return f"site.{s}" if s else None
    return None


def coarse_commit(ledger, kg, theta: float, theta_open: float) -> Optional[dict]:
    post = ledger.posterior()
    if post.get(NONE_KEY, 0.0) >= theta_open:
        return None
    for grain in GRAINS:
        mass: dict = {}
        members: dict = {}
        for k, p in post.items():
            if k == NONE_KEY or p <= 0.0:
                continue
            h = ledger.hypothesis(k)
            if not h.causes:
                continue
            if grain == "cause":
                for c in h.causes:              # a candidate supports each cause it contains
                    lab = f"{c.fault_mode}@{c.location.key()}"
                    mass[lab] = mass.get(lab, 0.0) + p
                    members.setdefault(lab, []).append(k)
                continue
            if grain == "mode":
                labels = {c.fault_mode for c in h.causes}
            else:
                labels = {place(kg, owner(kg, c.location.key()), grain) for c in h.causes}
            if len(labels) != 1 or None in labels:
                continue
            lab = labels.pop()
            mass[lab] = mass.get(lab, 0.0) + p
            members.setdefault(lab, []).append(k)
        if not mass:
            continue
        lab, m = max(mass.items(), key=lambda kv: kv[1])
        if m >= theta:
            if grain == "cause" and all(len(ledger.hypothesis(k).causes) == 1 for k in members[lab]):
                continue                        # a single cause alone is the ledger's own commitment, not a grain
            if grain == "cause" and CAUSE_ALONE_MIN:
                # C4b: the cause must also stand on its own: its single-cause candidate holds at least this much.
                # When every candidate needs it together with further causes, the shared cause is not established.
                alone = sum(post[k] for k in members[lab] if len(ledger.hypothesis(k).causes) == 1)
                if alone < CAUSE_ALONE_MIN:
                    continue
            return {"grain": grain, "at": lab, "mass": round(m, 4), "members": members[lab]}
    return None
