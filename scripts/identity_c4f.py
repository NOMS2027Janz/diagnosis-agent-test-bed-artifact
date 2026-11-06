"""Amendment 13's identity check: the agent with the advisor (A-full-cd) must reproduce, under the refactored
advisor, the records the frozen C4 code wrote for the same development cases (results/c4d/A-full-cd), exactly,
apart from timings. Exit status 0 when every case matches.

Run from the project root:   python scripts/identity_c4f.py --seeds 2004,2013,2017,2021,2028,2029 --workers 6
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SKIP = ("seconds", "timing", "agent_seconds", "twin_cost", "llm_calls")


def strip(o):
    if isinstance(o, dict):
        # the peel record's "worst" lists the twin's residual items by cost; items tied at the cost cap come from a
        # set and their order varies with the process's string hashing (PYTHONHASHSEED), in the frozen code too
        return {k: (sorted(strip(v), key=str) if k == "worst" else strip(v)) for k, v in o.items() if k not in SKIP}
    if isinstance(o, list):
        return [strip(x) for x in o]
    return o


def one(seed: int):
    from adagent.harness import exp2
    row = json.loads(json.dumps(exp2.run_job("A-full-cd", 2190.0, seed), default=str))
    ref = json.loads((ROOT / "results" / "c4d" / "A-full-cd" / f"s{seed}.json").read_text())
    a, b = strip({"score": row["score"], "loop": row["loop"]}), strip({"score": ref["score"], "loop": ref["loop"]})
    if a == b:
        return seed, "identical", ""
    diff = [k for k in set(a["loop"]) | set(b["loop"]) if a["loop"].get(k) != b["loop"].get(k)]
    if a["score"] != b["score"]:
        diff.append("score")
    return seed, "DIFFERS", ",".join(sorted(diff))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",")]
    with mp.get_context("spawn").Pool(a.workers) as pool:
        res = pool.map(one, seeds)
    for s, v, d in res:
        print(f"identity s{s}: {v} {d}", flush=True)
    sys.exit(0 if all(v == "identical" for _, v, _ in res) else 1)


if __name__ == "__main__":
    main()
