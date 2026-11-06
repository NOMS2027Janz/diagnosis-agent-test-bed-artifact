"""The winnowing loop, deterministic planner arm ("The Diagnosis Agent Test Bed" §8, §9.5, §9.6).

    frame -> generate (pooled lines) -> ledger -> graph consistency, twin
          -> repeat: advise -> check -> interpret -> update
                     return to generation when the ledger signals it (at most MAX_RETURNS times)
          -> end: commit, "I don't know" (no informative check left), or non-convergence (effort or steps spent)

Everything the loop knows comes from the substrate (graph, lake, the checks it asks for) and the documents. Each
pass, check and term is recorded, with the posterior after it, so that a case can be replayed and its passes,
triggers, checks and effort counted.

Generation. The pass-one lines are those of Experiment 1's pooled offline arm (abduction, set cover, alarm
correlation, PageRank, analogy, procedures) plus monitoring faults: for every instrument that changed with no alarm
on its element, or that the question names, the hypotheses that the instrument misreports or is frozen. A return to
generation reruns the abduction walk over a wider scope (twice the elements), with the hypotheses contradicted so
far excluded, forms compounds of the two leading single causes when they explain different symptoms, and adds the
set-cover compound; every term already in the ledger is then filled for the new hypotheses before the ledger may
commit.

Terms. Graph consistency over every hypothesis (tau 0.3) and the twin over the leading hypotheses (tau 0.7) share
the evidence of the data held, so their weights sum to one; each check's interpretation is a term of its own
(tau 1). The twin is fitted lazily: the leading TWIN_TOP hypotheses at the start and any hypothesis that later
enters contention.
"""
from __future__ import annotations

import math

import time
from typing import Optional

from ..ledger import NONE_KEY, Ledger
from ..schemas import Candidate, Cause, ElementLocation, GeneratorPayload, Hypothesis, HypothesisKind
from .advisor import Advisor, contenders_of
from .consistency import GraphConsistency
from .interpret import interpret
from .health import HealthLevels
from .twin import GraphTwin
from .walks import (AbductionWalk, MinimumExplanation, Observations, _explains, _modes, _prior, _to_payload,
                    backward_candidates, score_prediction, single)

MAX_RETURNS = 3
MAX_CHECKS = 12
UNEXPLAINED_MIN = 3
PEEL_UNEXPLAINED_MIN = 2
PEEL_STRONG_NATS = 8.0      # one reading missed by about four standard deviations also opens a peel
PEEL_ANCHORS = 3
PEEL_SINGLES = 6
NARROW = {"monitoring", "shared_risk", "trend", "liveness", "ports", "located", "logs"}
NARROW_WEIGHT = 0.25
TWIN_TOP = 12
TAU_C1, TAU_TWIN = 0.3, 0.7
TAU_TWIN_WITH_GNN, TAU_C1_WITH_GNN, TAU_GNN_WITH_TWIN = 0.5, 0.2, 0.3     # extension: the GNN shares the data held
DATA_HELD = "data_held"
DATA_TX = "data_held.transmission"      # the readings of optical power, OSNR, BER, DGD, polarisation, line counts
DATA_EQ = "data_held.equipment"         # the alarms raised and the services' states (down, switched)


def monitoring_payload(frame, kg) -> GeneratorPayload:
    """Faults of the monitoring: instruments whose readings changed with no alarm on their element, and the
    element the question names."""
    alarmed = {s.element for s in frame.symptoms if s.kind == "alarm"}
    els = []
    for s in frame.symptoms:
        if s.kind == "change" and s.element.split("|")[0] not in alarmed:
            els.append(s.element.split("|")[0])
    els += [a for a in frame.anchors if kg.has(a) and kg.cls(a) in ("Amplifier", "LineInterface")]
    insts = []
    for el in dict.fromkeys(els):
        for i in kg.instruments_for_lake_element(el):
            if any(o in ("amp_input_power", "amp_output_power", "rx_power") for o in kg.g.nodes[i].get("observes", [])):
                insts.append(i)
    per_el: dict = {}                   # one hypothesis per element: its optical instruments report together
    for i in dict.fromkeys(insts):
        per_el.setdefault(kg.g.nodes[i].get("lake_element"), i)
    insts = list(per_el.values())[:6]
    if not insts:
        return GeneratorPayload(candidates=[], residual_mass=1.0)
    m = 0.5 / (2 * len(insts))
    cands = []
    for i in insts:
        for mode in ("INSTRUMENT_MISREPORT", "TELEMETRY_STALE"):
            cands.append(Candidate(hypothesis=single(mode, i), prior_mass=m, rationale="monitoring fault"))
    return GeneratorPayload(candidates=cands, residual_mass=0.5)


def shared_risk_payload(frame, kg) -> GeneratorPayload:
    """One event behind several lost lines: when lines leaving or entering the same site lost their input at once,
    the cut of every one of them near that site (a dig-up in a shared duct or trench). Supported when the plant
    records place those spans in a common duct; still proposed when they do not, since shared-risk records are
    incomplete (knowledge/practice/records.yaml)."""
    lost_oms = set()
    for s in frame.symptoms:
        if s.kind == "alarm" and s.observable in ("AMP_INPUT_LOS", "OMS_LOS_P"):
            el = s.element.split("|")[0]
            oms = kg.g.nodes.get(el, {}).get("oms") if kg.has(el) and kg.cls(el) == "Amplifier" else \
                (el if kg.has(el) and kg.cls(el) == "OMS" else None)
            if oms:
                lost_oms.add(oms)
    links: dict = {}
    for o in lost_oms:
        a = kg.g.nodes[o]
        links.setdefault(a.get("link"), set()).add(o)
    by_site: dict = {}
    for link, omss in links.items():
        for o in omss:
            a = kg.g.nodes[o]
            for site in (a["src"], a["dst"]):
                by_site.setdefault(site, {})[link] = o
    cands = []
    for site, lk in sorted(by_site.items()):
        if len(lk) < 2:
            continue
        spans = []
        for link, o in sorted(lk.items()):
            els = [e for e in kg.oms_elements(o) if kg.cls(e) == "FibreSpan"]
            if els:
                spans.append(els[0] if kg.g.nodes[o]["src"] == site else els[-1])
        if len(spans) < 2:
            continue
        ducts = [{d for d, _ in kg.out(sp, "inDuct")} for sp in spans]
        recorded = bool(set.intersection(*ducts)) if all(ducts) else False
        h = Hypothesis(kind=HypothesisKind.COMPOUND, causes=[
            Cause(fault_mode="FIBRE_CUT", location=ElementLocation(element=sp)) for sp in spans])
        cands.append(Candidate(hypothesis=h, prior_mass=0.4 if recorded else 0.2,
                               rationale=f"lines at {site} lost together" + ("; common duct recorded" if recorded else "")))
    if not cands:
        return GeneratorPayload(candidates=[], residual_mass=1.0)
    tot = sum(c.prior_mass for c in cands)
    if tot > 0.9:
        for c in cands:
            c.prior_mass *= 0.9 / tot
    return GeneratorPayload(candidates=cands, residual_mass=max(0.0, 1.0 - sum(c.prior_mass for c in cands)))


