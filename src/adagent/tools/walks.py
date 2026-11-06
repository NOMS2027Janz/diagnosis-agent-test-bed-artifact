"""Generation by knowledge-graph walks ("Reference Network, Data Sets and Knowledge Graph" §6.2).

Three general walk methods over the same graph, each a generator (contract: generator). All domain
knowledge comes from the graph through the Propagator; the code names no fault mode.

- AbductionWalk: walk backward from every observed symptom along signal paths, ODU paths and service
  closures to the elements that could have caused it; attach each element's documented fault modes; walk
  forward from each candidate (Propagator) and score it on the observations it explains, the required or
  expected effects an examined instrument should have seen and did not, and its documented base rate.
- RandomWalkRanking: personalised PageRank from the symptomatic elements over the reversed signal-flow
  graph; elements ranked by visit probability, each element's documented fault modes weighted by base
  rate. No forward check: the contrast with abduction.
- MinimumExplanation: greedy set cover of the observations by the abduction candidates' predictions,
  giving the best single explanation and, when one cause does not suffice, a compound one.

Observations come only from the case frame.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

import networkx as nx

from ..schemas import (Candidate, Cause, ElementLocation, GeneratorPayload, Hypothesis, HypothesisKind,
                       ScenarioFrame)
from .grounding import PORT, admits, wss_locations
from .propagation import RANK, Propagator


def cause_key(mode: str, loc: str) -> str:
    return f"{mode}@{loc}"


def single(mode: str, loc: str) -> Hypothesis:
    return Hypothesis(id=cause_key(mode, loc), kind=HypothesisKind.SINGLE,
                      causes=[Cause(fault_mode=mode, location=ElementLocation(element=loc))])


# ---------------------------------------------------------------------- observations from the frame

class Observations:
    def __init__(self, frame: ScenarioFrame, kg):
        self.kg = kg
        self.O: set = set()
        self.kind: dict = {}                     # obs key -> level | lost | restored | excursion | pm
        for s in frame.symptoms:
            if s.kind == "alarm":
                self.O.add(("alarm", s.observable, s.element))
            elif s.kind in ("change", "pm"):
                k = ("obs", s.observable, s.element)
                self.O.add(k)
                self.kind[k] = "pm" if s.kind == "pm" else (s.detail.split()[0] if s.detail else "level")
        for v in frame.services:
            if v.state in ("down", "hit"):
                self.O.add(("client", v.state, v.service))
        self.quiet: set = set()
        for i in frame.quiet_instruments:
            a = kg.g.nodes.get(i, {})
            if a.get("per_channel"):
                continue                     # a channel monitor is quiet channel by channel (quiet_streams)
            for ob in a.get("observes", []):
                self.quiet.add((ob, a.get("lake_element")))
        for qs in frame.quiet_streams:
            ob, _, el = qs.partition("@")
            self.quiet.add((ob, el))
        self.hurt = [v.service for v in frame.services if v.state in ("down", "hit", "errored", "degraded")]

    def examined(self, key) -> bool:
        if key[0] in ("alarm", "client") or key in self.O:
            return True
        return key[0] == "obs" and ((key[1], key[2]) in self.quiet or (key[1], key[2].split("|")[0]) in self.quiet)

    def symptom_elements(self) -> Counter:
        """Graph elements at which symptoms were observed, with counts."""
        kg = self.kg
        c = Counter()
        for k in self.O:
            if k[0] == "client":
                c[k[2]] += 1
                continue
            el = k[2]
            if "|" in el and ":" in el:                         # channel monitor "site:oms|slot"
                site_oms, slot = el.split("|")
                c[f"{site_oms.split(':', 1)[1]}|{slot}"] += 1
            elif kg.has(el):
                c[el] += 1
        return c


def _prior(kg, prop: Propagator, mode: str, loc: str) -> float:
    """Documented rate of the mode on the location's type, raised by how often closed tickets recorded it."""
    owner = prop._owner(loc)
    edge = prop.fault_edge(mode, loc) or {}
    rate = float((edge.get("documented_rate") or {}).get("per_year") or 0.01)
    ty = kg.type_of(owner)
    counts = kg.g.nodes[ty].get("cause_counts", {}) if ty else {}
    total = sum(counts.values())
    boost = (1.0 + counts.get(mode, 0.0)) / (1.0 + total / max(1, len(counts) or 1))
    return max(rate * boost, 1e-6)


