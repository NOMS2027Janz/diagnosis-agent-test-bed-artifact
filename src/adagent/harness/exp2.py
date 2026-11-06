"""Experiment 2 jobs: one arm on one case, run and scored (spec/PROTOCOL_EXP2_WINNOWING.md).

A job builds its own copy of the case (checks can repair the world), runs the arm and scores it with the harness.
Arms (ARMS): the loop with the advisor (A), its controls and ablations, the LLM orchestrator (L), and Baseline B
with its procedures held by the tool (B) or read from the graph (B-graph). Scoring is the same for every arm: the
truth's equivalence classes on the data held (over every location the arm's hypotheses named), and the six
outcomes. Effort is counted as incurred, with actual travel.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Optional

from ..substrate.lifecycle import build_graph
from ..tools.analogy import AnalogySearch
from ..tools.baseline import ManualTroubleshooting
from ..tools.correlation import AlarmCorrelation
from ..tools.experience import ExperienceStore
from ..tools.frame import DeterministicFramer
from ..tools.grounding import GroundingTool
from ..tools.loop import WinnowingLoop
from ..tools.procedures import ProcedureLibrary, ProcedureTool
from ..tools.walks import AbductionWalk, MinimumExplanation, RandomWalkRanking
from ..world.checks import travel_hours
from .cases import context, sources
from .cases2 import make_case2
from .equivalence import _targets, cable_sections, equivalence_classes_v2, part_classes
from .separability import done_checks, separating_checks, unseparated

# Amendment 4: the operator's twin as good as practice. The world-records gap is calibrated (its spread centred on
# the datasheet values) at the scale GAP_REAL (margin error about 0.16 dB rms), and the twin adds its own calculation error CALC_REAL
# (dB, 1 sigma),
# together about 0.25 dB (1 sigma) on predicted margin (measured: scripts/twin_fidelity.py). Fidelity curve: a perfect
# twin (no gap, no calculation error), the realistic twin, twice its errors, and an uncalibrated twin (the full gap,
# whose fibre loss and noise figures run above the datasheet on average).
GAP_REAL, CALC_REAL = 1.0, 0.18
REAL = dict(gap=GAP_REAL, cal=True, calc=CALC_REAL, rc=False)
LOOP = dict(kind="loop", planner="advisor", returns=True, twin=True, c1=True)
ARMS = {
    "A":         {**LOOP, **REAL},
    "L":         {**LOOP, **REAL, "planner": "llm"},
    "A-once":    {**LOOP, **REAL, "returns": False},
    "A-rc":      {**LOOP, **REAL, "rc": True},
    "A-no-twin": {**LOOP, **REAL, "twin": False},
    "A-no-c1":   {**LOOP, **REAL, "c1": False},
    # the GNN ranker, and the twin's scoring role separated from its prediction of check answers (twin_score=False:
    # the twin predicts checks and runs the residual test only)
    "A-G":       {**LOOP, **REAL, "gnn": True},
    "G":         {**LOOP, **REAL, "gnn": True, "twin_score": False},
    "A-ts0":     {**LOOP, **REAL, "twin_score": False},
    "G-no-twin": {**LOOP, **REAL, "gnn": True, "twin": False},
    # a twin that also predicts equipment behaviour (alarms, protection switching, client counts, equipment checks),
    # which an operator's transmission twin does not: what building it in would add
    "A-twin-eq": {**LOOP, **REAL, "twin_eq": True},
    "A-twin-eq-doc": {**LOOP, **REAL, "twin_eq_matched": True},     # equipment simulated, judged as documented
    # peeling: returns also search where the leader's twin residual points; compounds fitted cause after cause
    "A-peel":    {**LOOP, **REAL, "peel": True},
    # instrumentation: the future branch (every site at the top tier the test bed models)
    "A-fut":      {**LOOP, **REAL, "instr": "future"},
    "A-peel-fut": {**LOOP, **REAL, "peel": True, "instr": "future"},
    # the transient event recogniser (specialist GNN) as a generation line
    "A-tr":       {**LOOP, **REAL, "transient": True},
    "A-tr-fut":   {**LOOP, **REAL, "transient": True, "instr": "future"},
    # the agent as built: peeling and the transient event recogniser together
    "A-full":     {**LOOP, **REAL, "peel": True, "transient": True},
    "A-full-fut": {**LOOP, **REAL, "peel": True, "transient": True, "instr": "future"},
    # the agent as built plus multiple-fault attribution on the twin's residual (specialist GNN) on return passes
    "A-full-mf":     {**LOOP, **REAL, "peel": True, "transient": True, "multifault": True},
    "A-full-mf-fut": {**LOOP, **REAL, "peel": True, "transient": True, "multifault": True, "instr": "future"},
    # the "can this decide?" step (campaign stage 1): costly checks (half an hour or more) refused when the chance of an
    # answer leading to a decision is under 5 per cent
    # the twin with structural error the world has and it does not model (world/gap.py MISMATCH)
    "A-full-tm1":    {**LOOP, **REAL, "peel": True, "transient": True, "tm": 1},
    "A-full-tm2":    {**LOOP, **REAL, "peel": True, "transient": True, "tm": 2},
    # amendment 17: the twin with structural error under the reported agent (with the "can this decide?" step)
    "A-full-cd-tm1": {**LOOP, **REAL, "peel": True, "transient": True, "decide": (0.5, 0.05), "tm": 1},
    "A-full-cd-tm2": {**LOOP, **REAL, "peel": True, "transient": True, "decide": (0.5, 0.05), "tm": 2},
    "B-tm1":         dict(kind="baseline", procedures="tool", tm=1, **REAL),
    "B-tm2":         dict(kind="baseline", procedures="tool", tm=2, **REAL),
    "A-full-cd":     {**LOOP, **REAL, "peel": True, "transient": True, "decide": (0.5, 0.05)},
    # the agent as reported (amendment 9): the LLM orchestrator plans and replans in place of the advisor; the
    # "can this decide?" gate belongs to the advisor and does not apply
    "L-full":        {**LOOP, **REAL, "peel": True, "transient": True, "planner": "llm"},
    # amendment 13: the LLM orchestrator with the "can this decide?" step applied to the checks it is offered, so that
    # it differs from A-full-cd only in who chooses among the admissible checks
    "L-full-cd":     {**LOOP, **REAL, "peel": True, "transient": True, "planner": "llm", "decide": (0.5, 0.05)},
    # amendment 15: the same arm with the LLM shown the advisor's own check groups, "none of these" included
    "L-full-cd2":    {**LOOP, **REAL, "peel": True, "transient": True, "planner": "llm", "decide": (0.5, 0.05)},
    # amendment 16: the agent with the LLM building the first case frame with query tools (tools/frame_llm.py) in
    # place of the deterministic framer; everything else as A-full-cd (return passes still widen the frame with the
    # deterministic framer, as peeling does)
    "A-full-cd-lf":  {**LOOP, **REAL, "peel": True, "transient": True, "decide": (0.5, 0.05), "framer": "llm"},
    "A-full-cd-fut": {**LOOP, **REAL, "peel": True, "transient": True, "decide": (0.5, 0.05), "instr": "future"},
    "A-tr-cd":       {**LOOP, **REAL, "transient": True, "decide": (0.5, 0.05)},
    "A-tr-cd-fut":   {**LOOP, **REAL, "transient": True, "decide": (0.5, 0.05), "instr": "future"},
    # the twin's fidelity
    "A-perfect": {**LOOP, **REAL, "gap": 0.0, "calc": 0.0},
    "A-2x":      {**LOOP, **REAL, "gap": 2 * GAP_REAL, "calc": 2 * CALC_REAL},
    "A-uncal":   {**LOOP, **REAL, "gap": 1.0, "cal": False},
    "B":         dict(kind="baseline", procedures="tool", **REAL),
    "B-graph":   dict(kind="baseline", procedures="graph", **REAL),
    "B-fut":     dict(kind="baseline", procedures="tool", instr="future", **REAL),
    # alarm correlation alone (campaign, stage 1): the equipment's root alarm and its documented probable causes,
    # no troubleshooting; one probable cause is a commitment, several are reported as a set
    "AC":        dict(kind="ac", **REAL),
    # the LLM diagnoses and decides alone (src/adagent/tools/llm_alone.py): single shot, and with checks
    "LLM-1":     dict(kind="llm_alone", checks=False, **REAL),
    "LLM-chk":   dict(kind="llm_alone", checks=True, **REAL),
    "AC-fut":    dict(kind="ac", instr="future", **REAL),
    # today's practice as handed off (amendment 11): the procedure for alarm correlation's root alarm, started at its
    # unit and followed as in B (tools/baseline_ac.py)
    "B-ac":      dict(kind="baseline", procedures="tool", handoff=True, **REAL),
    "B-ac-fut":  dict(kind="baseline", procedures="tool", handoff=True, instr="future", **REAL),
}
# the capability ladder (campaign; spec/PROTOCOL_CAMPAIGN.md, amendment "the capability ladder"): each rung adds one
# telemetry capability to the world (world/ladder.py) and its documented use to the agent (tools/ladder.py), on the
# future instrumentation branch. A-Lk: the agent of A-full-cd-fut with rung k alone; B-L1: manual troubleshooting
# under the future branch with L1's alarm; A-Lall and B-Lall: every rung.
LADDER_RUNGS = ("L1", "L2", "L3", "L4", "L5", "L6")
for _k in LADDER_RUNGS:
    ARMS[f"A-{_k}"] = {**ARMS["A-full-cd-fut"], "ladder": (_k,)}
ARMS["B-L1"] = {**ARMS["B-fut"], "ladder": ("L1",)}
ARMS["A-Lall"] = {**ARMS["A-full-cd-fut"], "ladder": LADDER_RUNGS}
ARMS["B-Lall"] = {**ARMS["B-fut"], "ladder": LADDER_RUNGS}
# spectral monitoring (spectral mode, ADAGENT_SPECTRAL=1; docs/campaign/spectral_design.md): the baseline in spectral
# mode, span-end spectra (S), receiver shape with and without the learned penalty model (R, R + NOPM), both
ARMS["A-sp"] = dict(ARMS["A-full-cd-fut"])
ARMS["A-S"] = {**ARMS["A-full-cd-fut"], "ladder": ("S",)}
ARMS["A-R"] = {**ARMS["A-full-cd-fut"], "ladder": ("R",)}
ARMS["A-R-nopm"] = {**ARMS["A-full-cd-fut"], "ladder": ("R", "NOPM")}
ARMS["A-SR"] = {**ARMS["A-full-cd-fut"], "ladder": ("S", "R")}
ARMS["B-sp"] = dict(ARMS["B-fut"])
ARMS["B-SR"] = {**ARMS["B-fut"], "ladder": ("S", "R")}
# the kinds of case (cases2.KINDS) whose faults a rung's observable can change, so where its single-rung arm runs:
# L1 counters stop only for stale telemetry (run on every case all the same, as the reference rung); per-span DGD moves
# only with a DGD rise; WSS port power moves with a fault at the port or a loss beyond the WSS's re-levelling headroom
# upstream, and the filter offset with a filter shift; span captures and event logs record only brief events; the
# cross-check can be asked of any amplifier or receiver a monitoring hypothesis names, which any case may hold.
LADDER_KINDS = {
    "L1": ("hard", "soft", "transient", "drift", "monitoring", "coincident", "maintenance", "common_cause"),
    "L2": ("soft", "coincident", "maintenance"),
    "L3": ("hard", "soft", "transient", "coincident", "maintenance", "common_cause"),
    "L4": ("transient",),
    "L5": ("transient",),
    "L6": ("monitoring",),     # the cross-check confirms a misreporting instrument; A-Lall covers the other kinds
}


def ladder_seeds(rung: str, lo: int = 2000, hi: int = 2319) -> list:
    """The diagnosis cases (seeds lo..hi) of the kinds a rung can affect (make_case2 cycles the kinds by seed)."""
    from .cases2 import KINDS
    return [s for s in range(lo, hi + 1) if KINDS[s % len(KINDS)] in LADDER_KINDS[rung]]
BUDGET_H = 8.0


def case_seed(case) -> int:
    return int(case.id.split("-s")[1].split("-")[0])


def _locs(h) -> list[str]:
    out = []
    for c in h.causes:
        loc = c.location
        out += [loc.element] if loc.kind == "element" else [loc.span] if loc.kind == "span" else list(loc.elements)
    return out


def world_loc(kg, el: str) -> str:
    """Instrument hypotheses name the instrument; the world names the element it is on. A WSS input port
    ("oms|port") is named in the world by the OMS arriving on it (WSS_PORT_FAIL's target)."""
    base, _, rest = el.partition("|")
    if rest == "port":
        return base
    if kg.has(el) and kg.cls(el) == "Instrument":
        return kg.g.nodes[el].get("lake_element", el)
    return el


def cause_keys(kg, h) -> list[str]:
    out = []
    for c in h.causes:
        loc = c.location
        els = [loc.element] if loc.kind == "element" else [loc.span] if loc.kind == "span" else list(loc.elements)
        out += [f"{c.fault_mode}@{world_loc(kg, e)}" for e in els]
    return out


def effort_of(case) -> tuple[float, int, int]:
    seed = case_seed(case)
    acq = [c for c in case.substrate.acquisitions if "check" in c]
    hours = sum(c.get("minutes", 0) for c in acq) / 60.0
    sites = []
    for c in acq:
        for s in c.get("dispatch_sites") or []:
            if s not in sites:
                sites.append(s)
                hours += travel_hours(seed, s)
    return round(hours, 2), len(sites), sites


def score(case, decision: dict, hyp, active: list[str], loop_outcome: Optional[str] = None,
          budget_h: float = BUDGET_H) -> dict:
    """Score a decision ({"outcome": "commit", "hypothesis"} or {"outcome": "set", "members", ...}) on a case.
    hyp(key) gives the hypothesis for a key; active lists every hypothesis the arm held.

    The parts of the truth a diagnosis must name are its faults; a single cut of several cable sections (a duct
    cut, both fibres of two or more cables) has one part per section. A hypothesis is correct when it names, for
    every part, a member of that part's equivalence class (for a cut section: either fibre of the section, or a
    cause indistinguishable from cutting that section with the rest of the cut held). A single cause in the
    class of the whole truth is also correct, since the data allow no better. A hypothesis naming some parts but
    not all is commit_partial: naming one cable of a duct cut is not finding the common cause.

    A correct commitment is commit_correct_ambiguous only if some class member other than the truth is left
    unseparated by the checks the agent made in the case (status done), each re-performed in a world with the
    truth and a world with the member at its best fit; it is commit_correct when every such member was separated.
    The class sizes are kept (class_size), and ambiguous_after_checks lists the members left unseparated."""
    from . import equivalence
    L = case.truth.labels
    fut = "" if L.get("instrumentation", "status_quo") == "status_quo" else f"_i{L['instrumentation']}"
    mm = f"_m{L['mismatch']}" if L.get("mismatch") else ""
    # the ladder's sampled streams change the fits (their own memo); its event-like rungs are checked on top of the
    # fits without them (equivalence._fit_with), which every arm on the case shares
    cont = sorted(set(L.get("ladder") or ()) & equivalence.CONT_RUNGS)
    lad = f"_L{''.join(x[1:] for x in cont)}" if cont else ""
    equivalence.fit_context(f"{case.id}_g{L.get('gap_scale')}_c{int(bool(L.get('gap_calibrated')))}"
                            f"_r{int(bool(L.get('records_complete')))}{fut}{mm}{lad}")
    equivalence.LADDER = frozenset(L.get("ladder") or ())
    try:
        return _score(case, decision, hyp, active, loop_outcome, budget_h)
    finally:
        equivalence.fit_context(None)
        equivalence.LADDER = frozenset()


def _score(case, decision: dict, hyp, active: list[str], loop_outcome: Optional[str], budget_h: float) -> dict:
    kg = case.substrate.kg
    net = case.world.net
    locs = [world_loc(kg, e) for k in active if k != "NONE_OF_THESE" for e in _locs(hyp(k))]
    locs = list(dict.fromkeys(locs))
    faults = case.truth.faults
    eq = equivalence_classes_v2(case, locs)
    comps = [set(c) for c in eq["components"]]
    whole = set(eq["whole"])
    truth_keys = {f"{f.mode}@{t}" for f in faults for t in f.targets}
    # parts: (fault index, targets, class). Several faults: one part per fault, its component class. One cut of
    # several cable sections: one part per section, its section class. Otherwise one part, the whole class.
    sections = (cable_sections(net, faults[0].targets)
                if len(faults) == 1 and faults[0].mode == "FIBRE_CUT" else [])
    if len(faults) > 1:
        parts = [(i, list(f.targets), comps[i]) for i, f in enumerate(faults)]
    elif len(sections) > 1:
        sec_cls = part_classes(case, locs, 0, sections)
        parts = [(0, [t for t in faults[0].targets if t in sec], set(c)) for sec, c in zip(sections, sec_cls)]
    else:
        parts = [(0, list(faults[0].targets), whole)]
    # for a duct cut, a single cause indistinguishable from the whole cut also counts (whole holds the truth's
    # own keys, which must not count on their own: one of them names only one section)
    whole_alt = (whole - truth_keys) if len(faults) == 1 else set()

    def correct(keys):
        if any(k in whole_alt for k in keys):
            return True
        return all(any(k in cls for k in keys) for _, _, cls in parts)

    def own(tg, i):            # a part's own keys: for a cut, both fibres of each section
        return {f"{faults[i].mode}@{t}" for t0 in tg for t in _targets(net, faults[i].mode, t0)}

    def names_part(keys):
        return any(k in cls for _, _, cls in parts for k in keys)

    present = any(correct(cause_keys(kg, hyp(k))) for k in active if k != "NONE_OF_THESE")
    hours, visits, sites = effort_of(case)
    out = {"truth_present": present, "class_whole": sorted(whole), "class_components": [sorted(c) for c in comps],
           "effort_h": hours, "site_visits": visits,
           "service_affecting": sum(1 for c in case.substrate.acquisitions
                                    if c.get("check") in ("loopback", "retune_spare", "swap_unit", "inspect_clean")
                                    and c.get("status") == "done")}
    if len(sections) > 1:
        out["class_sections"] = [sorted(cls) for _, _, cls in parts]
    if decision.get("outcome") == "commit":
        ck = cause_keys(kg, hyp(decision["hypothesis"]))
        ok = correct(ck)
        size = max(len(cls - own(tg, i)) + 1 for i, tg, cls in parts)
        if len(faults) == 1 and len(sections) > 1:
            size = max(size, len(whole_alt) + 1)
        out["class_size"] = size
        partial = not ok and len(parts) > 1 and names_part(ck)
        if ok:
            # which members could the agent's own completed checks not tell from the truth? per part, the
            # member replaces that part with the rest of the truth held; for a duct cut, also single causes
            # that replace the whole cut
            checks = done_checks(case)
            seed = case_seed(case)
            left = []
            for i, tg, cls in parts:
                left += unseparated(case, sorted(cls - own(tg, i)), checks, (i, tg), seed)
            if whole_alt and len(parts) > 1:
                left += unseparated(case, sorted(whole_alt), checks, (0, list(faults[0].targets)), seed)
            out["ambiguous_after_checks"] = sorted(set(left))
            out["outcome"] = "commit_correct_ambiguous" if left else "commit_correct"
        else:
            out["outcome"] = "commit_partial" if partial else "commit_wrong"
    elif loop_outcome == "non_convergence":
        out["outcome"] = "non_convergence"
    else:
        members = [m for m in decision.get("members", []) if m != "NONE_OF_THESE"]
        if not any(correct(cause_keys(kg, hyp(m))) for m in members):
            unconsidered = decision.get("cause_probably_unconsidered") and not present
            out["outcome"] = "dont_know_justified" if unconsidered else "dont_know_unjustified"
            out["why"] = "cause flagged as unconsidered and absent" if unconsidered else "truth not in the reported set"
        else:
            # the members a planner can act on: the reported set's leaders, at most the planner's cap
            from ..tools.advisor import MAX_CONTENDERS
            cont = sorted({k for m in members[:MAX_CONTENDERS] for k in cause_keys(kg, hyp(m))})
            sep = separating_checks(case, cont, budget_h - hours, visited=sites, case_seed=case_seed(case),
                                    t_s=case.substrate.now + 3600.0 * hours)
            deciding = {k: v for k, v in sep.items() if v}
            out["separable"] = {k: v[:2] for k, v in deciding.items()}
            out["outcome"] = "dont_know_unjustified" if deciding else "dont_know_justified"
        cc = decision.get("coarse")
        if cc:
            # a commitment at a coarser grain (tools/grain.py): right if a true fault lies at that place (or is of
            # that kind); wrong otherwise. The "I don't know" outcome it replaces is kept for comparison.
            from ..tools.grain import owner, place
            if cc["grain"] == "cause":
                mode, _, loc = cc["at"].partition("@")
                at = (loc[3:] if loc[:3] in ("el:", "sp:") else loc).split("(")[0].split("[")[0]
                if kg.has(at) and kg.cls(at) == "Instrument":
                    at = kg.g.nodes[at].get("lake_element", at)
                hit = [f for f in faults if f.mode == mode and any(t == at or t.split("|")[0] == at.split("|")[0]
                                                                   for t in f.targets)]
            elif cc["grain"] == "mode":
                hit = [f for f in faults if f.mode == cc["at"]]
            else:
                hit = [f for f in faults if any(place(kg, owner(kg, t), cc["grain"]) == cc["at"] for t in f.targets)]
            out["coarse"] = {**cc, "faults_at": len(hit), "faults": len(faults)}
            out["dont_know_outcome"] = out["outcome"]
            if cc["grain"] == "cause":       # a commitment to one cause: scored as one
                out["outcome"] = ("commit_correct" if len(faults) == 1 else "commit_partial") if hit else "commit_wrong"
            else:
                out["outcome"] = "coarse_correct" if hit else "coarse_wrong"
    return out


@lru_cache(maxsize=4)
def experience(day: float, gap: float, rc: bool, cal: bool = False, mm: int = 0) -> ExperienceStore:
    store = ExperienceStore()
    build_graph(context(gap_scale=gap, records_complete=rc, gap_calibrated=cal, mismatch=mm).events, day, sources(),
                observers=[store])
    return store.as_of(day)


def lines_for(case, day: float, gap: float, rc: bool, cal: bool = False, mm: int = 0) -> dict:
    sub, kg = case.substrate, case.substrate.kg
    abd = AbductionWalk(kg)
    return {"abduction": abd, "pagerank": RandomWalkRanking(kg), "setcover": MinimumExplanation(kg, abd),
            "alarm_correlation": AlarmCorrelation(kg),
            "analogy": AnalogySearch(kg, experience(day, gap, rc, cal, mm), GroundingTool(kg)),
            "procedures": ProcedureTool(sub, ProcedureLibrary.from_sources(sources()))}


def run_job(arm: str, day: float, seed: int, client=None) -> dict:
    cfg = ARMS[arm]
    t0 = time.time()
    case = make_case2(day, seed, gap_scale=cfg["gap"], records_complete=cfg["rc"],
                      procedures_in_graph=cfg.get("procedures") == "graph", gap_calibrated=cfg.get("cal", False),
                      instrumentation=cfg.get("instr", "status_quo"), mismatch=cfg.get("tm", 0),
                      ladder=cfg.get("ladder", ()))
    sub, kg = case.substrate, case.substrate.kg
    if cfg.get("framer") == "llm":
        from ..tools.frame_llm import LLMFramer
        nf0 = len(client.records())
        framer = LLMFramer(client, sub)
        frame = framer.build(case.question)
        framer_rec = {"trace": framer.trace, "examined": sorted(framer.examined),
                      "symptoms": len(frame.symptoms), "quiet_instruments": len(frame.quiet_instruments),
                      "calls": [{k: c.get(k) for k in ("latency_s", "input_tokens", "output_tokens",
                                                        "reasoning_tokens", "ok", "error")}
                                for c in client.records()[nf0:]]}
    else:
        frame = DeterministicFramer(sub).build(case.question)
        framer_rec = None
    row = {"arm": arm, "case": case.id, "day": day, "seed": seed, "labels": case.truth.labels,
           "truth": [{"mode": f.mode, "targets": f.targets, "params": f.params} for f in case.truth.faults]}
    if framer_rec is not None:
        row["llm_framer"] = framer_rec
    if cfg["kind"] == "loop":
        planner = None
        if cfg["planner"] == "llm":
            from ..tools.orchestrator_llm import LLMOrchestrator
            n0 = len(client.records())
            planner = LLMOrchestrator(client)
        loop = WinnowingLoop(sub, sources(), frame, lines_for(case, day, cfg["gap"], cfg["rc"], cfg.get("cal", False), cfg.get("tm", 0)), budget_h=BUDGET_H,
                             case_seed=seed, use_twin=cfg["twin"], use_c1=cfg["c1"], returns=cfg["returns"],
                             planner=planner, use_gnn=cfg.get("gnn", False), twin_score=cfg.get("twin_score", True),
                             question=case.question, twin_calc_err_db=cfg.get("calc", 0.0),
                             twin_equipment=cfg.get("twin_eq", False),
                             twin_eq_matched=cfg.get("twin_eq_matched", False), peel=cfg.get("peel", False),
                             transient=cfg.get("transient", False), decide_gate=cfg.get("decide"),
                             multifault=cfg.get("multifault", False), ladder=cfg.get("ladder", ()))
        t1 = time.time()
        rec = loop.run()
        row["agent_seconds"] = round(time.time() - t1, 1)
        if cfg["planner"] == "llm":
            row["llm_calls"] = [{k: c.get(k) for k in ("latency_s", "input_tokens", "output_tokens",
                                                       "reasoning_tokens", "ok", "error")}
                                for c in client.records()[n0:]]
        led = loop.ledger
        row["loop"] = rec
        row["score"] = score(case, rec["decision"], led.hypothesis, led.active_keys(), rec["outcome"])
    elif cfg["kind"] == "llm_alone":
        from ..tools.llm_alone import LLMAlone
        n0 = len(client.records())
        la = LLMAlone(client, sub, sources(), frame, GroundingTool(kg), with_checks=cfg["checks"], budget_h=BUDGET_H)
        t1 = time.time()
        dec, hyps, rec = la.run()
        row["agent_seconds"] = round(time.time() - t1, 1)
        row["llm_alone"] = rec
        row["llm_calls"] = [{k: c.get(k) for k in ("latency_s", "input_tokens", "output_tokens",
                                                   "reasoning_tokens", "ok", "error")}
                            for c in client.records()[n0:]]
        row["score"] = score(case, dec, lambda k: hyps[k], list(hyps))
    elif cfg["kind"] == "ac":
        from ..tools.correlation import AlarmCorrelation
        ac = AlarmCorrelation(kg)
        t1 = time.time()
        pay = ac.run(frame)
        row["agent_seconds"] = round(time.time() - t1, 2)
        hyps = {f"ac{i}": c.hypothesis for i, c in enumerate(pay.candidates)}
        if len(hyps) == 1:
            dec = {"outcome": "commit", "hypothesis": "ac0"}
        else:
            dec = {"outcome": "set", "members": list(hyps), "cause_probably_unconsidered": not hyps}
        row["baseline"] = {"trace": {k: (list(v) if isinstance(v, tuple) else v) for k, v in ac.trace.items()},
                           "n_causes": len(hyps)}
        row["score"] = score(case, dec, lambda k: hyps[k], list(hyps))
        if not hyps:
            row["score"]["outcome"] = "no_procedure"      # no alarm to correlate
    else:
        lib = (ProcedureLibrary.from_graph(kg) if cfg["procedures"] == "graph"
               else ProcedureLibrary.from_sources(sources()))
        if cfg.get("ladder"):
            # the ladder's alarms (raised by the management system) and their procedures
            from ..tools.ladder import LadderGrounding, ladder_library
            lib = ladder_library(lib, cfg["ladder"])
            b = ManualTroubleshooting(sub, lib, grounding=LadderGrounding(kg))
            b.extra_alarm_sources = ("nms_alarm",)
        elif cfg.get("handoff"):
            from ..tools.baseline_ac import ManualFromCorrelation
            b = ManualFromCorrelation(sub, lib)
        else:
            b = ManualTroubleshooting(sub, lib)
        q0, t1 = kg.cost.nodes_touched, time.time()
        res = b.diagnose(frame)
        row["agent_seconds"] = round(time.time() - t1, 2)
        row["graph_nodes_touched"] = kg.cost.nodes_touched - q0
        hyps = res["hypotheses"]
        dec = {"outcome": "commit", "hypothesis": res["hypothesis"]} if res["outcome"] == "commit" else \
            {"outcome": "set", "members": res.get("members", []), "cause_probably_unconsidered": not hyps}
        row["baseline"] = {k: v for k, v in res.items() if k != "hypotheses"}
        row["score"] = score(case, dec, lambda k: hyps[k], list(hyps))
        if res["outcome"] == "no_procedure":
            row["score"]["outcome"] = "no_procedure"      # no alarm or symptom the procedures start from
    row["seconds"] = round(time.time() - t0, 1)
    return row
