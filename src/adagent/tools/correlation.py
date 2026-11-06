"""Classical alarm correlation: the control against which the agent is compared ("Reference Network,
Data Sets and Knowledge Graph" §6.2, Table 4).

What a network management system's correlation engine does, using only the graph:

1. take the raised alarms (the network elements have already applied G.798 defect correlation, so each
   reports its fault cause);
2. correlate them topologically: an alarm is a consequence of another if the other's element lies upstream
   on a signal path that reaches it, or if it is a client alarm of a service whose path the other's element
   carries; alarms that are consequences of none are root alarms;
3. take the root alarm that accounts for most others (then the most severe) and list the probable causes its
   documentation gives, resolved to concrete elements ("upstream span", "far-end transmitter", ...) by the
   grounding tool, which keeps those whose equipment documents the cause at that grain.

It gives no probabilities: the documented causes are listed with equal weight, as an engineer would receive
them. Faults that raise no alarm leave it with nothing to say. The code names no fault mode.
"""
from __future__ import annotations

from collections import defaultdict

from ..schemas import Candidate, GeneratorPayload, ScenarioFrame
from .grounding import GroundingTool
from .propagation import Propagator
from .walks import single

SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2}


class AlarmCorrelation:
    name, version = "alarm_correlation", "0.1"

    def __init__(self, kg):
        self.kg, self.prop = kg, Propagator(kg)
        self.grounding = GroundingTool(kg)
        self.trace: dict = {}

    # ------------------------------------------------------------------ positions on signal paths
    def _positions(self, el: str) -> set:
        kg, prop = self.kg, self.prop
        out = set()
        for lp, d in kg.lightpaths_through(el):
            path = prop.path(lp, d)
            if el in path:
                out.add((lp, d, path.index(el)))
        return out

    def run(self, frame: ScenarioFrame) -> GeneratorPayload:
        kg = self.kg
        alarms = [(s.observable, s.element, s.detail) for s in frame.symptoms if s.kind == "alarm"]
        alarms = list(dict.fromkeys(alarms))
        if not alarms:
            self.trace = {"root": None, "reason": "no alarms"}
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        pos = {a: self._positions(a[1]) for a in alarms}
        svc_of = {}
        for a in alarms:
            if kg.cls(a[1]) == "ClientPort":
                svc_of[a] = set(kg.services_through(a[1]))
        lps_of_svc = {}
        explains = defaultdict(set)
        # an equipment alarm (one documented as caused at its own unit) explains the client alarms of the
        # services that pass through that unit
        equip = {a for a in alarms if any(pc.get("where") in ("this_unit", "this_port")
                                          for x, d in kg.alarm_definitions(a[1]) if x == a[0]
                                          for pc in (d.get("probable_causes") or []))}
        for x in alarms:
            for y in alarms:
                if x == y:
                    continue
                # upstream on a common signal path
                if any(lx == ly and dx == dy and ix < iy for lx, dx, ix in pos[x] for ly, dy, iy in pos[y]):
                    explains[x].add(y)
                    continue
                if x in equip and y[1] == x[1]:               # other alarms on the failed unit itself
                    explains[x].add(y)
                    continue
                if x in equip and y in svc_of and svc_of[y] & set(kg.services_through(x[1])):
                    explains[x].add(y)
                    continue
                # a client alarm of a service carried through x's element
                if y in svc_of and pos[x]:
                    lps = {lp for lp, _, _ in pos[x]}
                    for s in svc_of[y]:
                        if s not in lps_of_svc:
                            lps_of_svc[s] = {h["lightpath"] for p in kg.service_paths(s).values() if p
                                             for h in kg.odu_hops(p)}
                        if lps & lps_of_svc[s]:
                            explains[x].add(y)
                            break
        explained = set().union(*explains.values()) if explains else set()
        roots = [a for a in alarms if a not in explained]
        if not roots:                       # mutual explanation (both directions of a cut): take all
            roots = alarms
        roots.sort(key=lambda a: (-len(explains[a]), SEVERITY_RANK.get(a[2], 9), a[0], a[1]))
        root = roots[0]
        cands = self._probable_causes(root)
        self.trace = {"root": root, "n_alarms": len(alarms), "n_roots": len(roots),
                      "explained_by_root": len(explains[root]), "n_candidates": len(cands)}
        if not cands:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        res = 0.1
        w = (1 - res) / len(cands)
        return GeneratorPayload(candidates=[Candidate(hypothesis=single(m, loc), prior_mass=w,
                                                      rationale=f"probable cause of root alarm {root[0]} at {root[1]}")
                                            for m, loc in cands], residual_mass=res)

    # ------------------------------------------------------------------ documented probable causes
    def _probable_causes(self, alarm) -> list[tuple[str, str]]:
        kg, prop = self.kg, self.prop
        al, el, _ = alarm
        defs = [d for a, d in kg.alarm_definitions(el) if a == al]
        if not defs and kg.cls(el) == "FibreSpan":           # reported against a span by the next unit
            nxt = [v for v, _ in kg.out(el, "feeds")]
            defs = [d for n in nxt for a, d in kg.alarm_definitions(n) if a == al]
            el_unit = nxt[0] if nxt else el
        else:
            el_unit = el
        out = []
        for d in defs:
            for pc in d.get("probable_causes") or []:
                mode, where = pc.get("mode"), pc.get("where")
                if not mode or mode.startswith("any_"):
                    continue
                for loc in self.grounding.resolve(where, el_unit, el):
                    if self.grounding.admits(loc, mode):
                        out.append((mode, loc))
        return list(dict.fromkeys(out))