# ---------------------------------------------------------------------- candidate locations (backward walk)

def backward_candidates(obs: Observations, prop: Propagator, max_elements: int = 400) -> list[str]:
    kg = prop.kg
    support = Counter()
    for el, n in obs.symptom_elements().items():
        ups: set = set()
        if kg.cls(el) == "ClientService":
            ups |= set(kg.service_closure(el, "both"))
        elif "|" in el:                                          # a channel at a ROADM degree
            oms, slot = el.split("|")
            for lp, d in kg.lightpaths_through(oms):
                if str(kg.g.nodes[lp].get("slot")) == slot:
                    path = prop.path(lp, d)
                    end = path.index(f"roadm.{kg.g.nodes[oms]['dst']}") if f"roadm.{kg.g.nodes[oms]['dst']}" in path else len(path)
                    ups |= set(path[:end + 1])
                    ups.add(f"{oms}|{slot}")
        elif kg.cls(el) == "ClientPort":
            for svc in kg.services_through(el):
                ups |= set(kg.service_closure(svc, "both"))
        else:
            for lp, d in kg.lightpaths_through(el):
                path = prop.path(lp, d)
                if el in path:
                    ups |= set(path[:path.index(el) + 1])
            ups.add(el)
        for u in ups:
            support[u] += n
        support[el] += n                       # the element where a symptom was seen is itself a suspect
    ranked = sorted(support, key=lambda e: (-support[e], e))   # deterministic order among equal support
    # keep every element with at least half the best support (ties are not cut arbitrarily), up to a cap
    top = support[ranked[0]] if ranked else 0
    keep = [e for e in ranked if support[e] >= 0.5 * top][:max_elements]
    # expand ROADMs into the WSS channels of symptomatic lightpaths and the input ports they arrive on, and switches
    # into cross-connects
    out = []
    sym_lps: set = set()
    for el in obs.symptom_elements():
        base = el.split("|")[0]
        if kg.cls(base) == "LineInterface":
            sym_lps |= {lp for lp, _ in kg.inn(base, "endsAt")}
        elif "|" in el and kg.cls(base) == "OMS":
            slot = el.split("|")[1]
            sym_lps |= {lp for lp, _ in kg.lightpaths_through(base) if str(kg.g.nodes[lp].get("slot")) == slot}
    hurt_paths = {p for s in obs.hurt for p in kg.service_paths(s).values() if p}
    for el in keep:
        c = kg.cls(el)
        if c == "ROADM":
            # the WSS channels of symptomatic lightpaths at this ROADM, and the WSS input ports they arrive on
            for lp, d in kg.lightpaths_through(el):
                if lp not in sym_lps:
                    continue
                path = prop.path(lp, d)
                if el in path:
                    i = path.index(el)
                    prev_oms = next((x for x in reversed(path[:i]) if kg.cls(x) == "OMS"), None)
                    if prev_oms:
                        out += wss_locations(prev_oms, kg.g.nodes[lp].get("slot"))
        elif c == "OTNSwitch":
            out.append(el)
            out += [f"{el}|{p}" for p, _ in kg.inn(el, "crossConnectedAt") if p in hurt_paths]
        elif c in ("FibreSpan", "Amplifier", "OMS", "LineInterface", "ClientPort"):
            out.append(el)
        elif "|" in el:
            out.append(el)
    return list(dict.fromkeys(out))


def _modes(prop: Propagator, loc: str) -> list[str]:
    """Documented modes of the location's equipment, at the location's grain (the grounding tool's rule)."""
    return [m for m, _ in prop.kg.fault_modes_for(prop._owner(loc)) if admits(prop.kg, loc, m)]


