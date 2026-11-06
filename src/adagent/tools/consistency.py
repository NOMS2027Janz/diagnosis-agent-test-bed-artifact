"""Graph consistency (likelihood tool C1; "The Diagnosis Agent Test Bed" §9.2), in rule-engine form.

Each hypothesis is put to a fixed set of rules over the knowledge graph and the case frame. Every rule returns one
of three verdicts, supported, contradicted or unresolved, with its reason and the graph relations it relied on.
A rule that would have to read absence as false over a relation the records declare incomplete
(KnowledgeGraph.completeness) returns unresolved instead, and says what would resolve it: a request for the missing
relation or reading. The hypothesis' verdict is contradicted if any rule contradicts it, supported if at least one
rule supports it and none contradicts, and unresolved otherwise.

Rules (the forward predictions are the Propagator's, from the documentation in the graph):

  R1 required effects   every effect the hypothesis requires at an instrument that was examined was seen. An
                        instrument whose stream is silent leaves the effect unresolved (request: its data).
  R2 services           every service the hypothesis requires down is down, and every service down is one the
                        hypothesis can take down (service paths are complete once recorded, so a service down
                        outside the hypothesis' reach contradicts it as a sole cause).
  R3 symptoms explained the hypothesis explains most of what was observed (supported), or very little of it
                        (unresolved: another cause may be at work as well; never a contradiction on its own).
  R4 documented         the fault mode is documented for the equipment (supported); absence is unresolved, since
                        documentation is complete only as far as it goes.
  R5 shared risk        a hypothesis that one event caused failures on several spans is supported when the plant
                        records place them in a common duct or cable; absence is unresolved (shared risk is
                        incomplete in the records; request: a plant records check or field survey).
  R6 monitoring         a hypothesis that an instrument is at fault is supported when the readings around it on
                        the same signal path did not change with it, and contradicted when they changed alike.

Verdicts map to a likelihood term for the ledger: supported 0, unresolved 0 (declined is not used, so that every
hypothesis is covered), contradicted -CONTRADICTION_NATS for each contradicting rule, at most two. A contradiction
is not a certainty, since records can lag the network; the ledger's cap and tempering apply as for any term.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import Hypothesis, HypothesisKind, LikelihoodPayload, LikelihoodValue, ScenarioFrame
from .propagation import Propagator
from .walks import Observations, _explains

CONTRADICTION_NATS = 4.0
EQUIP_NATS = 2.0             # per alarm or service effect, as the twin charged a discrete difference (twin.py)
Z_CAP_NATS = 8.0            # at most, as for one stream of the twin
SUPPORTED, CONTRADICTED, UNRESOLVED = "supported", "contradicted", "unresolved"


@dataclass
class RuleResult:
    rule: str
    verdict: str
    reason: str
    relations: list = field(default_factory=list)
    requests: list = field(default_factory=list)


@dataclass
class Verdict:
    hypothesis: str
    verdict: str
    rules: list

    def requests(self) -> list:
        return [r for x in self.rules for r in x.requests]


class GraphConsistency:
    name, version = "consistency.graph", "0.1"

    def __init__(self, kg, frame: ScenarioFrame):
        self.kg, self.frame = kg, frame
        self.prop = Propagator(kg)
        self.obs = Observations(frame, kg)
        self.silent_lake = set()
        for s in frame.silent_streams:                 # "instrument:observable"
            inst = s.rsplit(":", 1)[0]
            a = kg.g.nodes.get(inst, {})
            if a.get("lake_element"):
                self.silent_lake.add(a["lake_element"])
        self.down = {v.service for v in frame.services if v.state == "down"}
        self.ok = {v.service for v in frame.services if v.state == "ok"}
        self.log: list[Verdict] = []

    # ------------------------------------------------------------------ rules
    def _r1(self, P) -> RuleResult:
        missing, silent = [], []
        for k, s in P.items():
            if s != "required" or k[0] == "client":
                continue
            if k in self.obs.O:
                continue
            if k[0] == "obs" and k[2].split("|")[0] in self.silent_lake:
                silent.append(k)
            elif self.obs.examined(k):
                missing.append(k)
        rel = ["traverses", "carriedBy", "monitoredBy", "hasFaultMode"]
        if missing:
            return RuleResult("R1", CONTRADICTED, f"{len(missing)} required effects not seen, e.g. {missing[0]}", rel)
        if silent:
            return RuleResult("R1", UNRESOLVED, f"{len(silent)} required effects at silent instruments", rel,
                              [{"read": "instrument data", "element": k[2]} for k in silent[:5]])
        req = [k for k, s in P.items() if s == "required"]
        if req:
            return RuleResult("R1", SUPPORTED, f"all {len(req)} required effects seen", rel)
        return RuleResult("R1", UNRESOLVED, "no required effects to test", rel)

    def _r2(self, P) -> RuleResult:
        rel = ["working", "protecting", "oduHop", "carriedBy"]
        can_down = {k[2] for k in P if k[0] == "client"}
        must_down = {k[2] for k, s in P.items() if k[0] == "client" and k[1] == "down" and s == "required"}
        wrongly_up = sorted(must_down & self.ok)
        unexplained = sorted(self.down - can_down)
        if wrongly_up:
            return RuleResult("R2", CONTRADICTED, f"requires {wrongly_up[0]} down, but it is not", rel)
        if unexplained:
            return RuleResult("R2", CONTRADICTED, f"cannot take down {unexplained[0]}, which is down", rel)
        if self.down:
            return RuleResult("R2", SUPPORTED, f"accounts for all {len(self.down)} services down", rel)
        return RuleResult("R2", UNRESOLVED, "no service is down", rel)

    def _r3(self, P) -> RuleResult:
        n = len(self.obs.O)
        if n == 0:
            return RuleResult("R3", UNRESOLVED, "nothing observed")
        expl = sum(1 for k in self.obs.O if _explains(P, self.obs, k))
        if expl >= 0.7 * n:
            return RuleResult("R3", SUPPORTED, f"explains {expl} of {n} observations")
        return RuleResult("R3", UNRESOLVED, f"explains {expl} of {n} observations; another cause may be at work")

    def _r4(self, mode: str, loc: str) -> RuleResult:
        owner = self.prop._owner(loc)
        documented = any(m == mode for m, _ in self.kg.fault_modes_for(owner))
        if documented:
            return RuleResult("R4", SUPPORTED, "documented for this equipment", ["hasType", "hasFaultMode"])
        return RuleResult("R4", UNRESOLVED, "not in this equipment's documentation (documentation is not "
                          "complete)", ["hasType", "hasFaultMode"],
                          [{"read": "documentation of other equipment of the same class"}])

    def _r5(self, spans: list[str]) -> RuleResult:
        rel = ["inDuct", "inCable"]
        groups = []
        for rel_name in rel:
            sets = [{c for c, _ in self.kg.out(s, rel_name)} for s in spans]
            if sets and all(sets) and set.intersection(*sets):
                groups.append(rel_name)
        if groups:
            return RuleResult("R5", SUPPORTED, f"recorded in a common {groups[0][2:].lower()}", rel)
        if all(self.kg.completeness(r) == "complete_once_recorded" for r in rel):
            return RuleResult("R5", CONTRADICTED, "no common duct or cable, and the records are complete", rel)
        return RuleResult("R5", UNRESOLVED, "no common duct or cable recorded; shared risk records are incomplete",
                          rel, [{"check": "plant records or field survey of the ducts", "spans": spans}])

    def _r6(self, inst_el: str) -> RuleResult:
        """An instrument at fault changes its own readings only. A real change of the signal at the element would
        reach the instruments downstream of it on the same signal paths; if those were examined and saw nothing,
        the change is in the instrument; if they changed too, it is in the signal."""
        mine = {k for k in self.obs.O if k[0] == "obs" and k[2].split("|")[0] == inst_el}
        if not mine:
            return RuleResult("R6", UNRESOLVED, "no reading of this instrument changed", ["monitoredBy"])
        down: set = set()
        for lp, d in self.kg.lightpaths_through(inst_el) if self.kg.has(inst_el) else []:
            path = self.prop.path(lp, d)
            if inst_el in path:
                down |= set(path[path.index(inst_el) + 1:])
        if self.kg.has(inst_el) and self.kg.cls(inst_el) == "LineInterface":
            down = set()                          # a receiver is the end of its paths: nothing downstream to compare
        changed = {k for k in self.obs.O if k[0] in ("obs", "alarm") and k[2].split("|")[0] in down}
        quiet = [el for el in down if any(self.obs.examined(("obs", ob, el)) and ("obs", ob, el) not in self.obs.O
                                          for _, ob, _ in self._streams(el))]
        rel = ["monitoredBy", "traverses", "carriedBy"]
        if changed:
            return RuleResult("R6", CONTRADICTED, f"{len(changed)} readings downstream changed with it", rel)
        if quiet:
            return RuleResult("R6", SUPPORTED, f"{len(quiet)} instruments downstream saw no change", rel)
        return RuleResult("R6", UNRESOLVED, "nothing downstream was examined", rel,
                          [{"check": "compare with an independent measurement", "element": inst_el}])

    def _streams(self, el: str) -> list:
        return [(i, ob, a.get("lake_element")) for i, a in self.kg.instruments_on(el) for ob in a.get("observes", [])]

    # ------------------------------------------------------------------ hypotheses
    def evaluate(self, h: Hypothesis) -> Verdict:
        rules: list[RuleResult] = []
        if h.kind == HypothesisKind.NONE_OF_THESE:
            return Verdict(h.key(), UNRESOLVED, [])
        span_targets = []
        for c in h.causes:
            loc = c.location
            if loc.kind == "set":
                span_targets += [e for e in loc.elements if self.kg.cls(e) == "FibreSpan"]
                continue
            el = loc.element if loc.kind == "element" else loc.span
            if c.fault_mode in ("INSTRUMENT_MISREPORT", "TELEMETRY_STALE"):
                a = self.kg.g.nodes.get(el, {})
                rules.append(self._r6(a.get("lake_element", el)))
                continue
            P = self.prop.predict(c.fault_mode, el)
            rules.append(self._r4(c.fault_mode, el))
            if not P:
                rules.append(RuleResult("R1", UNRESOLVED, "the documentation gives no effects to test"))
                if len(h.causes) == 1:
                    # C4 (amendment 8): with no documented effects it can take no service down, so services that
                    # are down count against it, as they do against a documented mode that cannot take them down
                    rules.append(self._r2(P))
                continue
            if len(h.causes) == 1:
                rules += [self._r1(P), self._r3(P), self._r2(P)]
            if self.kg.cls(el) == "FibreSpan":
                span_targets.append(el)
        if len(h.causes) > 1 or any(c.location.kind == "set" for c in h.causes):
            # services are judged on the causes acting together (a working and a protecting path hit by two causes)
            joint = []
            for c in h.causes:
                if c.fault_mode in ("INSTRUMENT_MISREPORT", "TELEMETRY_STALE"):
                    continue
                loc = c.location
                els = list(loc.elements) if loc.kind == "set" else [loc.element if loc.kind == "element" else loc.span]
                joint += [(c.fault_mode, el) for el in els]
            if joint:
                # a compound's effects are those of its causes acting together (a protection-unavailable alarm that
                # one cut alone would raise is not required when the other cut takes the service down)
                Pj = self.prop.predict_joint(joint)
                rules += [self._r1(Pj), self._r3(Pj), self._r2(Pj)]
        if len({s for s in span_targets}) >= 2 and len(h.causes) >= 2 or \
                any(c.location.kind == "set" for c in h.causes):
            rules.append(self._r5(sorted(set(span_targets))))
        verdicts = {r.verdict for r in rules}
        v = CONTRADICTED if CONTRADICTED in verdicts else SUPPORTED if SUPPORTED in verdicts else UNRESOLVED
        out = Verdict(h.key(), v, rules)
        self.log.append(out)
        return out

    def equipment_likelihood(self, hyps: list[Hypothesis]) -> LikelihoodPayload:
        """The alarms and service effects (down, switched) in the frame against the documented propagation of each
        hypothesis's causes acting together: EQUIP_NATS for each observed item it does not produce and for each item
        it requires that was examined and not seen, at most Z_CAP_NATS. The comparison of equipment behaviour that a
        twin modelling it would make, done from the documentation. An unconsidered cause is charged one item."""
        vals = {}
        for h in hyps:
            joint = []
            for c in h.causes:
                if c.fault_mode in ("INSTRUMENT_MISREPORT", "TELEMETRY_STALE"):
                    continue
                loc = c.location
                els = list(loc.elements) if loc.kind == "set" else [loc.element if loc.kind == "element" else loc.span]
                joint += [(c.fault_mode, el) for el in els]
            P = self.prop.predict_joint(joint)
            seen = [k for k in self.obs.O if k[0] in ("alarm", "client")]
            unexplained = sum(1 for k in seen if not _explains(P, self.obs, k))
            absent = sum(1 for k, st in P.items() if k[0] in ("alarm", "client") and st == "required"
                         and k not in self.obs.O and self.obs.examined(k))
            vals[h.key()] = LikelihoodValue(log_likelihood=-min(EQUIP_NATS * (unexplained + absent), Z_CAP_NATS))
        return LikelihoodPayload(values=vals, background_log_likelihood=-EQUIP_NATS)

    def likelihood(self, hyps: list[Hypothesis]) -> LikelihoodPayload:
        vals = {}
        for h in hyps:
            v = self.evaluate(h)
            n = sum(1 for r in v.rules if r.verdict == CONTRADICTED)
            vals[h.key()] = LikelihoodValue(log_likelihood=-CONTRADICTION_NATS * min(n, 2))
        return LikelihoodPayload(values=vals, background_log_likelihood=0.0)
