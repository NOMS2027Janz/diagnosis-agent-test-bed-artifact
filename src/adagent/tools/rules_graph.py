"""Graph consistency with its rules as graph content, evaluated by a general engine (Experiment 2, rules comparison 2).

The same rules as tools/consistency.py, but held as data: knowledge/rules/consistency.yaml is published into the
graph (publish_rules: a Rule node per rule, a RuleVerdict node per verdict with its conditions, and the declared
completeness of each relation the rules use), and a general engine reads the rules from the graph and evaluates
them. The engine knows no rule. It joins conditions over relations computed for the case and the candidate (the
facts), and it enforces the records' completeness: a negated condition over a relation not declared complete is
unknown, so a verdict that depends on it is not reached and a request for that relation is recorded instead.

The facts are computed from the same sources as the coded version: the documentation's predicted effects for the
candidate (the Propagator), the case frame's observations, the graph's records. The comparison (scripts/
exp2_rules_compare.py) puts every candidate of every case to both versions and compares the verdicts rule by rule,
and their cost.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..schemas import Hypothesis, HypothesisKind
from .consistency import CONTRADICTED, SUPPORTED, UNRESOLVED, GraphConsistency, RuleResult, Verdict
from .walks import _explains

RULES_FILE = Path(__file__).resolve().parents[3] / "knowledge" / "rules" / "consistency.yaml"
OPS = {"==": operator.eq, ">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
UNKNOWN = object()


def publish_rules(kg, doc: dict, tag: str = "rules") -> None:
    """Write the rules into the graph."""
    kg.put_node("ruledoc.consistency", "RuleDocument", tag, document=doc["document"])
    for rel, q in doc["relations"].items():
        kg.put_node(f"relation.{rel}", "Relation", tag, completeness=q)
    for r in doc["rules"]:
        rid = f"rule.{r['id']}"
        kg.put_node(rid, "Rule", tag, applies=r["applies"])
        kg.put_edge("ruledoc.consistency", rid, "contains", tag)
        for i, v in enumerate(r["verdicts"]):
            vid = f"{rid}.v{i}"
            kg.put_node(vid, "RuleVerdict", tag, verdict=v["verdict"], reason=v["reason"], when=v["when"], order=i)
            kg.put_edge(rid, vid, "hasVerdict", tag, order=i)


@dataclass
class EngineRule:
    id: str
    applies: str
    verdicts: list = field(default_factory=list)      # (verdict, reason, conditions), in order


def rules_from_graph(kg) -> tuple[list[EngineRule], dict]:
    rules = []
    for rid in sorted(n for n, d in kg.g.nodes(data=True) if d.get("cls") == "Rule"):
        r = EngineRule(rid[5:], kg.g.nodes[rid]["applies"])
        for vid, _ in sorted(kg.out(rid, "hasVerdict"), key=lambda x: x[1]["order"]):
            a = kg.g.nodes[vid]
            r.verdicts.append((a["verdict"], a["reason"], a["when"]))
        rules.append(r)
    completeness = {n[9:]: d["completeness"] for n, d in kg.g.nodes(data=True) if d.get("cls") == "Relation"}
    return rules, completeness


def _var(x) -> bool:
    return isinstance(x, str) and x[:1].isupper() and x.isalnum()


class Engine:
    """Conjunctive conditions over relation tables, first verdict that holds wins."""

    def __init__(self, completeness: dict):
        self.completeness = completeness
        self.requests: list = []

    def holds(self, conds: list, facts: dict) -> object:
        """True, False or UNKNOWN (a negation over a relation that is not complete was needed)."""
        unknown = []

        def solve(i: int, env: dict):
            if i == len(conds):
                return True
            c = conds[i]
            if c[0] == "count":
                _, rel, op, n = c
                return OPS[op](len(facts.get(rel, ())), n) and solve(i + 1, env)
            if c[0] == "fraction":
                _, num, den, op, x = c
                d = len(facts.get(den, ()))
                return d > 0 and OPS[op](len(facts.get(num, ())), x * d) and solve(i + 1, env)
            rel, args = c[0], c[1]
            neg = len(c) > 2 and c[2] == "not"
            if neg:
                tup = tuple(env.get(a, a) if _var(a) else a for a in args)
                if any(_var(t) for t in tup):
                    raise ValueError(f"unbound variable in negated condition {c}")
                if tup in facts.get(rel, set()):
                    return False
                if self.completeness.get(rel, "incomplete") != "complete":
                    unknown.append(rel)            # absence cannot be read as false here
                    return False
                return solve(i + 1, env)
            for row in facts.get(rel, ()):
                env2 = dict(env)
                ok = True
                for a, v in zip(args, row):
                    if _var(a):
                        if a in env2 and env2[a] != v:
                            ok = False
                            break
                        env2[a] = v
                    elif a != v:
                        ok = False
                        break
                if ok and solve(i + 1, env2):
                    return True
            return False
        if solve(0, {}):
            return True
        return UNKNOWN if unknown else False

    def evaluate(self, rule: EngineRule, facts: dict) -> RuleResult:
        asked = []
        for verdict, reason, conds in rule.verdicts:
            r = self.holds(conds, facts)
            if r is True:
                return RuleResult(rule.id, verdict, reason, requests=[{"read": rel} for rel in asked])
            if r is UNKNOWN:
                asked += [c[0] for c in conds if len(c) > 2 and c[2] == "not"
                          and self.completeness.get(c[0], "incomplete") != "complete"]
        return RuleResult(rule.id, UNRESOLVED, "no verdict reached", requests=[{"read": rel} for rel in asked])


class GraphRuleConsistency(GraphConsistency):
    """Graph consistency whose rules are read from the graph and evaluated by the engine."""
    name, version = "consistency.graph_rules", "0.1"

    def __init__(self, kg, frame, doc: dict | None = None):
        super().__init__(kg, frame)
        if not any(d.get("cls") == "Rule" for _, d in kg.g.nodes(data=True)):
            publish_rules(kg, doc or yaml.safe_load(RULES_FILE.read_text()))
        self.rules, self.completeness = rules_from_graph(kg)
        self.engine = Engine(self.completeness)

    # ------------------------------------------------------------------ facts
    def _facts_equipment(self, mode: str, el: str, P) -> dict:
        f: dict = {}
        O = self.obs.O
        f["required"] = {(k,) for k, s in P.items() if s == "required" and k[0] != "client"}
        f["observed"] = {(k,) for k in O}
        f["examined"] = {(k,) for (k,) in f["required"] if self.obs.examined(k)}
        f["silent"] = {(k,) for (k,) in f["required"] if k[0] == "obs" and k[2].split("|")[0] in self.silent_lake}
        f["must_down"] = {(k[2],) for k, s in P.items() if k[0] == "client" and k[1] == "down" and s == "required"}
        f["can_down"] = {(k[2],) for k in P if k[0] == "client"}
        f["down"] = {(s,) for s in self.down}
        f["ok"] = {(s,) for s in self.ok}
        f["observation"] = {(k,) for k in O}
        f["explained"] = {(k,) for k in O if _explains(P, self.obs, k)}
        owner = self.prop._owner(el)
        if any(m == mode for m, _ in self.kg.fault_modes_for(owner)):
            f["documented"] = {(mode, owner)}
        f["_bind"] = {"M": mode, "O": owner}
        return f

    def _facts_instrument(self, inst_el: str) -> dict:
        mine = {k for k in self.obs.O if k[0] == "obs" and k[2].split("|")[0] == inst_el}
        down: set = set()
        if self.kg.has(inst_el) and self.kg.cls(inst_el) != "LineInterface":
            for lp, d in self.kg.lightpaths_through(inst_el):
                path = self.prop.path(lp, d)
                if inst_el in path:
                    down |= set(path[path.index(inst_el) + 1:])
        changed = {k for k in self.obs.O if k[0] in ("obs", "alarm") and k[2].split("|")[0] in down}
        quiet = [el for el in down if any(self.obs.examined(("obs", ob, el)) and ("obs", ob, el) not in self.obs.O
                                          for _, ob, _ in self._streams(el))]
        return {"my_reading_changed": {(k,) for k in mine}, "downstream_changed": {(k,) for k in changed},
                "downstream_quiet": {(e,) for e in quiet}}

    def _facts_spans(self, spans: list[str]) -> dict:
        def common(rel):
            sets = [{c for c, _ in self.kg.out(s, rel)} for s in spans]
            return bool(sets and all(sets) and set.intersection(*sets))
        f = {"span_group": {("g",)}}
        if common("inDuct"):
            f["common_duct"] = {("g",)}
        if common("inCable"):
            f["common_cable"] = {("g",)}
        return f

    def _run(self, applies: str, facts: dict) -> list[RuleResult]:
        out = []
        bind = facts.pop("_bind", {})
        for r in self.rules:
            if r.applies != applies:
                continue
            if bind:     # constants of the candidate (its mode and owner) substituted into the rule's conditions
                r = EngineRule(r.id, r.applies, [(v, why, [[c[0], [bind.get(a, a) for a in c[1]]] + list(c[2:])
                                                            if c[0] not in ("count", "fraction") else c
                                                            for c in conds]) for v, why, conds in r.verdicts])
            out.append(self.engine.evaluate(r, facts))
        return out

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
                rules += self._run("instrument", self._facts_instrument(a.get("lake_element", el)))
                continue
            P = self.prop.predict(c.fault_mode, el)
            facts = self._facts_equipment(c.fault_mode, el, P)
            if not P:
                rules += [x for x in self._run("equipment", dict(facts)) if x.rule == "R4"]
                rules.append(RuleResult("R1", UNRESOLVED, "the documentation gives no effects to test"))
                continue
            if len(h.causes) == 1:
                rules += self._run("equipment", dict(facts))
                rules += self._run("equipment_single", {k: v for k, v in facts.items() if k != "_bind"})
            else:
                # a compound is judged on its causes acting together (below); each cause keeps its documentation rule
                rules += [x for x in self._run("equipment", dict(facts)) if x.rule == "R4"]
            if self.kg.cls(el) == "FibreSpan":
                span_targets.append(el)
        if len(h.causes) > 1 or any(c.location.kind == "set" for c in h.causes):
            joint = []
            for c in h.causes:
                if c.fault_mode in ("INSTRUMENT_MISREPORT", "TELEMETRY_STALE"):
                    continue
                loc = c.location
                els = list(loc.elements) if loc.kind == "set" else [loc.element if loc.kind == "element" else loc.span]
                joint += [(c.fault_mode, e) for e in els]
            if joint:
                Pj = self.prop.predict_joint(joint)
                facts = self._facts_equipment(joint[0][0], joint[0][1], Pj)
                rules += [x for x in self._run("equipment", dict(facts)) if x.rule != "R4"]
                rules += self._run("equipment_single", {k: v for k, v in facts.items() if k != "_bind"})
        if len(set(span_targets)) >= 2 and len(h.causes) >= 2 or any(c.location.kind == "set" for c in h.causes):
            rules += self._run("several_spans", self._facts_spans(sorted(set(span_targets))))
        verdicts = {r.verdict for r in rules}
        v = CONTRADICTED if CONTRADICTED in verdicts else SUPPORTED if SUPPORTED in verdicts else UNRESOLVED
        out = Verdict(h.key(), v, rules)
        self.log.append(out)
        return out