def _explains(P, obs: Observations, k) -> bool:
    """A brief fault explains excursions, counters and alarms, not lasting level changes."""
    if k not in P:
        return False
    if getattr(P, "transient", False) and obs.kind.get(k) in ("level", "lost", "restored"):
        return False
    return True


def score_prediction(P, obs: Observations) -> dict:
    expl = sum(1 for k in obs.O if _explains(P, obs, k))
    miss_req = sum(1 for k, s in P.items() if s == "required" and k not in obs.O and obs.examined(k))
    miss_exp = sum(1 for k, s in P.items() if s == "expected" and k not in obs.O and obs.examined(k))
    return {"explained": expl, "missing_required": miss_req, "missing_expected": miss_exp,
            "unexplained": len(obs.O) - expl, "score": expl - 1.0 * miss_req - 0.3 * miss_exp}


# ---------------------------------------------------------------------- the three methods

class AbductionWalk:
    name, version = "walk.abduction", "0.1"

    def __init__(self, kg):
        self.kg, self.prop = kg, Propagator(kg)
        self.scored: list[dict] = []

    def run(self, frame: ScenarioFrame) -> GeneratorPayload:
        obs = Observations(frame, self.kg)
        if not obs.O:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        locs = backward_candidates(obs, self.prop)
        rows = []
        for loc in locs:
            for m in _modes(self.prop, loc):
                P = self.prop.predict(m, loc)
                if not P:
                    continue
                sc = score_prediction(P, obs)
                if sc["explained"] == 0:
                    continue
                rows.append({"mode": m, "loc": loc, "P": P, **sc, "prior": _prior(self.kg, self.prop, m, loc)})
        self.scored = rows
        return _to_payload(rows, len(obs.O), "abduction")


def _to_payload(rows, n_obs, tag, top: int = 25) -> GeneratorPayload:
    if not rows:
        return GeneratorPayload(candidates=[], residual_mass=1.0)
    T = 1.0                       # one logit unit per observation of evidence
    pmax = max(r["prior"] for r in rows)
    smax = max(r["score"] for r in rows)
    for r in rows:
        r["logit"] = (r["score"] - smax) / T + 0.5 * math.log(r["prior"] / pmax)
    rows.sort(key=lambda r: (-r["logit"], r["mode"], r["loc"]))     # ties in a fixed order
    rows = rows[:top]
    z = sum(math.exp(r["logit"]) for r in rows)
    best = rows[0]
    residual = min(0.5, 0.05 + 0.4 * best["unexplained"] / max(1, n_obs))
    cands = [Candidate(hypothesis=single(r["mode"], r["loc"]), prior_mass=(1 - residual) * math.exp(r["logit"]) / z,
                       rationale=f"{tag}: explains {r['explained']}/{n_obs}; missing required {r['missing_required']}, "
                                 f"expected {r['missing_expected']}")
             for r in rows]
    return GeneratorPayload(candidates=cands, residual_mass=residual)


