"""Experiment 2 analysis: which generation methods and winnowing tools did the work, by kind of fault.

Arm A is deterministic (a rerun reproduces the stored results exactly), so this script reruns arm A on each case and
keeps what the stored results leave out: for the truth's class and its main competitors, which generation lines
proposed them (and at which pass), their priors, and each winnowing term's contribution (twin, graph consistency,
health levels, each check); the lines that abstained; each pass with its trigger and yield; the checks with their
verdicts on the truth. Nothing in the frozen code is changed: the loop's run is wrapped in this process only.

Run from the project root (no API calls):
    python scripts/analysis_provenance.py --seeds 2000-2159 --workers 6
Writes results/analysis/provenance/s<seed>.json; resumable. With --row-out DIR it also writes the arm's result rows
(DIR/<arm>/s<seed>.json, identical to scripts/exp2_run.py's), so arm A need not be run twice. Not part of the frozen experiment (an analysis tool).
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


def _digest(loop) -> dict:
    """Ledger internals at the end of the run, compact, in ledger keys (mapped to world keys by the caller)."""
    led = loop.ledger
    snap = led.snapshot()
    post = snap["posterior"]
    return {"posterior": post, "priors": snap["priors"],
            "terms": [{"id": t["id"], "tool": t["tool"], "tau": t["tau_effective"], "contribution": t["contribution"]}
                      for t in snap["terms"]],
            "provenance": {k: [(tool, round(m, 4)) for tool, m, _ in v] for k, v in snap["provenance"].items()}}


def generation_diagnosis(kg, frame, truth, passes: int) -> list[dict]:
    """For each true fault: could generation have proposed it, and where did it fall short? Whether the target is in
    the frame (anchors, affected elements, scope), reachable by the backward walk at the widest pass run, whether its
    mode is documented for the element, what the documentation predicts for it, and how that prediction scores
    against the observations the frame holds."""
    from adagent.tools.walks import AbductionWalk, Observations, _modes, backward_candidates, score_prediction
    abd = AbductionWalk(kg)
    obs = Observations(frame, kg)
    widest = set(backward_candidates(obs, abd.prop, max_elements=400 * 2 ** max(passes - 1, 0))) if obs.O else set()
    out = []
    for f in truth:
        for t in f["targets"]:
            el = t if kg.has(t) else None
            d = {"mode": f["mode"], "target": t, "in_graph": el is not None,
                 "anchor": t in frame.anchors, "affected": t in frame.affected_elements, "in_scope": t in frame.scope,
                 "observations": len(obs.O), "reachable_by_walk": t in widest}
            if el is not None:
                d["documented_for_element"] = f["mode"] in set(_modes(abd.prop, el))
                P = abd.prop.predict(f["mode"], el)
                d["predicted_effects"] = len(P) if P else 0
                d["transient_prediction"] = bool(getattr(P, "transient", False)) if P else None
                if P and obs.O:
                    d["score"] = score_prediction(P, obs)
            out.append(d)
    return out


def job(args):
    seed, day, outdir, arm, row_out = args
    f = Path(outdir) / f"s{seed}.json"
    try:
        from adagent.harness import exp2
        from adagent.tools import loop as loopmod
        orig = loopmod.WinnowingLoop.run
        holder = {}

        def run(self):
            rec = orig(self)
            holder["digest"] = _digest(self)
            holder["kg"] = self.kg
            holder["ledger"] = self.ledger
            holder["frame"] = self.frame
            holder["passes"] = len(self.record["passes"])
            return rec
        loopmod.WinnowingLoop.run = run
        exp2.WinnowingLoop.run = run
        row = exp2.run_job(arm, day, seed)
        if row_out:                     # the arm's own result, as scripts/exp2_run.py writes it (the run is the same)
            (Path(row_out) / arm).mkdir(parents=True, exist_ok=True)
            (Path(row_out) / arm / f"s{seed}.json").write_text(json.dumps(row, default=str))
        dg, kg, led = holder["digest"], holder["kg"], holder["ledger"]
        sc = row["score"]
        whole = set(sc["class_whole"])
        comps = [set(c) for c in sc["class_components"]]
        world = {k: exp2.cause_keys(kg, led.hypothesis(k)) for k in dg["posterior"] if k != "NONE_OF_THESE"}

        def in_truth(k):
            ws = world.get(k, [])
            return any(w in whole for w in ws) or any(w in c for c in comps for w in ws)
        post = dg["posterior"]
        truth_keys = [k for k in post if k != "NONE_OF_THESE" and in_truth(k)]
        rivals = [k for k, _ in sorted(post.items(), key=lambda kv: -kv[1])
                  if k != "NONE_OF_THESE" and k not in truth_keys][:5]
        best_truth = max(truth_keys, key=lambda k: post[k]) if truth_keys else None

        def contribs(k):
            return {t["id"]: round(t["contribution"].get(k, 0.0), 3) for t in dg["terms"] if k in t["contribution"]}
        keep = ([best_truth] if best_truth else []) + rivals + ["NONE_OF_THESE"]
        out = {"seed": seed, "case": row["case"], "kind": row["labels"]["kind"], "labels": row["labels"],
               "truth": row["truth"], "outcome": sc["outcome"], "effort_h": sc["effort_h"],
               "truth_present": sc["truth_present"], "separable": sc.get("separable"),
               "passes": row["loop"]["passes"], "abstained": row["loop"].get("abstained"),
               "returns": row["loop"]["returns"], "stop_reason": row["loop"].get("stop_reason"),
               "truth_members": [{"key": k, "posterior": round(post[k], 5), "prior": round(dg["priors"].get(k, 0), 5),
                                  "proposed_by": dg["provenance"].get(k, [])} for k in truth_keys],
               "best_truth": best_truth, "rivals": rivals,
               "none_posterior": round(post.get("NONE_OF_THESE", 0.0), 5),
               "contributions": {k: contribs(k) for k in keep},
               "priors": {k: round(dg["priors"].get(k, 0), 5) for k in keep},
               "rival_provenance": {k: dg["provenance"].get(k, []) for k in rivals},
               "checks": [{"check": c["check"], "params": c["params"], "status": c["status"],
                           "verdict_on_truth": {k: v for k, v in (c.get("verdicts") or {}).items() if k in truth_keys}}
                          for c in row["loop"]["checks"]],
               "lines_run": sorted({t.split(".")[0] for v in dg["provenance"].values() for t, _ in v})}
        out["generation"] = generation_diagnosis(kg, holder["frame"], row["truth"], holder["passes"])
        out["all_world_keys"] = world
        out["all_contributions"] = {t["id"]: {k: round(v, 3) for k, v in t["contribution"].items()}
                                    for t in dg["terms"]}
        out["all_priors"] = {k: round(v, 6) for k, v in dg["priors"].items()}
        out["all_provenance"] = dg["provenance"]
        f.write_text(json.dumps(out, default=str))
        return f"s{seed} {row['case']:<28} {sc['outcome']:<26} truth members {len(truth_keys)}"
    except Exception as e:  # noqa: BLE001
        import traceback
        f.with_suffix(".error.json").write_text(json.dumps({"seed": seed, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"s{seed} FAILED {repr(e)[:120]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--day", type=float, default=2190.0)
    ap.add_argument("--arm", default="A")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="results/analysis/provenance")
    ap.add_argument("--row-out", default=None,
                    help="also write the arm's result rows under this results directory (<dir>/<arm>/s<seed>.json)")
    a = ap.parse_args()
    import os
    os.environ.setdefault("EXP2_FIT_CACHE", str(ROOT / "results" / "_fitcache"))
    outdir = ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    row_out = str(ROOT / a.row_out) if a.row_out else None
    jobs = [(s, a.day, str(outdir), a.arm, row_out) for s in seeds_arg(a.seeds)
            if not (outdir / f"s{s}.json").exists()
            or (row_out and not (Path(row_out) / a.arm / f"s{s}.json").exists())]
    print(f"{len(jobs)} cases on {a.workers} workers", flush=True)
    t0 = time.time()
    with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=4) as pool:
        for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
            print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] {line}", flush=True)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