HEALTH_HIGH_PCT = 103.0


HEALTH_TREND_OBS = ("pump_current_pct", "laser_bias_pct")
MARGIN_LEVEL = __import__("os").environ.get("ADAGENT_MARGIN_LEVEL", "1") == "1"   # C4 (Chris, 26 Oct 15:48): margin levels against the twin
COARSE_COMMIT = __import__("os").environ.get("ADAGENT_COARSE", "1") == "1"   # C4 (Chris, 26 Oct 14:47): commitment at a coarser grain
FULL_COVER = __import__("os").environ.get("ADAGENT_FULL_COVER", "1") == "1"   # C4: "none of these" leads only after the twin has scored all of "these"
PEEL_MIN_LEAD = float(__import__("os").environ.get("ADAGENT_PEEL_MIN_LEAD", "0"))   # C4b guard on peeling: off in C4d (it cost
#   coincident-fault answers: when "none of these" leads, the named leader is weak and peeling is what finds the second cause)
COMPOUND_MIN_LEAD = float(__import__("os").environ.get("ADAGENT_COMPOUND_MIN_LEAD", "0.2"))   # C4d: pair the two leaders
#   only when the first holds at least this (an established cause); 0 = off


def trend_payload(frame, sub) -> GeneratorPayload:
    """Slow trends: the documented ageing (incipient) modes of the equipment the question names, and of any unit
    whose health telemetry (pump current, laser bias) reads above HEALTH_HIGH_PCT of nominal now. A change at an
    onset is what the framer looks for; a drift has no onset in the window, so it needs this reading of levels."""
    kg = sub.kg
    high = {}
    for ob in ("pump_current_pct", "laser_bias_pct"):
        for el in sub.elements_reporting(ob):
            x = sub.latest(ob, el)
            if x is not None and x.value > HEALTH_HIGH_PCT:
                high[el] = x.value
    changed_at_onset = {s.element.split("|")[0] for s in frame.symptoms}
    # the element the question names, unless it shows a change at an onset (then the question is not about a trend)
    els = [a for a in frame.anchors if kg.has(a) and a not in changed_at_onset] + \
        sorted(high, key=lambda e: -high[e])[:5]
    cands = []
    reporting = {ob: sub.elements_reporting(ob) for ob in HEALTH_TREND_OBS}
    def judged_here(d, el):
        """C4 (amendment 8): an ageing mode seen only by a health stream is proposed only where that stream is
        reported; elsewhere nothing can judge the trend, and the hypothesis would score as well as anything can."""
        seen_by = set()
        for e in d.get("effects") or []:
            seen_by |= {o.split(".", 1)[1] for o, _ in kg.inn(f"effect.{e['effect']}", "sees")}
        health = seen_by & set(HEALTH_TREND_OBS)
        if not health or seen_by - health:
            return True                  # seen by other streams too (or by none): not restricted here
        return any(el in reporting[ob] for ob in health)
    for el in dict.fromkeys(els):
        ty = kg.type_of(el)
        if not ty:
            continue
        own = [m for m, d in kg.out(ty, "hasFaultMode") if d.get("nature") == "incipient" and judged_here(d, el)]
        borrowed = []
        if not own:                    # the documentation of another vendor's equipment of the same class
            cls_ = kg.g.nodes[ty].get("element_class")
            for t2, a in kg.g.nodes(data=True):
                if a.get("cls") == "EquipmentType" and t2 != ty and a.get("element_class") == cls_:
                    borrowed += [m for m, d in kg.out(t2, "hasFaultMode")
                                 if d.get("nature") == "incipient" and judged_here(d, el)]
        w0 = 2.0 if el in high else 1.0
        cands += [(m, el, w0) for m in own] + [(m, el, 0.5 * w0) for m in dict.fromkeys(borrowed)]
    if not cands:
        return GeneratorPayload(candidates=[], residual_mass=1.0)
    tot = sum(w for *_, w in cands)
    return GeneratorPayload(candidates=[Candidate(hypothesis=single(m, el), prior_mass=0.7 * w / tot,
                                                  rationale="documented ageing mode" + (", health level high" if w > 1 else ""))
                                        for m, el, w in cands], residual_mass=0.3)


def canonical(payload: GeneratorPayload, kg) -> GeneratorPayload:
    """A cut takes both fibres of a cable section (the plant documentation's extends_to): name it once, by the
    span of the section whose identifier sorts first, and merge the two proposals' mass."""
    def canon_loc(mode, loc):
        if mode != "FIBRE_CUT" or not kg.has(loc) or kg.cls(loc) != "FibreSpan":
            return loc
        a = kg.g.nodes[loc]
        o = kg.g.nodes[a["oms"]]
        rev = f"oms.{o['dst']}-{o['src']}"
        if not kg.has(rev):
            return loc
        rs = [e for e in kg.oms_elements(rev) if kg.cls(e) == "FibreSpan"]
        mine = [e for e in kg.oms_elements(a["oms"]) if kg.cls(e) == "FibreSpan"]
        twin = rs[len(rs) - 1 - mine.index(loc)] if len(rs) == len(mine) else None
        return min(loc, twin) if twin else loc
    merged: dict = {}
    for c in payload.candidates:
        causes = []
        for cs in c.hypothesis.causes:
            if cs.location.kind == "element":
                cs = cs.model_copy(update={"location": ElementLocation(
                    element=canon_loc(cs.fault_mode, cs.location.element))})
            causes.append(cs)
        uniq = list({x.key(): x for x in causes}.values())
        kind = c.hypothesis.kind
        if kind == HypothesisKind.COMPOUND and len(uniq) == 1:
            kind = HypothesisKind.SINGLE
        h = c.hypothesis.model_copy(update={"causes": uniq, "kind": kind})
        k = h.key()
        if k in merged:
            merged[k] = merged[k].model_copy(update={"prior_mass": merged[k].prior_mass + c.prior_mass})
        else:
            merged[k] = c.model_copy(update={"hypothesis": h})
    return GeneratorPayload(candidates=list(merged.values()), residual_mass=payload.residual_mass)


