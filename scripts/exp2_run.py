"""Experiment 2: run arms on cases in parallel, resumably (spec/PROTOCOL_EXP2_WINNOWING.md).

Run from the project root, for example:
    python scripts/exp2_run.py --arms A,A-once,A-perfect,A-rc,A-no-twin,A-no-c1,B,B-graph --seeds 2000-2159 --workers 6
    python scripts/exp2_run.py --arms L --seeds 2000-2159 --workers 1 --only-planned

Each job (one arm on one case) writes results/exp2/<arm>/s<seed>.json; jobs already written are skipped, so the
same command resumes a run that stopped. Failures are written as s<seed>.error.json and retried on the next run.
--only-planned restricts an arm to the cases where arm A did not commit at once (the orchestrator is called only
there; elsewhere arm L equals arm A by construction). Arm L calls the OpenAI API (or, with --provider and --model,
another provider or model: the model-strength comparison); the others make no API calls.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_client = None


def seeds_arg(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def job(args):
    arm, day, seed, outdir = args
    from adagent.harness.exp2 import run_job
    global _client
    f = Path(outdir) / arm / f"s{seed}.json"
    t0 = time.time()
    try:
        from adagent.harness.exp2 import ARMS
        uses_llm = arm == "L" or arm.startswith("LLM-") or ARMS[arm].get("planner") == "llm" or ARMS[arm].get("framer") == "llm"
        if uses_llm and _client is None:
            import os
            from adagent.llm.client import LLMClient
            _client = LLMClient(os.environ.get("EXP2_LLM_PROVIDER", "openai"), os.environ.get("EXP2_LLM_MODEL") or None)
        row = run_job(arm, day, seed, client=_client)
        if uses_llm:
            row["llm"] = {"provider": _client.provider, "model": _client.model}
        f.write_text(json.dumps(row, default=str))
        err = f.with_suffix(".error.json")
        if err.exists():
            err.write_text("")            # superseded by a successful run (files are not deleted)
        sc = row["score"]
        return f"{arm:<10} s{seed}  {row['case']:<28} {sc['outcome']:<26} effort={sc['effort_h']:.1f}h  {time.time() - t0:.0f}s"
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"arm": arm, "seed": seed, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"{arm:<10} s{seed}  FAILED {repr(e)[:120]}"


def planned_seeds(outdir: Path, seeds: list[int], plan_arm: str = "A") -> list[int]:
    out = []
    for s in seeds:
        f = outdir / plan_arm / f"s{s}.json"
        if not f.exists():
            continue
        r = json.loads(f.read_text())
        loop = r.get("loop", {})
        if loop.get("plans") or loop.get("checks") or loop.get("outcome") != "commit":
            out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", required=True)
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--day", type=float, default=2190.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="results/exp2")
    ap.add_argument("--only-planned", action="store_true")
    ap.add_argument("--provider", default="openai", choices=["openai", "deepseek"],
                    help="arm L only: the orchestrator's provider")
    ap.add_argument("--model", default=None, help="arm L only: the orchestrator's model (default: the provider's)")
    ap.add_argument("--plan-arm", default="A", help="with --only-planned: the arm whose planned cases are taken "
                    "(A for arm L; A-full-cd for L-full)")
    ap.add_argument("--plan-from", default=None,
                    help="results directory whose arm A defines the planned cases (default: --out)")
    a = ap.parse_args()
    import os
    os.environ["EXP2_LLM_PROVIDER"] = a.provider          # read by the worker processes (spawned after this)
    os.environ.setdefault("EXP2_FIT_CACHE", str(ROOT / "results" / "_fitcache"))   # equivalence fits, shared by arms
    if a.model:
        os.environ["EXP2_LLM_MODEL"] = a.model
    outdir = ROOT / a.out
    seeds = seeds_arg(a.seeds)
    # the third case set (spec/PROTOCOL_CAMPAIGN.md, section 2) is sealed until the record run
    sealed = [s for s in seeds if 4000 <= s <= 4319]
    if sealed and os.environ.get("ADAGENT_RECORD_RUN") != "1":
        raise SystemExit(f"seeds 4000-4319 are sealed until the record run ({len(sealed)} requested); "
                         "set ADAGENT_RECORD_RUN=1 only for the frozen agent's record run")
    # the fourth case set (amendment 8) is sealed until the record run of the agent frozen under C4
    sealed2 = [s for s in seeds if 7000 <= s <= 7319]
    if sealed2 and os.environ.get("ADAGENT_RECORD_RUN_C4") != "1":
        raise SystemExit(f"seeds 7000-7319 are sealed until the C4 record run ({len(sealed2)} requested); "
                         "set ADAGENT_RECORD_RUN_C4=1 only for that run")
    jobs = []
    for arm in a.arms.split(","):
        (outdir / arm).mkdir(parents=True, exist_ok=True)
        arm_seeds = planned_seeds(ROOT / (a.plan_from or a.out), seeds, a.plan_arm) if a.only_planned else seeds
        for s in arm_seeds:
            if not (outdir / arm / f"s{s}.json").exists():
                jobs.append((arm, a.day, s, str(outdir)))
    print(f"{len(jobs)} jobs to run on {a.workers} workers", flush=True)
    t0 = time.time()
    if a.workers <= 1:
        for i, j in enumerate(jobs, 1):
            print(f"[{i}/{len(jobs)}] " + job(j), flush=True)
    else:
        with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=4) as pool:
            for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
                print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] " + line, flush=True)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
