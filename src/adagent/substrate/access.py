"""The agent's only window on the network ("Reference Network, Data Sets and Knowledge Graph" §4.2).

`Substrate` joins the knowledge graph as recorded at the question's day and the data lake up to the
question's time. Tools see the network only through it. It turns lake observations into evidence items
with stable identifiers, so two tools reading the same observation refer to the same item and the ledger
can keep dependent evidence from being counted twice. It records which evidence each tool read and what
each acquisition cost.

It also answers the two observability questions the design note raises: which streams the graph says
should exist for an element (and which of them are silent), and which streams arrive from sources the
graph does not know.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..lake import Lake, Observation
from ..schemas import EvidenceItem
from .kg import KnowledgeGraph


def to_evidence(o: Observation) -> EvidenceItem:
    v = o.value
    if isinstance(v, dict):
        v = str(v)
    elif isinstance(v, float) and math.isinf(v):
        v = -999.0
    elif isinstance(v, list):
        v = [(-999.0 if (isinstance(x, float) and math.isinf(x)) else x) for x in v]
    return EvidenceItem(id=o.id, source=o.source, observable=o.observable, element=o.element,
                        instrument=o.instrument, t_start=o.t_start, t_end=o.t_end, value=v, sigma=o.sigma)


@dataclass
class Sample:
    t: float
    value: float
    evidence_id: str


@dataclass
class Substrate:
    kg: KnowledgeGraph
    lake: Lake
    now: float                                    # question time, seconds on the lake's clock
    day: Optional[float] = None                   # day of the history at which the graph is recorded
    acquire_fn: Optional[Callable[[str, dict], Optional[Observation]]] = None
    acquisitions: list = field(default_factory=list)
    check_fn: Optional[Callable[[str, dict], dict]] = None
    read_log: list = field(default_factory=list)  # (reader, evidence id)
    reader: str = "unknown"

    # ------------------------------------------------------------------ lake reads (never past `now`)
    def _q(self, **kw) -> list[Observation]:
        kw["t1"] = min(kw.get("t1", math.inf), self.now + 1e-9)
        res = [o for o in self.lake.query(**kw) if o.t_start <= self.now]
        self.read_log += [(self.reader, o.id) for o in res]
        return res

    def alarms(self, t0: float = -math.inf, t1: float = math.inf, element: Optional[str] = None,
               include_unalarmed: bool = False) -> list[Observation]:
        res = self._q(source="alarm", t0=t0, t1=t1)
        if element is not None:
            res = [o for o in res if o.element == element]
        if not include_unalarmed:
            res = [o for o in res if o.value != "not_alarmed"]
        return sorted(res, key=lambda o: (o.t_start, o.observable, o.element))

    def events(self, observable: Optional[str] = None, t0: float = -math.inf, t1: float = math.inf) -> list[Observation]:
        res = self._q(source="event", t0=t0, t1=t1)
        return [o for o in res if observable is None or o.observable == observable]

    def records(self, observable: str, element: Optional[str] = None, t0: float = -math.inf,
                t1: float = math.inf) -> list[Observation]:
        """Raw observations (PM bins, sampled readings, chunks of fast streams)."""
        return self._q(observable=observable, element=element, t0=t0, t1=t1)

    def samples(self, observable: str, element: str, t0: float = -math.inf, t1: float = math.inf) -> list[Sample]:
        """Time-ordered samples, with chunks of fast streams expanded; each keeps its evidence id."""
        out = []
        t1 = min(t1, self.now)
        for o in self.records(observable, element, t0, t1):
            if o.period and isinstance(o.value, list):
                for k, v in enumerate(o.value):
                    t = o.t_start + k * o.period
                    if t0 <= t <= t1:
                        out.append(Sample(t, v, o.id))
            elif isinstance(o.value, (int, float)) and t0 <= o.t_start <= t1:
                out.append(Sample(o.t_start, float(o.value), o.id))
        return sorted(out, key=lambda s: s.t)

    def latest(self, observable: str, element: str) -> Optional[Sample]:
        s = self.samples(observable, element)
        return s[-1] if s else None

    def elements_reporting(self, observable: str) -> set[str]:
        return self.lake.elements(observable)

    def evidence(self, o: Observation) -> EvidenceItem:
        return to_evidence(o)

    # ------------------------------------------------------------------ observability
    def expected_streams(self, element: str) -> list[tuple[str, str, str]]:
        """(instrument, observable, lake element) the graph records for an element."""
        out = []
        for inst, a in self.kg.instruments_on(element):
            for ob in a.get("observes", []):
                out.append((inst, ob, a["lake_element"]))
        return out

    def silent_streams(self, element: str, t0: float, t1: float) -> list[tuple[str, str]]:
        """Recorded instruments on the element that produced nothing in [t0, t1]: evidence that the
        instrument, or its reporting, has failed. 15-minute PM is skipped when no bin closed in the window."""
        out = []
        for inst, ob, le in self.expected_streams(element):
            if ob.endswith("_pm15") and not self.lake.query(observable=ob, t0=t0, t1=min(t1, self.now)):
                continue
            per_channel = self.kg.g.nodes[inst].get("per_channel")
            if per_channel:
                have = any(e.split("|")[0] == le for e in self.lake.elements(ob))
            else:
                have = bool(self.lake.query(observable=ob, element=le, t0=t0, t1=min(t1, self.now)))
            if not have:
                out.append((inst, ob))
        return out

    def unknown_sources(self, observables: Optional[list[str]] = None) -> list[tuple[str, str]]:
        """(observable, lake element) pairs that report data but have no instrument in the graph."""
        out = []
        for ob in observables or list(self.lake._by_obs):
            for el in self.lake.elements(ob):
                if not any(ob in self.kg.g.nodes[i].get("observes", [])
                           for i in self.kg.instruments_for_lake_element(el)):
                    out.append((ob, el))
        return sorted(out)

    # ------------------------------------------------------------------ context
    def maintenance(self, site: Optional[str] = None, since_day: float = -math.inf) -> list[dict]:
        out = []
        for mw in self.kg.nodes_of("MaintenanceWindow"):
            a = self.kg.g.nodes[mw]
            s = self.kg.out(mw, "at")[0][0].split(".", 1)[1]
            if (site is None or s == site) and since_day <= a["t_day"] <= (self.day if self.day is not None else math.inf):
                out.append({"site": s, "t_day": a["t_day"], "duration_h": a["duration_h"]})
        return sorted(out, key=lambda m: m["t_day"])

    # ------------------------------------------------------------------ acquisition
    def run_check(self, check: str, params: dict) -> dict:
        """Ask for a remotely triggered or field test (knowledge/practice/checks.yaml lists them). Returns the
        result as a dict: status (done, unavailable or failed), value, minutes, dispatch_sites, service_impact,
        repaired. A failed or unavailable check says nothing about the network."""
        if self.check_fn is None:
            r = {"check": check, "status": "unavailable", "value": {"reason": "no checks in this setting"}}
        else:
            r = self.check_fn(check, params)
        self.acquisitions.append({"check": check, **params, "status": r.get("status"),
                                  "minutes": r.get("minutes", 0), "dispatch_sites": r.get("dispatch_sites", []),
                                  "t": r.get("t")})
        # what was repaired is the world's truth; the agent learns of a repair only from the answer itself
        return {k: v for k, v in r.items() if k != "repaired"}

    def acquire(self, kind: str, params: dict, cost: float = 1.0) -> Optional[Observation]:
        """Request a reading the harness fulfils from the world (e.g. an OTDR trace), at a cost."""
        if self.acquire_fn is None:
            return None
        o = self.acquire_fn(kind, params)
        self.acquisitions.append({"kind": kind, "cost": cost, **params, "fulfilled": o is not None})
        return o