class RandomWalkRanking:
    name, version = "walk.pagerank", "0.1"

    def __init__(self, kg, alpha: float = 0.85):
        self.kg, self.prop, self.alpha = kg, Propagator(kg), alpha

    def run(self, frame: ScenarioFrame) -> GeneratorPayload:
        kg, prop = self.kg, self.prop
        obs = Observations(frame, kg)
        seeds = obs.symptom_elements()
        if not seeds:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        G = nx.DiGraph()
        for el in seeds:
            if kg.cls(el) == "ClientService":
                for x in kg.service_closure(el, "both"):
                    G.add_edge(el, x)
                continue
            base = el.split("|")[0]
            for lp, d in kg.lightpaths_through(base):
                path = prop.path(lp, d)
                for a, b in zip(path, path[1:]):
                    G.add_edge(b, a)                         # reversed signal flow: walk upstream
            if "|" in el:
                G.add_edge(el, f"roadm.{kg.g.nodes[base]['dst']}")
            G.add_node(el)
        pers = {n: float(seeds.get(n, 0)) for n in G}
        if sum(pers.values()) == 0:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        pr = nx.pagerank(G, alpha=self.alpha, personalization=pers)
        rows = []
        for el, p in sorted(pr.items(), key=lambda x: (-x[1], x[0]))[:60]:
            locs = [el]
            if kg.cls(el) == "ROADM":
                node = el.split(".", 1)[1]
                locs = [k for k in seeds if "|" in k and kg.g.nodes.get(k.split("|")[0], {}).get("dst") == node]
                # a channel monitor reads before the WSS of its own node: a channel short at the monitor of the OMS
                # leaving this node points to this node's WSS, on the OMS the channel arrived by
                for k in seeds:
                    base, _, slot = k.partition("|")
                    if not slot.isdigit() or kg.g.nodes.get(base, {}).get("src") != node:
                        continue
                    for lp, d in kg.lightpaths_through(base):
                        if str(kg.g.nodes[lp].get("slot")) != slot:
                            continue
                        path = prop.path(lp, d)
                        i = path.index(el) if el in path else -1
                        prev = next((x for x in reversed(path[:i]) if kg.cls(x) == "OMS"), None) if i > 0 else None
                        if prev:
                            locs.append(f"{prev}|{slot}")
                locs = list(dict.fromkeys(locs))
                locs += list(dict.fromkeys(f"{k.split('|')[0]}|{PORT}" for k in locs))   # the ports they arrive on
            for loc in locs:
                ms = _modes(prop, loc) if (kg.has(loc) or "|" in loc) else []
                priors = {m: _prior(kg, prop, m, loc) for m in ms}
                tot = sum(priors.values()) or 1.0
                for m in ms:
                    rows.append((m, loc, p * priors[m] / tot))
        if not rows:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        rows.sort(key=lambda r: (-r[2], r[0], r[1]))
        rows = rows[:25]
        z = sum(r[2] for r in rows)
        res = 0.1
        return GeneratorPayload(candidates=[Candidate(hypothesis=single(m, loc), prior_mass=(1 - res) * w / z,
                                                      rationale="pagerank") for m, loc, w in rows],
                                residual_mass=res)


class MinimumExplanation:
    name, version = "walk.setcover", "0.1"

    def __init__(self, kg, abduction: AbductionWalk):
        self.kg, self.abd = kg, abduction

    def run(self, frame: ScenarioFrame) -> GeneratorPayload:
        obs = Observations(frame, self.kg)
        rows = self.abd.scored if self.abd.scored else (self.abd.run(frame) and self.abd.scored)
        if not rows:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        pool = sorted(rows, key=lambda r: (-r["score"], r["mode"], r["loc"]))[:40]
        covered: set = set()
        picks = []
        while True:
            best, gain = None, 0.0
            for r in pool:
                if r in picks:
                    continue
                new = sum(1 for k in obs.O if _explains(r["P"], obs, k) and k not in covered)
                g = new - r["missing_required"] - 0.3 * r["missing_expected"] + 0.1 * math.log(r["prior"])
                if new > 0 and (best is None or g > gain):
                    best, gain = r, g
            if best is None:
                break
            picks.append(best)
            covered |= {k for k in obs.O if _explains(best["P"], obs, k)}
            if len(covered) >= 0.95 * len(obs.O) or len(picks) >= 3:
                break
        cands = []
        n = len(obs.O)
        first = picks[0]
        f1 = sum(1 for k in obs.O if _explains(first["P"], obs, k)) / n
        cands.append(Candidate(hypothesis=single(first["mode"], first["loc"]), prior_mass=0.6 * f1 + 0.1,
                               rationale=f"set cover: first pick explains {f1:.0%}"))
        if len(picks) > 1:
            comp = Hypothesis(id="+".join(cause_key(p["mode"], p["loc"]) for p in picks), kind=HypothesisKind.COMPOUND,
                              causes=[Cause(fault_mode=p["mode"], location=ElementLocation(element=p["loc"])) for p in picks])
            cands.append(Candidate(hypothesis=comp, prior_mass=0.6 * (1 - f1),
                                   rationale=f"set cover: {len(picks)} causes explain {len(covered) / n:.0%}"))
        rest = 1 - sum(c.prior_mass for c in cands)
        return GeneratorPayload(candidates=cands, residual_mass=max(0.0, rest))
