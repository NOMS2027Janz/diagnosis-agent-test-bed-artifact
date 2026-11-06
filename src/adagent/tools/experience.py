"""The experience store and the closure process that writes it ("Reference Network, Data Sets and
Knowledge Graph" §4.4).

The store belongs to the analogy tool. The closure process writes one stored case when each trouble ticket's
closure is recorded, using the knowledge graph as it stands at that moment, and never revises it afterwards:
the record says what the person closing the ticket could say with the inventory of that day, and later
changes to the network (equipment replaced, services removed, lightpaths re-routed) neither sharpen nor erode
it. The one later change is a follow-up finding (a returned unit's failure analysis, a later trace), which can
confirm the explanation, complete a coarse one with its fault mode, or refute it; it is kept as a dated update,
so that the store as it stood on any day shows only the findings recorded by then. Each stored case holds what was seen at the time, the explanation recorded and how it was verified, and
two descriptions written with the graph:

- a role signature: what was seen, in terms of alarm types, the classes and roles of the elements that
  raised them, and how many services went down or were switched, so that an incident on one line can match
  an incident on another;
- the recorded cause expressed as a relation to what was seen ("the span upstream of the amplifier that
  raised input loss", "the transmitter at the far end of the receiver that lost signal"), so that the cause
  can be carried to a new incident by the grounding tool.

Nothing here knows the truth of any incident; tickets can be coarse or wrong, and their verification status
is kept so that weak records count for less. Alarms raised by elements the inventory did not know at closure
(unrecorded or late-recorded equipment) are kept, with the role "Unknown", because the engineer saw them.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Optional

VERIFICATION_WEIGHT = {"repair_confirmed": 1.0, "engineer_concluded": 0.7, "agent_concluded": 0.3, "refuted": 0.0}


def bucket(n: int) -> str:
    return "0" if n == 0 else "1" if n == 1 else "2-5" if n <= 5 else "6-20" if n <= 20 else ">20"


def element_role(kg, el: str) -> str:
    base = el.split("|")[0]
    if "|" in el and kg.cls(base) == "OMS":
        return "WSSPort" if el.partition("|")[2] == "port" else "WSSChannel"
    if not kg.has(base):
        return "Unknown"
    c = kg.cls(base) or "Unknown"
    role = kg.g.nodes[base].get("role") or kg.g.nodes[base].get("kind")
    return f"{c}:{role}" if role else c


def signature(kg, alarms: list, n_down: int, n_hit: int, n_degraded: int) -> Counter:
    """Role signature of what was seen."""
    f = Counter()
    for a, el in alarms:
        f[f"alarm:{a}:{element_role(kg, el)}"] += 1
        f[f"alarm:{a}"] += 1
    f[f"down:{bucket(n_down)}"] += 1
    f[f"switched:{bucket(n_hit)}"] += 1
    f[f"degraded:{bucket(n_degraded)}"] += 1
    if not alarms:
        f["no_alarms"] += 1
    return f


def similarity(a: Counter, b: Counter) -> float:
    """Weighted Jaccard on feature counts (log-damped)."""
    keys = sorted(set(a) | set(b))          # a fixed order, so the float sums do not depend on set order
    if not keys:
        return 0.0
    da = {k: math.log1p(a.get(k, 0)) for k in keys}
    db = {k: math.log1p(b.get(k, 0)) for k in keys}
    num = sum(min(da[k], db[k]) for k in keys)
    den = sum(max(da[k], db[k]) for k in keys)
    return num / den if den else 0.0


# ---------------------------------------------------------------------- relations of a cause to what was seen

def relation(kg, loc: str, alarms: list) -> Optional[tuple]:
    """Express a recorded cause location relative to the alarms seen, using the graph."""
    if not loc or not (kg.has(loc.split("|")[0])):
        return None
    base = loc.split("|")[0]
    cls = kg.cls(base)
    freq = Counter(a for a, _ in alarms)
    order = sorted(alarms, key=lambda x: (freq[x[0]], x[0], x[1]))     # the rarest alarm is the most specific
    for a, el in order:
        if el == loc:
            return ("at", a)
    if cls == "ClientPort":
        return ("port_of_down_service",)
    if cls == "OTNSwitch":
        return ("xc_on_path_of_down_service",) if "|" in loc else ("switch_on_paths_of_down_services",)
    order = [(a, el) for a, el in order if kg.has(el.split("|")[0])]   # unknown elements give no path
    if cls == "LineInterface":
        for a, el in order:
            if kg.cls(el) == "LineInterface":
                for lp, d in kg.inn(el, "endsAt"):
                    ends = kg.lightpath_ends(lp)
                    if loc in ends.values() and loc != el:
                        return ("far_end_of", a)
    best = None
    for a, el in order:
        for lp, d in kg.lightpaths_through(el.split("|")[0]):
            path = kg.signal_path(lp, d)
            tgt = f"roadm.{kg.g.nodes[base]['dst']}" if "|" in loc and cls == "OMS" else base
            e0 = el.split("|")[0]
            if tgt in path and e0 in path:
                k = path.index(e0) - path.index(tgt)
                if k > 0 and (best is None or k < best[2]):
                    best = ("upstream_of", a, k, element_role(kg, loc))
    if best:
        return best
    return ("class", element_role(kg, loc))


@dataclass
class StoredCase:
    id: str
    closed_day: float
    recorded_day: float           # when the closure was recorded, and so when the case entered the store
    verification: str
    outcome: str
    mode: Optional[str]
    location: Optional[str]
    relation: Optional[tuple]
    signature: Counter
    weight: float
    alarms: tuple = ()            # what was seen, with element identifiers kept as references
    down_services: tuple = ()
    explanation: str = ""
    updates: list = field(default_factory=list)   # (day recorded, fields changed) from follow-up findings
    at_closure: dict = field(default_factory=dict, repr=False)   # the fields follow-ups can change, as closed

    def __post_init__(self):
        if not self.at_closure:
            self.at_closure = {k: getattr(self, k) for k in _MUTABLE}


_MUTABLE = ("verification", "weight", "mode", "relation")


def closure_record(tk: dict, kg, recorded_day: float) -> StoredCase:
    """The closure process for one ticket, with the graph as it stands when the closure is recorded."""
    obs = tk.get("observed") or {}
    alarms = tuple(tuple(a) for a in obs.get("alarms", []))
    down = tuple(sorted({cp.split(".")[1] for cp in obs.get("client_sf", [])}))
    sig = signature(kg, list(alarms), len(down), obs.get("prot_switches", 0), len(obs.get("pm_degraded", [])))
    loc = tk.get("recorded_location")
    return StoredCase(
        id=tk["id"], closed_day=tk["closed"], recorded_day=recorded_day,
        verification=tk.get("verification", "engineer_concluded"), outcome=tk.get("outcome", "resolved"),
        mode=tk.get("recorded_fault_mode"), location=loc,
        relation=relation(kg, loc, list(alarms)) if tk.get("recorded_fault_mode") else None,
        signature=sig, weight=VERIFICATION_WEIGHT.get(tk.get("verification"), 0.5),
        alarms=alarms, down_services=down, explanation=tk.get("explanation", ""))


def followup_fields(case: StoredCase, payload: dict, kg) -> dict:
    """What a follow-up finding changes. A fault mode supplied for a coarse record gets its relation written
    with the graph as it stands when the finding is recorded."""
    f = {"verification": payload["verification"],
         "weight": VERIFICATION_WEIGHT.get(payload["verification"], 0.5)}
    if payload.get("recorded_fault_mode") and payload["recorded_fault_mode"] != case.mode:
        f["mode"] = payload["recorded_fault_mode"]
        if case.relation is None:
            f["relation"] = relation(kg, case.location, list(case.alarms))
    return f


@dataclass
class ExperienceStore:
    """Written at closure; afterwards changed only by dated follow-up findings."""
    cases: list = field(default_factory=list)
    _by_id: dict = field(default_factory=dict, repr=False)

    def on_event(self, ev, kg) -> None:
        """Lifecycle observer: write a stored case when a ticket's closure is recorded, and apply follow-ups."""
        if ev.kind == "incident_closed":
            c = closure_record(ev.payload["ticket"], kg, ev.t_recorded)
            self.cases.append(c)
            self._by_id[c.id] = c
        elif ev.kind == "ticket_followup" and ev.payload["ticket_id"] in self._by_id:
            c = self._by_id[ev.payload["ticket_id"]]
            f = followup_fields(c, ev.payload, kg)
            c.updates.append((ev.t_recorded, f))
            for k, v in f.items():            # the store as it now stands
                setattr(c, k, v)

    def as_of(self, day: float) -> "ExperienceStore":
        """The store as it stood on a day: cases closed by then, with the follow-ups recorded by then."""
        out = ExperienceStore()
        for c in self.cases:
            if c.recorded_day > day:
                continue
            ch = dict(c.at_closure)
            for d, f in c.updates:
                if d <= day:
                    ch.update(f)
            v = replace(c, updates=[u for u in c.updates if u[0] <= day], at_closure=dict(c.at_closure), **ch)
            out.cases.append(v)
            out._by_id[v.id] = v
        return out

    def retrieve(self, sig: Counter, k: int = 20) -> list[tuple[float, StoredCase]]:
        scored = [(similarity(sig, c.signature), c) for c in self.cases]
        scored = [x for x in scored if x[0] > 0]
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return scored[:k]