def can_decide(ledger, groups: dict) -> dict:
    """Recorded, not acted on: could any answer of the check about to be made bring the ledger to a commitment?
    For each answer group the check's predicted answers form: hypotheses predicting it keep their probability;
    those predicting another answer are charged the full likelihood cap; "none of these" is charged as the check's
    reading would charge it; hypotheses with no predicted answer are not charged. "one": some answer commits now;
    "two": some answer leaves a state from which one more check as strong as this one against "none of these",
    confirming its leader against everything else, would commit. p_one, p_two: the probability, as the agent holds it
    now, of an answer that does so (the mass of the hypotheses predicting it; "none of these" counts only when it has
    an answer group of its own). Coverage is ignored (an optimistic bound)."""
    cfg = ledger.cfg
    lp = ledger.log_posterior_unnormalised()
    cap = cfg.likelihood_cap_nats
    known = {k for m in groups["members"] for k in m}

    def commit(lpx):
        fin = [v for v in lpx.values() if v != -math.inf]
        if not fin:
            return False, 0.0, 1.0
        z = max(fin) + math.log(sum(math.exp(v - max(fin)) for v in fin))
        post = {k: math.exp(v - z) for k, v in lpx.items() if v != -math.inf}
        lead = max((k for k in post if k != NONE_KEY), key=lambda k: post[k], default=None)
        pl, pn = (post[lead] if lead else 0.0), post.get(NONE_KEY, 0.0)
        return (lead is not None and pl >= cfg.theta_commit and pn < cfg.theta_open), pl, pn

    one = two = False
    best = 0.0
    strength = max(groups["none_cost"], default=0.0)
    post0 = ledger.posterior()
    p_one = p_two = 0.0
    for members, nc in zip(groups["members"], groups["none_cost"]):
        mem = set(members)
        lpx = {}
        for k, v in lp.items():
            if k == NONE_KEY:
                lpx[k] = v - (0.0 if k in mem else nc)
            elif k in known and k not in mem:
                lpx[k] = v - cap
            else:
                lpx[k] = v
        ok, pl, pn = commit(lpx)
        best = max(best, pl)
        one = one or ok
        mass = sum(post0.get(k, 0.0) for k in mem)        # the probability, as the agent holds it, of this answer
        p_one += mass if ok else 0.0
        two_here = ok
        if not ok:
            # one further check of the same strength confirming this answer's leader: the other named hypotheses
            # charged the cap, "none of these" charged the most this check charges it under any answer
            fin = {k: v for k, v in lpx.items() if v != -math.inf and k != NONE_KEY}
            if fin:
                lead = max(fin, key=lambda k: fin[k])
                lpy = {k: (v if k == lead else v - (strength if k == NONE_KEY else cap)) for k, v in lpx.items()}
                two_here = commit(lpy)[0]
        two = two or two_here
        p_two += mass if two_here else 0.0
    return {"one": one, "two": two, "p_one": round(p_one, 4), "p_two": round(p_two, 4),
            "best_lead_after": round(best, 4), "none_charge_max": round(strength, 3)}


class AdvisorPlanner:
    """The deterministic reference planner: the advisor's recommendation, or stop when it has none."""
    name = "advisor"

    def __init__(self):
        self.advisor: Optional[Advisor] = None

    def bind(self, loop) -> None:
        self.advisor = Advisor(loop.kg, loop.src.checks, loop.twin, loop.seed, decide_gate=loop.decide_gate)

    def next(self, loop, budget_left: float, done: list, visited: set) -> dict:
        rec = self.advisor.recommend(loop.ledger, budget_left, done, visited)
        if rec is None:
            return {"action": "stop", "reason": "no check within the effort left is expected to separate the contenders"}
        return {"action": "check", **rec}

    def trace(self) -> list:
        return self.advisor.trace[-3:] if self.advisor else []


REPAIRING_CHECKS = ("inspect_clean", "swap_unit")     # checks that repair what they find (world/checks.py)


