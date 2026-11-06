"""Experiment 2, rules comparison 2: graph consistency with its rules in code against the same rules held in the
graph and evaluated by a general engine (spec/PROTOCOL_EXP2_WINNOWING.md §4).

Run from the project root:
    python scripts/exp2_rules_compare.py --seeds 2000-2159 --workers 6

For each case: the frame and every candidate of the first generation pass (the six offline lines and the
specialised lines), each put to both versions. Recorded per case: candidates, verdicts that agree and disagree (rule
by rule, with both reasons), requests the engine raised for relations not declared complete, and the time each
version took. Writes results/a41/rules_compare/s<seed>.json (cases in the REAL arms' world); resumable. No API calls.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def seeds_arg(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def job(args):
    day, seed, outdir = args
    from adagent.harness.cases import sources
    from adagent.harness.cases2 import make_case2
    from adagent.harness.exp2 import lines_for
    from adagent.tools.consistency import GraphConsistency
    from adagent.tools.frame import DeterministicFramer
    from adagent.tools.loop import canonical, monitoring_payload, shared_risk_payload, trend_payload
    from adagent.tools.rules_graph import GraphRuleConsistency
    f = Path(outdir) / f"s{seed}.json"
    try:
        case = make_case2(day, seed, gap_scale=1.0, gap_calibrated=True)   # the REAL arms' world (exp2.ARMS)
        sub, kg = case.substrate, case.substrate.kg
        frame = DeterministicFramer(sub).build(case.question)
        hyps = {}
        pays = [g.run(frame) for g in lines_for(case, day, 1.0, False).values()]
        pays += [monitoring_payload(frame, kg), shared_risk_payload(frame, kg), trend_payload(frame, sub)]
        for p in pays:
            for c in canonical(p, kg).candidates:
                hyps[c.hypothesis.key()] = c.hypothesis
        _ = sources()
        code, graph = GraphConsistency(kg, frame), GraphRuleConsistency(kg, frame)
        t0 = time.perf_counter()
        vc = {k: code.evaluate(h) for k, h in hyps.items()}
        t_code = time.perf_counter() - t0
        t0 = time.perf_counter()
        vg = {k: graph.evaluate(h) for k, h in hyps.items()}
        t_graph = time.perf_counter() - t0
        agree, differ, requests = 0, [], []
        for k in hyps:
            a = {r.rule: r for r in vc[k].rules}
            b = {r.rule: r for r in vg[k].rules}
            for rule in sorted(a.keys() | b.keys()):
                ra, rb = a.get(rule), b.get(rule)
                if ra and rb and ra.verdict == rb.verdict:
                    agree += 1
                else:
                    differ.append({"hypothesis": k, "rule": rule,
                                   "code": (ra.verdict, ra.reason) if ra else None,
                                   "graph": (rb.verdict, rb.reason) if rb else None})
                if rb and rb.requests:
                    requests.append({"hypothesis": k, "rule": rule, "requests": rb.requests})
        row = {"case": case.id, "seed": seed, "kind": case.truth.labels["kind"], "candidates": len(hyps),
               "rule_verdicts_agree": agree, "rule_verdicts_differ": len(differ), "differences": differ[:50],
               "overall_differ": sum(1 for k in hyps if vc[k].verdict != vg[k].verdict),
               "engine_requests": requests[:50], "seconds_code": round(t_code, 3), "seconds_graph": round(t_graph, 3)}
        f.write_text(json.dumps(row, default=str))
        return f"s{seed} {case.id:<28} candidates={len(hyps):>3} differ={len(differ):>3} " \
               f"code={t_code:.2f}s graph={t_graph:.2f}s"
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"seed": seed, "error": repr(e)[:500]}))
        return f"s{seed} FAILED {repr(e)[:120]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--day", type=float, default=2190.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="results/a41/rules_compare")
    a = ap.parse_args()
    outdir = ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = [(a.day, s, str(outdir)) for s in seeds_arg(a.seeds) if not (outdir / f"s{s}.json").exists()]
    print(f"{len(jobs)} cases on {a.workers} workers", flush=True)
    if a.workers <= 1:
        for j in jobs:
            print(job(j), flush=True)
    else:
        with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=8) as pool:
            for line in pool.imap_unordered(job, jobs):
                print(line, flush=True)


if __name__ == "__main__":
    main()
