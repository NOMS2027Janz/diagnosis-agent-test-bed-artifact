"""Experiment 2 development runs: the winnowing loop (advisor arm, no LLM) on development cases, scored by the harness.

Run from the project root:  python scripts/exp2_dev.py [--seeds 9100-9115] [--day 2190] [--out results/exp2_dev/dev.jsonl]
No API calls. Development seeds only (9100 upwards); the held-out seeds of the protocol are not touched.

For each case: the frame (deterministic framer), the six offline lines of Experiment 1 and the monitoring-fault
generator, then the loop (tools/loop.py). The harness then scores the result:
- truth: every cause, its equivalence class on the data held (equivalence_classes_v2, over every location any
  hypothesis named);
- outcome: commit correct, commit wrong, "I don't know" justified or unjustified (separating_checks within the
  effort left), or non-convergence;
- effort as incurred: hours (time to result, plus actual travel once per site), site visits, service-affecting
  checks.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adagent.harness.cases import context, sources                          # noqa: E402
from adagent.harness.cases2 import NotObservable, make_case2                 # noqa: E402
from adagent.harness.equivalence import equivalence_classes_v2              # noqa: E402
from adagent.harness.separability import separating_checks                  # noqa: E402
from adagent.substrate.lifecycle import build_graph                          # noqa: E402
from adagent.tools.analogy import AnalogySearch                             # noqa: E402
from adagent.tools.baseline import ManualTroubleshooting                    # noqa: E402
from adagent.tools.correlation import AlarmCorrelation                      # noqa: E402
from adagent.tools.experience import ExperienceStore                        # noqa: E402
from adagent.tools.frame import DeterministicFramer                         # noqa: E402
from adagent.tools.grounding import GroundingTool                           # noqa: E402
from adagent.tools.loop import WinnowingLoop                                # noqa: E402
from adagent.tools.procedures import ProcedureLibrary, ProcedureTool        # noqa: E402
from adagent.tools.walks import AbductionWalk, MinimumExplanation, RandomWalkRanking  # noqa: E402
from adagent.world.checks import travel_hours                               # noqa: E402


def seeds_arg(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def _el(h) -> list[str]:
    out = []
    for c in h.causes:
        loc = c.location
        out += [loc.element] if loc.kind == "element" else [loc.span] if loc.kind == "span" else list(loc.elements)
    return out


def _world_loc(kg, el: str) -> str:
    """Instrument hypotheses name the instrument; the world names the element it is on."""
    if kg.has(el) and kg.cls(el) == "Instrument":
        return kg.g.nodes[el].get("lake_element", el)
    return el


def cause_keys(kg, h) -> list[str]:
    return [f"{c.fault_mode}@{_world_loc(kg, e)}" for c in h.causes
            for e in ([c.location.element] if c.location.kind == "element" else
                      [c.location.span] if c.location.kind == "span" else list(c.location.elements))]


def score(case, loop: WinnowingLoop, rec: dict, budget_h: float) -> dict:
    kg = case.substrate.kg
    led = loop.ledger
    locs = []
    for k in led.active_keys():
        h = led.hypothesis(k)
        locs += [_world_loc(kg, e) for e in _el(h)]
    faults = case.truth.faults
    eq = equivalence_classes_v2(case, list(dict.fromkeys(locs)))
    comps = [set(c) for c in eq["components"]]
    whole = set(eq["whole"])

    def correct(keys: list[str]) -> bool:
        if len(faults) == 1:
            return any(k in whole for k in keys)
        return all(any(k in comp for k in keys) for comp in comps)

    present = any(correct(cause_keys(kg, led.hypothesis(k))) for k in led.active_keys() if k != "NONE_OF_THESE")
    dec = rec["decision"]
    seed = int(case.id.split("-s")[1].split("-")[0])
    hours = sum(c.get("minutes", 0) for c in case.substrate.acquisitions if "check" in c) / 60.0
    sites = []
    for c in case.substrate.acquisitions:
        for s in c.get("dispatch_sites") or []:
            if s not in sites:
                sites.append(s)
                hours += travel_hours(seed, s)
    out = {"truth_present": present, "class_whole": sorted(whole), "class_components": [sorted(c) for c in comps],
           "effort_h": round(hours, 2), "site_visits": len(sites)}
    if dec["outcome"] == "commit":
        ck = cause_keys(kg, led.hypothesis(dec["hypothesis"]))
        ok = correct(ck)
        truth_keys = {f"{f.mode}@{t}" for f in faults for t in f.targets}   # one event, several targets: one member
        size = (len(whole - truth_keys) + 1) if len(faults) == 1 else max(len(set(c) - truth_keys) + 1 for c in comps)
        partial = len(faults) > 1 and not ok and any(k in comp for comp in comps for k in ck)
        out["outcome"] = (("commit_correct_ambiguous" if size > 1 else "commit_correct") if ok
                          else "commit_partial" if partial else "commit_wrong")
    elif rec["outcome"] == "non_convergence":
        out["outcome"] = "non_convergence"
    else:
        members = [m for m in dec["members"] if m != "NONE_OF_THESE"]
        in_set = any(correct(cause_keys(kg, led.hypothesis(m))) for m in members)
        if not in_set:
            unconsidered = dec.get("cause_probably_unconsidered") and not present
            out["outcome"] = "dont_know_justified" if unconsidered else "dont_know_unjustified"
            out["why"] = "cause flagged as unconsidered and absent" if unconsidered else "truth not in the reported set"
        else:
            cont = sorted({k for m in members for k in cause_keys(kg, led.hypothesis(m))})
            sep = separating_checks(case, cont, budget_h - hours, visited=sites, case_seed=seed,
                                    t_s=case.substrate.now + 3600.0 * hours)
            deciding = {k: v for k, v in sep.items() if v}
            out["separable"] = {k: v[:2] for k, v in deciding.items()}
            out["outcome"] = "dont_know_unjustified" if deciding else "dont_know_justified"
    return out


def effort_of(case) -> tuple[float, int]:
    seed = int(case.id.split("-s")[1].split("-")[0])
    hours = sum(c.get("minutes", 0) for c in case.substrate.acquisitions if "check" in c) / 60.0
    sites = []
    for c in case.substrate.acquisitions:
        for s in c.get("dispatch_sites") or []:
            if s not in sites:
                sites.append(s)
                hours += travel_hours(seed, s)
    return round(hours, 2), len(sites)


def run_baseline(day: float, seed: int, src, lib) -> dict:
    """Baseline B on its own copy of the case (checks can repair the world)."""
    case = make_case2(day, seed)
    sub, kg = case.substrate, case.substrate.kg
    frame = DeterministicFramer(sub).build(case.question)
    b = ManualTroubleshooting(sub, lib)
    res = b.diagnose(frame)
    truth = {f"{f.mode}@{t}" for f in case.truth.faults for t in f.targets}

    def keys(k):
        return cause_keys(kg, res["hypotheses"][k]) if k in res["hypotheses"] else []
    if res["outcome"] == "commit":
        hit = set(keys(res["hypothesis"])) & truth
        outcome = ("commit_correct" if len(case.truth.faults) == 1 else "commit_one_of_several") if hit \
            else "commit_wrong"
    elif res.get("members"):
        outcome = "set_contains_truth" if any(set(keys(m)) & truth for m in res["members"]) else "set_without_truth"
    else:
        outcome = res["outcome"]
    hours, visits = effort_of(case)
    return {"outcome": outcome, "effort_h": hours, "site_visits": visits, "checks": len(res["checks"]),
            "trigger": res["trace"].get("trigger"), "procedure": res["trace"].get("procedure"),
            "candidates": res["candidates"][:5], "check_log": res["checks"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="9100-9115")
    ap.add_argument("--day", type=float, default=2190.0)
    ap.add_argument("--budget", type=float, default=8.0)
    ap.add_argument("--out", default="results/exp2_dev/dev.jsonl")
    ap.add_argument("--arms", default="loop,baseline")
    ap.add_argument("--planner", default="advisor", choices=["advisor", "llm", "stub"],
                    help="advisor (deterministic), llm (OpenAI, the orchestrator) or stub (no API: records the "
                         "prompts the orchestrator would be sent, and stops)")
    a = ap.parse_args()
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(x)["case"] for x in out.open()} if out.exists() else set()
    client = None
    if a.planner == "llm":
        from adagent.llm.client import LLMClient
        client = LLMClient("openai")
    elif a.planner == "stub":
        from adagent.tools.orchestrator_llm import Decision

        class _Stub:
            def __init__(self):
                self.log = []

            def parse(self, role, pv, system, user, schema):
                self.log.append({"system_chars": len(system), "user_chars": len(user)})
                return Decision(action="stop", check_index=None, reason="stub")

            def records(self):
                return self.log
        client = _Stub()
    src = sources()
    store = ExperienceStore()
    build_graph(context(gap_scale=1.0).events, a.day, src, observers=[store])
    lib = ProcedureLibrary.from_sources(src)
    for seed in seeds_arg(a.seeds):
        t0 = time.time()
        try:
            case = make_case2(a.day, seed)
        except NotObservable as e:
            print(f"s{seed}: not observable ({e})")
            continue
        if case.id in done:
            print(f"{case.id}: already done, skipped", flush=True)
            continue
        sub, kg = case.substrate, case.substrate.kg
        frame = DeterministicFramer(sub).build(case.question)
        abd = AbductionWalk(kg)
        lines = {"abduction": abd, "pagerank": RandomWalkRanking(kg), "setcover": MinimumExplanation(kg, abd),
                 "alarm_correlation": AlarmCorrelation(kg),
                 "analogy": AnalogySearch(kg, store.as_of(a.day), GroundingTool(kg)),
                 "procedures": ProcedureTool(sub, lib)}
        row = {"case": case.id, "planner": a.planner, "labels": case.truth.labels,
               "truth": [{"mode": f.mode, "targets": f.targets} for f in case.truth.faults]}
        arms = a.arms.split(",")
        sc = {"outcome": "-", "effort_h": 0.0}
        rec = {"decision": {}, "passes": [], "checks": []}
        if "loop" in arms:
            planner = None
            if client is not None:
                from adagent.tools.orchestrator_llm import LLMOrchestrator
                n0 = len(client.records())
                planner = LLMOrchestrator(client)
            loop = WinnowingLoop(sub, src, frame, lines, budget_h=a.budget, case_seed=seed, planner=planner)
            rec = loop.run()
            if client is not None:
                calls = client.records()[n0:]
                row["llm_calls"] = [{k: c.get(k) for k in ("latency_s", "input_tokens", "output_tokens",
                                                           "reasoning_tokens", "ok", "error", "system_chars",
                                                           "user_chars")} for c in calls]
            sc = score(case, loop, rec, a.budget)
            row.update(loop={k: v for k, v in rec.items()}, score=sc)
        if "baseline" in arms:
            row["baseline"] = run_baseline(a.day, seed, src, lib)
        row["seconds"] = round(time.time() - t0, 1)
        with out.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        d = rec["decision"]
        lead = d.get("hypothesis") or (d.get("members") or ["-"])[0]
        bl = row.get("baseline", {})
        print(f"{case.id:>26}  loop: {sc['outcome']:<24} passes={len(rec['passes'])} checks={len(rec['checks'])} "
              f"effort={sc['effort_h']:.1f}h lead={lead[:50]} | baseline: {bl.get('outcome', '-')} "
              f"checks={bl.get('checks', 0)} effort={bl.get('effort_h', 0):.1f}h  {row['seconds']:.0f}s", flush=True)


if __name__ == "__main__":
    main()