class WinnowingLoop:
    name, version = "loop", "0.5"   # 0.5 (amendment 4): wider frame on returns; residual counts alarms; twin clock
    # 0.4: GNN term and twin_score flag
    # 0.3   # 0.3: a return asked for when none is left is refused, the planner asked again

    def __init__(self, substrate, sources, frame, lines: dict, budget_h: float = 8.0, case_seed: int = 0,
                 use_twin: bool = True, use_c1: bool = True, returns: bool = True, planner=None,
                 use_gnn: bool = False, twin_score: bool = True, question=None, twin_calc_err_db: float = 0.0,
                 twin_equipment: bool = False, twin_eq_matched: bool = False, peel: bool = False,
                 transient: bool = False, decide_gate=None, multifault: bool = False, ladder=()):
        self.sub, self.src, self.frame = substrate, sources, frame
        # the capability ladder (tools/ladder.py): the rungs whose observables the agent reads; none by default
        self.ladder = frozenset(ladder or ())
        self.liveness = self.ports = self.located = self.logs = None
        if "L1" in self.ladder:
            from .ladder import StreamLiveness
            self.liveness = StreamLiveness(substrate)
        if "L3" in self.ladder:
            from .ladder import PortMonitor
            self.ports = PortMonitor(substrate, frame.onset_estimate)
        if "L4" in self.ladder:
            from .ladder import LocatedEvents
            self.located = LocatedEvents(substrate, frame.onset_estimate)
        if "L5" in self.ladder:
            from .ladder import EventLogs
            self.logs = EventLogs(substrate, frame.onset_estimate)
        self.spectral = None
        if {"S", "R"} & self.ladder:
            from .ladder import SpectralLocator
            self.spectral = SpectralLocator(substrate, frame.onset_estimate, self.ladder)
        self.decide_gate = decide_gate      # the "can this decide?" step (Advisor.decide_gate); None: not taken
        # twin_eq_matched: the equipment evidence is judged by a twin that simulates equipment behaviour, in the same
        # term, with the same charges and the same value for "none of these" as the documented equipment term;
        # transmission and the reading of checks are as in the main arms. Only the precision of the equipment
        # predictions differs from them (an experimental arm).
        self.twin_eq_matched = bool(twin_eq_matched) and not twin_equipment
        self.eqtwin: Optional[GraphTwin] = None
        self.question = question            # with it, a return to generation works from a wider frame
        # peel: a return to generation also searches where the leader's twin residual points (the readings it leaves
        # unexplained, anywhere in the network), pairs the leader with what is proposed there, and the twin fits each
        # later cause of a compound given the earlier ones (an experimental arm)
        self.peel = bool(peel)
        # transient: the transient event recogniser (a specialist GNN) as a pass-one generation line
        self.transient = None
        if transient:
            from .transient import TransientRecogniser
            self.transient = TransientRecogniser(substrate, sources, frame.onset_estimate)
        # multifault: multiple-fault attribution on the twin's residual (a specialist GNN) as a generation line on
        # return passes where the leader leaves readings unexplained (an experimental arm; off by default)
        self.multifault = bool(multifault)
        self._mf_params = None
        self._mf = None
        if self.multifault:
            from .multifault import load as _mf_load
            self._mf_params = _mf_load()
        from . import interpret as _interp
        _interp.COMPOUND_READING = self.peel     # a compound is read by a check's documented meaning, cause by cause
        self.unexplained_min = PEEL_UNEXPLAINED_MIN if self.peel else UNEXPLAINED_MIN
        self.twin_calc_err_db, self.twin_equipment = twin_calc_err_db, twin_equipment
        self.kg = substrate.kg
        self.lines = lines                  # name -> generator with .run(frame)
        self.budget_h, self.seed = budget_h, case_seed
        self.use_twin, self.use_c1, self.allow_returns = use_twin, use_c1, returns
        self.returns_left, self.return_refused = (MAX_RETURNS if returns else 0), False
        self.use_gnn, self.twin_score = use_gnn, twin_score
        # shares of the data held (ledger: consumers of one evidence item sum to at most one)
        if use_gnn and use_twin and twin_score:
            self.tau = {"twin": TAU_TWIN_WITH_GNN, "c1": TAU_C1_WITH_GNN, "gnn": TAU_GNN_WITH_TWIN}
        elif use_gnn:
            self.tau = {"twin": TAU_TWIN, "c1": TAU_C1, "gnn": TAU_TWIN}       # the GNN takes the twin's share
        else:
            self.tau = {"twin": TAU_TWIN, "c1": TAU_C1, "gnn": 0.0}
        self.gnn = None
        self.ledger = Ledger()
        self.record: dict = {"passes": [], "checks": [], "terms": [], "trajectory": [], "timing": {}}
        self.excluded: set = set()
        self.twin: Optional[GraphTwin] = None
        self.c1: Optional[GraphConsistency] = None
        self.twin_covered: set = set()
        self.health = None
        self.margin = None
        self.check_terms: list = []          # (term id, check, params, result)
        self.planner = planner or AdvisorPlanner()

    # ------------------------------------------------------------------ generation
    def _generate(self, pass_no: int, trigger: str) -> list[str]:
        t0 = time.time()
        new = []
        before = set(self.ledger.active_keys())
        def add(name, p):
            """A line with no candidates abstains: it is recorded, but does not vote for 'none of these' in the
            mixture (a line that saw nothing to work from says nothing about whether the cause was considered)."""
            if not p.candidates:
                self.record.setdefault("abstained", []).append(f"{name}@pass{pass_no}")
                return
            narrow = name in NARROW or pass_no > 1
            if narrow:
                # a specialised line speaks only of its own kind of cause, and a return pass adds to what the first
                # pass proposed: their candidates join the mixture at a small weight and their residual is not a
                # vote for "none of these" (otherwise every return would raise the mass that triggered it)
                p = GeneratorPayload(candidates=p.candidates, residual_mass=0.0) if sum(
                    c.prior_mass for c in p.candidates) > 0 else p
                tot = sum(c.prior_mass for c in p.candidates)
                p = GeneratorPayload(candidates=[c.model_copy(update={"prior_mass": c.prior_mass / tot})
                                                 for c in p.candidates], residual_mass=0.0)
            self.ledger.add_generator(name, canonical(p, self.kg), NARROW_WEIGHT if narrow else 1.0)
        if pass_no == 1:
            for name, g in self.lines.items():
                add(name, g.run(self.frame))
            add("monitoring", monitoring_payload(self.frame, self.kg))
            add("shared_risk", shared_risk_payload(self.frame, self.kg))
            add("trend", trend_payload(self.frame, self.sub))
            if self.liveness is not None:
                add("liveness", self.liveness.payload())
                self.record["liveness"] = {u: round(t, 1) for u, t in sorted(self.liveness.stopped.items())}
            for name, tool in (("ports", self.ports), ("located", self.located), ("logs", self.logs)):
                if tool is not None:
                    add(name, tool.payload())
                    self.record.setdefault("ladder_entries", {})[name] = [list(e) for e in tool.entries]
            if self.spectral is not None:
                add("spectral", self.spectral.payload())
                self.record.setdefault("ladder_entries", {})["spectral"] = [list(e) for e in self.spectral.entries]
            if self.transient is not None:
                add("transient", self.transient.run(self.frame))
                self.record["transient"] = self.transient.last
        else:
            abd = AbductionWalk(self.kg)
            frame = self.frame
            if self.question is not None:
                # a return looks further: a frame widened by 2 per pass (scope, paths followed, neighbours of the
                # affected elements), so symptoms outside the first frame can be explained
                from .frame import DeterministicFramer
                frame = DeterministicFramer(self.sub, widen=2 ** (pass_no - 1)).build(self.question)
                self.record.setdefault("frames", []).append({"pass": pass_no, "scope": len(frame.scope),
                                                             "symptoms": len(frame.symptoms)})
            obs = Observations(frame, self.kg)
            locs = backward_candidates(obs, abd.prop, max_elements=400 * 2 ** (pass_no - 1))
            rows = []
            for loc in locs:
                for m in _modes(abd.prop, loc):
                    if f"{m}@{loc}" in self.excluded:
                        continue
                    P = abd.prop.predict(m, loc)
                    if not P:
                        continue
                    sc = score_prediction(P, obs)
                    if sc["explained"] == 0:
                        continue
                    rows.append({"mode": m, "loc": loc, "P": P, **sc, "prior": _prior(self.kg, abd.prop, m, loc)})
            abd.scored = rows
            add(f"abduction.pass{pass_no}", _to_payload(rows, len(obs.O), "abduction", top=40))
            add(f"setcover.pass{pass_no}", MinimumExplanation(self.kg, abd).run(frame))
            if self.use_c1 and frame is not self.frame:
                # graph consistency judges the new candidates against the same widened frame they were drawn from
                self.c1 = GraphConsistency(self.kg, frame)
                self._c1_refresh = True                  # every hypothesis is judged again on the wider frame
            if self.peel and self.twin is not None:
                pp = self._peel(pass_no, abd)
                if pp is not None:
                    add(f"peel.pass{pass_no}", pp)
            if self.multifault and self.twin is not None:
                mp = self._multifault(pass_no)
                if mp is not None:
                    add(f"multifault.pass{pass_no}", mp)
            comp = self._compound(obs)
            if comp is not None:
                add(f"compound.pass{pass_no}", GeneratorPayload(
                    candidates=[Candidate(hypothesis=comp, prior_mass=0.3, rationale="two leading causes")],
                    residual_mass=0.7))
        new = [k for k in self.ledger.active_keys() if k not in before]
        self.record["passes"].append({"pass": pass_no, "trigger": trigger, "new_hypotheses": len(new),
                                      "seconds": round(time.time() - t0, 2)})
        return new

    def _peel(self, pass_no: int, abd) -> Optional[GeneratorPayload]:
        """Peeling: fit the leader in the twin, take the readings it leaves unexplained, build a frame around the
        instruments that show them (wherever they are in the network) and propose there; pair the leader with each
        cause proposed, so the twin fits the second cause to what the first leaves."""
        post = self.ledger.posterior()
        lead = next((k for k, _ in sorted(post.items(), key=lambda kv: -kv[1]) if k != NONE_KEY), None)
        if lead is None:
            return None
        if PEEL_MIN_LEAD and post.get(lead, 0.0) < PEEL_MIN_LEAD:
            # C4b: a leader holding less than this is not an established cause; pairing it with what it leaves
            # stacks causes on a guess (a weak leader the twin can now score, once every proposal is scored)
            return None
        h = self.ledger.hypothesis(lead)
        resid = self.twin.residual(h)
        self.record.setdefault("peel", []).append({"pass": pass_no, "leader": lead, "residual": len(resid),
                                                   "worst": [list(map(str, k)) + [round(c, 1)] for c, k in resid[:5]]})
        if not resid:
            return None
        def _bare(el):
            el = el.split("|")[0]
            return el.split(":", 1)[1] if ":" in el else el
        resid_els = {str(k[2] if k[0] == "pm" else k[1]) for _, k in resid}
        anchors = []
        for _, k in resid:
            el = k[2] if k[0] == "pm" else k[1]
            el = str(el).split("|")[0]
            el = el.split(":", 1)[1] if ":" in el else el
            if el in self.kg.g and el not in anchors:
                anchors.append(el)
            if len(anchors) >= PEEL_ANCHORS:
                break
        if not anchors:
            return None
        from types import SimpleNamespace

        from .frame import DeterministicFramer
        lead_causes = {(c.fault_mode, c.location.key()) for c in h.causes}
        rows = {}
        rep = float(self.question.reported_at_s) if self.question is not None else float(self.frame.onset_estimate)
        for el in anchors:
            try:
                fr = DeterministicFramer(self.sub).build(SimpleNamespace(kind="operations", text="residual",
                                                                           anchor=el, reported_at_s=rep))
            except Exception as e:
                self.record["peel"][-1].setdefault("errors", []).append(f"{el}: {type(e).__name__}: {e}"[:200])
                continue
            obs = Observations(fr, self.kg)
            # what the second cause must explain is the residual: keep only the observations at its instruments
            keep = {o for o in obs.O if str(o[-1]) in resid_els or str(o[-1]).split("|")[0] in resid_els
                    or _bare(str(o[-1])) in anchors}
            if not keep:
                continue
            obs.O = keep
            for loc in backward_candidates(obs, abd.prop, max_elements=400):
                for m in _modes(abd.prop, loc):
                    key = f"{m}@{loc}"
                    if key in self.excluded or key in rows:
                        continue
                    P = abd.prop.predict(m, loc)
                    if not P:
                        continue
                    sc = score_prediction(P, obs)
                    if sc["explained"] == 0:
                        continue
                    rows[key] = (sc["explained"], sc["score"], _prior(self.kg, abd.prop, m, loc), m, loc)
        best = sorted(rows.values(), key=lambda r: (-r[0], -r[1], -r[2]))
        self.record["peel"][-1]["proposed"] = [f"{r[3]}@{r[4]}" for r in best[:PEEL_SINGLES + 2]]
        cands = []
        for expl, score_, prior, m, loc in best:
            single_h = single(m, loc)
            if (m, single_h.causes[0].location.key()) in lead_causes:
                continue
            comp = Hypothesis(kind=HypothesisKind.COMPOUND, causes=list(h.causes) + list(single_h.causes))
            cands.append(Candidate(hypothesis=comp, prior_mass=1.0, rationale="leader plus a cause where its residual points"))
            if len(cands) >= PEEL_SINGLES:
                break
        if not cands:
            return None
        return GeneratorPayload(candidates=[c.model_copy(update={"prior_mass": 1.0 / len(cands)}) for c in cands],
                                residual_mass=0.0)

    def _multifault(self, pass_no: int) -> Optional[GeneratorPayload]:
        """Multiple-fault attribution: when the leader leaves readings unexplained by the twin, the specialist GNN
        reads the residual and proposes the leader paired with each of its top proposals."""
        post = self.ledger.posterior()
        lead = next((k for k, _ in sorted(post.items(), key=lambda kv: -kv[1]) if k != NONE_KEY), None)
        if lead is None:
            return None
        h = self.ledger.hypothesis(lead)
        entry = {"pass": pass_no, "leader": lead}
        self.record.setdefault("multifault", []).append(entry)
        if not self.twin.residual(h):
            entry["skipped"] = "the leader leaves no reading unexplained"
            return None
        if self._mf is None:
            from .multifault import MultiFaultAttribution
            self._mf = MultiFaultAttribution(self.sub, self.twin, params=self._mf_params)
        p = self._mf.run(h)
        entry.update(self._mf.last or {})
        return p if p.candidates else None

    def _compound(self, obs) -> Optional[Hypothesis]:
        post = self.ledger.posterior()
        singles = [k for k, _ in sorted(post.items(), key=lambda kv: -kv[1])
                   if k != NONE_KEY and self.ledger.hypothesis(k).kind == HypothesisKind.SINGLE][:6]
        if len(singles) < 2:
            return None
        if COMPOUND_MIN_LEAD and post.get(singles[0], 0.0) < COMPOUND_MIN_LEAD:
            return None                      # C4d: pair only an established leader
        prop = (self.c1.prop if self.c1 else AbductionWalk(self.kg).prop)

        def explained(k):
            c = self.ledger.hypothesis(k).causes[0]
            loc = c.location.element if c.location.kind == "element" else None
            if loc is None:
                return set()
            P = prop.predict(c.fault_mode, loc)
            return {o for o in obs.O if _explains(P, obs, o)}
        a = singles[0]
        ea = explained(a)
        for b in singles[1:]:
            eb = explained(b)
            if eb - ea and ea - eb:
                ha, hb = self.ledger.hypothesis(a), self.ledger.hypothesis(b)
                return Hypothesis(kind=HypothesisKind.COMPOUND, causes=list(ha.causes) + list(hb.causes))
        return None

    # ------------------------------------------------------------------ terms
    def _hyps(self, keys) -> list[Hypothesis]:
        return [self.ledger.hypothesis(k) for k in keys if k != NONE_KEY]

    def _apply_data_terms(self, keys: list[str], first: bool) -> None:
        hyps = self._hyps(keys)
        deferred = None
        if self.use_c1:
            c1_hyps = hyps
            if getattr(self, "_c1_refresh", False) and not first:
                c1_hyps = self._hyps([k for k in self.ledger.active_keys() if k != NONE_KEY])
                self._c1_refresh = False
            pl = self.c1.likelihood(c1_hyps)
            if first:
                self.ledger.add_likelihood("c1", "consistency.graph", pl, [DATA_TX, DATA_EQ], self.tau["c1"])
            else:
                self.ledger.fill_term("c1", pl)
            for h in c1_hyps:
                v = next((x for x in reversed(self.c1.log) if x.hypothesis == h.key()), None)
                if v is not None and v.verdict == "contradicted":
                    self.excluded.add(_cause_key(h))
            if not self.twin_equipment:
                # the equipment behaviour a transmission twin cannot predict, judged from the documentation; it
                # takes the share of the equipment evidence that graph consistency leaves
                pe = self.c1.equipment_likelihood(c1_hyps)
                if self.eqtwin is not None:
                    deferred = (pe, c1_hyps)      # simulated after the twin has fitted the leading hypotheses
                elif first:
                    self.ledger.add_likelihood("equip", "consistency.equipment", pe, [DATA_EQ], 1.0 - self.tau["c1"])
                else:
                    self.ledger.fill_term("equip", pe)
        for tid, tool, ev in (("liveness", self.liveness, "stream_counters"), ("ports", self.ports, "wss_port_readings"),
                              ("located", self.located, "span_captures"), ("logs", self.logs, "event_logs")):
            if tool is None:
                continue
            pl = tool.likelihood(hyps)
            if first:
                self.ledger.add_likelihood(tid, tool.name, pl, [ev], 1.0)
            else:
                self.ledger.fill_term(tid, pl)
        if self.health is not None:
            pl = self.health.likelihood(hyps)
            if first:
                self.ledger.add_likelihood("health", "health.levels", pl, ["health_levels"], 1.0)
            else:
                self.ledger.fill_term("health", pl)
        if self.margin is not None:
            pl = self.margin.likelihood(hyps)
            if first:
                self.ledger.add_likelihood("margin", "margin.levels", pl, ["margin_levels"], 1.0)
            else:
                self.ledger.fill_term("margin", pl)
        if self.use_twin:
            prior = self.ledger.priors()
            top = sorted((k for k in keys if k != NONE_KEY), key=lambda k: -prior.get(k, 0.0))[:TWIN_TOP]
            self._twin_fill(top, first)
        if deferred is not None:
            pe, c1_hyps = deferred
            pe = self._eq_simulated(pe, [h for h in c1_hyps if h.key() in self.twin_covered])
            if first:
                self.ledger.add_likelihood("equip", "consistency.equipment", pe, [DATA_EQ], 1.0 - self.tau["c1"])
            else:
                self.ledger.fill_term("equip", pe)
        if self.gnn is not None:
            self._gnn_term()
        for tid, check, params, res in self.check_terms:     # earlier checks speak to new hypotheses too
            pl, _ = interpret(check, params, res, hyps, self.twin, self.seed, self.twin_covered,
                                     kg=self.kg)
            if pl is not None:
                self.ledger.fill_term(tid, pl)

    def _gnn_term(self) -> None:
        """The GNN's likelihood term over every candidate it can score; re-issued after a return so that it covers
        the new candidates and its "none of these" reflects what is still uncovered (one term, on the data held)."""
        pl = self.gnn.likelihood(self._hyps(self.ledger.active_keys()))
        self.ledger._lik = [t for t in self.ledger._lik if t.term_id != "gnn"]
        for terms in self.ledger._evidence_terms.values():     # registered once, however often re-issued
            while "gnn" in terms:
                terms.remove("gnn")
        if pl is not None:
            self.ledger.add_likelihood("gnn", "gnn.graph", pl, [DATA_TX], self.tau["gnn"])

    def _twin_fill(self, keys: list[str], first: bool = False) -> None:
        keys = [k for k in keys if k not in self.twin_covered and k != NONE_KEY]
        if not keys and not first:
            return
        if not self.twin_score:
            # the twin predicts check answers and runs the residual test, but does not score the data held
            self.twin_covered |= set(keys)
            self._reread_checks(keys)
            return
        pl = self.twin.likelihood(self._hyps(keys))
        self.twin_covered |= set(keys)
        if first:
            self.ledger.add_likelihood("twin", "twin.graph", pl, [DATA_TX, DATA_EQ] if self.twin.equipment else [DATA_TX],
                                       self.tau["twin"])
        else:
            self.ledger.fill_term("twin", pl)
            if self.eqtwin is not None and self.c1 is not None and any(t.term_id == "equip" for t in self.ledger._lik):
                hk = self._hyps(keys)
                self.ledger.fill_term("equip", self._eq_simulated(self.c1.equipment_likelihood(hk), hk))
        self._reread_checks(keys)

    def _eq_simulated(self, pe, hyps):
        """The documented equipment term with the documentation's predictions replaced, for every hypothesis the
        transmission twin can fit, by those of a twin that simulates equipment behaviour: the same charge per alarm,
        protection switch or client outage observed and not produced or produced and not observed, the same cap and
        the same value for "none of these". Hypotheses the twin cannot fit keep the documented value."""
        from .consistency import EQUIP_NATS, Z_CAP_NATS
        from ..schemas import LikelihoodPayload, LikelihoodValue
        ob = self.eqtwin.obs
        def items(f):
            down = {e for (o, e, t), v in f.pm.items() if o == "client_unavailable_seconds_pm15" and v > 0}
            return set(f.alarms) | {("switch", e) for e in f.switches} | {("down", e) for e in down}
        seen = items(ob)
        vals = dict(pe.values)
        for h in hyps:
            fit = self.twin.fit(h)
            if "declined" in fit:
                continue
            pred = items(self.eqtwin.features(fit["events"]))
            n = len(seen - pred) + len(pred - seen)
            vals[h.key()] = LikelihoodValue(log_likelihood=-min(EQUIP_NATS * n, Z_CAP_NATS))
        return LikelihoodPayload(values=vals, background_log_likelihood=pe.background_log_likelihood)

    def _reread_checks(self, keys: list[str]) -> None:
        """Checks already made are read again for hypotheses the twin has just come to cover, so that every check
        speaks of every hypothesis the twin can predict it for."""
        if not keys or not self.check_terms:
            return
        hyps = self._hyps(keys)
        for tid, check, params, res in self.check_terms:
            pl, _ = interpret(check, params, res, hyps, self.twin, self.seed, self.twin_covered,
                                     kg=self.kg)
            if pl is not None:
                self.ledger.fill_term(tid, pl)

    def _contenders_covered(self) -> None:
        """Every hypothesis in contention must carry the twin's term before the ledger may commit."""
        if not self.use_twin:
            return
        post = self.ledger.posterior()
        lead = [k for k, _ in sorted(post.items(), key=lambda kv: -kv[1]) if k != NONE_KEY][:6]
        # and the planner's contenders: the twin's term, its predictions of checks and the interpretation of their
        # answers then cover the same hypotheses
        lead += [k for k, _ in contenders_of(self.ledger) if k not in lead]
        if FULL_COVER and post and max(post, key=post.get) == NONE_KEY:
            # "none of these" leads: before the agent may say that no proposed cause explains the data, the twin
            # scores every proposed cause it has not yet scored (lazy fitting otherwise leaves a cause proposed with
            # a small prior, such as a monitoring fault, untested while the agent concludes that none fits)
            lead += [k for k in self.ledger.active_keys() if k != NONE_KEY and k not in self.twin_covered
                     and k not in lead]
        self._twin_fill(lead)

    def _unexplained(self, key: str) -> list:
        """Changes the leader does not account for, when at least UNEXPLAINED_MIN of them: evidence of a further
        cause (coincident faults). Transmission: changed readings the leader's twin prediction misses by more than
        three standard deviations. Equipment behaviour: alarms and service effects (down, switched) in the frame
        that the documented propagation of the leader's causes does not produce (or, for a twin that models
        equipment, that the twin does not predict)."""
        h = self.ledger.hypothesis(key)
        left, costs = [], {}
        fit = self.twin.fit(h) if self.use_twin else {"declined": "no twin"}
        if "declined" not in fit:
            costs = self.twin.costs(self.twin.features(fit["events"]))
            left = [k for k in self.twin.changed()
                    if costs.get(k, 0.0) > 4.5 or (k[0] in ("alarm", "switch") and costs.get(k, 0.0) > 0.0)]
        if not (self.twin is not None and self.twin.equipment):
            c1 = self.c1 or GraphConsistency(self.kg, self.frame)
            joint = []
            for c in h.causes:
                if c.fault_mode in ("INSTRUMENT_MISREPORT", "TELEMETRY_STALE"):
                    continue                     # a fault of the monitoring raises no alarm and moves no service
                loc = c.location
                els = list(loc.elements) if loc.kind == "set" else [loc.element if loc.kind == "element" else loc.span]
                joint += [(c.fault_mode, el) for el in els]
            P = c1.prop.predict_joint(joint)     # the causes acting together (protection judged once)
            left += [k for k in sorted(c1.obs.O) if k[0] in ("alarm", "client") and not _explains(P, c1.obs, k)]
        if self.peel and left and any(costs.get(k, 0.0) > PEEL_STRONG_NATS for k in left):
            return left
        return left if len(left) >= self.unexplained_min else []

    def _snapshot(self, label: str) -> None:
        post = self.ledger.posterior()
        top = sorted(post.items(), key=lambda kv: -kv[1])[:6]
        self.record["trajectory"].append({"after": label, "top": [(k, round(p, 4)) for k, p in top],
                                          "none": round(post.get(NONE_KEY, 0.0), 4),
                                          "entropy_bits": round(self.ledger.entropy_bits(), 3)})

    # ------------------------------------------------------------------ the loop
    def run(self) -> dict:
        t0 = time.time()
        new = self._generate(1, "start")
        if self.use_c1:
            self.c1 = GraphConsistency(self.kg, self.frame)
        if self.use_twin:
            self.twin = GraphTwin(self.sub, self.src, self.frame.onset_estimate, calc_err_db=self.twin_calc_err_db,
                                  seed=self.seed, equipment=self.twin_equipment, ladder=self.ladder)
            self.twin.sequential = self.peel
            if self.twin_eq_matched:
                self.eqtwin = GraphTwin(self.sub, self.src, self.frame.onset_estimate,
                                        calc_err_db=self.twin_calc_err_db, seed=self.seed, equipment=True)
        self.health = HealthLevels(self.sub, (self.c1.prop if self.c1 else AbductionWalk(self.kg).prop))
        if MARGIN_LEVEL and self.twin is not None:
            from .margin import MarginLevels
            from .twin import BER_DECADES_PER_DB
            sa = (self.twin.practice.get("stated_accuracy") or {})
            self.margin = MarginLevels(self.twin, self.kg, self.health.prop, float(sa.get("margin_db_1sigma", 0.25)),
                                       BER_DECADES_PER_DB)
            self.record["margin_lost"] = sorted(self.margin.lost)
            if getattr(self.margin, "offset", 0.0):
                self.record["margin_offset_decades"] = round(self.margin.offset, 4)
        if self.use_gnn:
            from .gnn import GNNRanker
            self.gnn = GNNRanker(self.sub, self.src, self.frame.onset_estimate, frame=self.frame)
        self._apply_data_terms(self.ledger.active_keys(), first=True)
        self._contenders_covered()
        self._snapshot("pass 1")
        self.planner.bind(self)
        adv = getattr(self.planner, "advisor", None)
        if "L6" in self.ladder and adv is not None:
            # the cross-check joins the advisor's catalogue, at the units of the contenders it can speak of
            from .ladder import xcheck_catalogue, xcheck_for
            c = xcheck_catalogue()
            adv.minutes[c["id"]], adv.cls[c["id"]], adv.impact[c["id"]] = c["minutes"], c["class"], False
            adv.extra_checks = xcheck_for
        spent, visited, done = 0.0, set(), []
        returns, outcome = 0, None
        while True:
            self._contenders_covered()
            dec = self.ledger.decision()
            if dec["outcome"] == "commit":
                left = self._unexplained(dec["hypothesis"])
                if (left and self.allow_returns and returns < MAX_RETURNS
                        and dec["hypothesis"] not in self.record.setdefault("residual_checked", [])):
                    # the leader leaves changes unexplained: look for a further cause before committing
                    self.record["residual_checked"].append(dec["hypothesis"])
                    returns += 1
                    new = self._generate(returns + 1, f"unexplained changes under the leader ({len(left)})")
                    self._apply_data_terms(new, first=False)
                    self._snapshot(f"pass {returns + 1}")
                    continue
                outcome = "commit"
                break
            sig = self.ledger.signals()
            if self.allow_returns and returns < MAX_RETURNS and (sig["regenerate"] or sig["misfit"]):
                returns += 1
                trig = (sig["regenerate_reason"].strip() or "") + (" misfit" if sig["misfit"] else "")
                new = self._generate(returns + 1, trig.strip())
                self._apply_data_terms(new, first=False)
                self._snapshot(f"pass {returns + 1}")
                continue
            if len(done) >= MAX_CHECKS:
                outcome = "non_convergence"
                break
            self.returns_left = (MAX_RETURNS - returns) if self.allow_returns else 0
            rec = self.planner.next(self, self.budget_h - spent, done, visited)
            if rec["action"] == "return" and self.returns_left <= 0:
                # a return asked for when none is left is refused and the planner asked again; it is not a stop
                self.record.setdefault("plans", []).append({**{k: v for k, v in rec.items() if k != "groups"},
                                                            "refused": "no returns to generation left"})
                self.return_refused = True
                rec = self.planner.next(self, self.budget_h - spent, done, visited)
                self.return_refused = False
            self.record.setdefault("plans", []).append({k: v for k, v in rec.items() if k != "groups"})
            if rec["action"] == "return" and self.allow_returns and returns < MAX_RETURNS:
                returns += 1
                new = self._generate(returns + 1, "planner: " + rec.get("reason", "")[:120])
                self._apply_data_terms(new, first=False)
                self._snapshot(f"pass {returns + 1}")
                continue
            if rec["action"] != "check":
                outcome = "no_informative_check" if spent < self.budget_h - 0.25 else "non_convergence"
                self.record["stop_reason"] = rec.get("reason", "")
                adv = getattr(self.planner, "advisor", None)
                if adv is not None and getattr(adv, "last_refused", None):
                    self.record["stop_reason"] = ("can-decide: no check left that could lead to a decision; "
                                                  "the costly checks that remain are refused")
                break
            lg = getattr(getattr(self.planner, "advisor", None), "last_groups", None)
            if lg is not None:
                self.record.setdefault("decide", []).append(
                    {"check": rec["check"], "params": rec["params"], "effort_h_est": rec.get("effort_h"),
                     **can_decide(self.ledger, lg)})
            res = self.sub.run_check(rec["check"], rec["params"])
            if self.twin is not None and res.get("t") is not None and res.get("status") != "unavailable":
                self.twin.at_s = float(res["t"])         # the clock moves on as checks complete
                if res.get("status") == "done" and rec["check"] in REPAIRING_CHECKS:
                    self.twin.repair_checks.append((rec["check"], dict(rec["params"]), float(res["t"])))
            # effort as incurred: time to result; travel is counted by the harness from the sites visited
            spent += res.get("minutes", 0) / 60.0
            for s in res.get("dispatch_sites") or []:
                if s not in visited:
                    visited.add(s)
                    spent += 2.0                       # the agent's estimate until the harness counts actual travel
            entry = {"check": rec["check"], "params": rec["params"], "status": res.get("status"),
                     "value": res.get("value"), "repaired": res.get("repaired"), "dispatch_sites": res.get("dispatch_sites"),
                     "predicted_gain_bits": rec.get("gain_bits"), "effort_h_est": rec.get("effort_h"),
                     "planner_reason": rec.get("reason")}
            done.append(entry)
            tid = f"check{len(done)}"
            hyps = self._hyps(self.ledger.active_keys())
            pl, verdicts = interpret(rec["check"], rec["params"], res, hyps, self.twin, self.seed, self.twin_covered,
                                     kg=self.kg)
            entry["verdicts"] = {k: v for k, v in verdicts.items() if v != "unresolved"}
            if pl is not None:
                self.ledger.add_likelihood(tid, f"check.{rec['check']}", pl, [tid], 1.0)
                self.check_terms.append((tid, rec["check"], rec["params"], res))
                for k, v in verdicts.items():
                    if v == "contradicted":
                        self.excluded.add(_cause_key(self.ledger.hypothesis(k)))
            self.record["checks"].append(entry)
            self._snapshot(tid)
            if spent >= self.budget_h:
                outcome = "non_convergence"
                break
        self._snapshot("final")
        dec = self.ledger.decision()
        if COARSE_COMMIT and dec.get("outcome") == "set":
            from .grain import coarse_commit
            cc = coarse_commit(self.ledger, self.kg, self.ledger.cfg.theta_commit, self.ledger.cfg.theta_open)
            if cc is not None:
                dec = {**dec, "coarse": cc}
        self.record.update({"outcome": outcome, "decision": dec, "returns": returns, "effort_h_est": round(spent, 2),
                            "sites_visited": sorted(visited), "planner": self.planner.name,
                            "planner_trace": self.planner.trace(),
                            "twin_cost": self.twin.cost if self.twin else None,
                            "decide_refused": list(getattr(getattr(self.planner, "advisor", None), "refused", []) or []),
                            "c1": [{"h": v.hypothesis, "verdict": v.verdict,
                                    "rules": [(r.rule, r.verdict, r.reason) for r in v.rules]}
                                   for v in (self.c1.log if self.c1 else [])
                                   if v.hypothesis in {k for k, _ in self.record["trajectory"][-1]["top"]}],
                            "seconds": round(time.time() - t0, 1)})
        return self.record


def _cause_key(h: Hypothesis) -> str:
    c = h.causes[0] if h.causes else None
    if c is None or len(h.causes) > 1:
        return h.key()
    loc = c.location.element if c.location.kind == "element" else c.location.key()
    return f"{c.fault_mode}@{loc}"
