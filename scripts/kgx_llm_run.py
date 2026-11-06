"""The LLM proposer where the documentation is missing fault knowledge (spec/PROTOCOL_CAMPAIGN.md, amendment 14).

Question: does the agent's LLM proposer recover diagnoses that the documentation-driven lines lose when the
documentation names a fault mode but gives none of its effects? The coded lines reason from documented effects and
cannot connect symptoms to such a mode; the LLM knows what such a fault does, and grounding accepts the mode because
it is documented. Where a mode is missing from the documentation altogether, grounding rejects it too, so no gain is
expected there (the control of the argument).

Arms (each on the diagnosis cases 2000-2159, as the original degradation study, under the C4E code):
  A-full-cd-kg-sig        the agent, documentation with 30% of fault signatures removed (scripts/kgx_run.py, kind sig)
  A-full-cd-llm-kg-sig    the same with the LLM proposer added as a generation line (scripts/llmline_run.py's line)
  A-full-cd-kg-modes      the agent, 20% of fault modes removed from the documentation
  A-full-cd-llm-kg-modes  the same with the LLM proposer
The proposer sees the degraded documentation, as every other line does: it is built on the agent's side of the
case (the degraded graph and the frame built from it). Nothing else changes.

Run from the project root:
    python scripts/kgx_llm_run.py --arms A-full-cd-llm-kg-sig --seeds 2000-2159 --workers 6
--fake-empty makes the proposer abstain on every case: an arm A-full-cd-llm-kg-<kind> must then reproduce
A-full-cd-kg-<kind> exactly (the wrapper's control, run without API calls).
Each job writes results/campaign/kgx_c4/<arm>/s<seed>.json; finished jobs are skipped, so the command resumes.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import kgx_run  # noqa: E402  (the degradations and their wrapper, unchanged)
import llmline_run  # noqa: E402  (the proposer's client and its replay stand-in, unchanged)

_client = None


def split(arm: str) -> tuple[bool, str]:
    base, _, kind = arm.rpartition("-kg-")
    assert kind in ("sig", "modes", "none") and base in ("A-full-cd", "A-full-cd-llm"), arm
    return base == "A-full-cd-llm", kind


def install_llm():
    """Add the LLM proposer, built on the agent's side of the case (the degraded graph), to the generation lines."""
    from adagent.harness import exp2
    from adagent.tools.grounding import GroundingTool
    from adagent.tools.proposer_llm import LLMProposer
    orig = exp2.lines_for          # already the degradation wrapper's

    def lines_for(case, *a, **k):
        lines = orig(case, *a, **k)
        kgd = kgx_run._CUR["kgd"]
        prop = LLMProposer(_client, kgd, GroundingTool(kgd))
        exp2._llm_line = prop
        return {**lines, "llm_proposer": prop}
    exp2.lines_for = lines_for


def job(args):
    arm, day, seed, outdir, fake_empty = args
    global _client
    with_llm, kind = split(arm)
    f = Path(outdir) / arm / f"s{seed}.json"
    t0 = time.time()
    try:
        from adagent.harness import exp2
        if not getattr(exp2, "_kgx", None):
            kgx_run.install(kind)
            exp2._kgx = kind
            if with_llm:
                if fake_empty:
                    _client = llmline_run.FakeClient(None)
                else:
                    from adagent.llm.client import LLMClient
                    _client = LLMClient(os.environ.get("LLMLINE_PROVIDER", "openai"),
                                        os.environ.get("LLMLINE_MODEL") or None)
                install_llm()
        assert exp2._kgx == kind, "one degradation per worker process"
        llmline_run._seed = seed
        n0 = len(_client.records()) if _client is not None else 0
        row = exp2.run_job("A-full-cd", day, seed)
        row["arm"] = arm
        row["kgx"] = {"kind": kind, "report": kgx_run._CUR.get("rep")}
        if with_llm:
            prop = getattr(exp2, "_llm_line", None)
            row["llm_proposer"] = prop.trace if prop is not None else None
            row["llm"] = {"provider": _client.provider, "model": _client.model}
            row["llm_calls"] = [{k: c.get(k) for k in ("role", "latency_s", "input_tokens", "output_tokens",
                                                       "reasoning_tokens", "ok", "error")}
                                for c in _client.records()[n0:]]
        kgx_run._CUR.clear()
        f.write_text(json.dumps(row, default=str))
        sc = row["score"]
        return f"{arm:<22} s{seed}  {row['case']:<28} {sc['outcome']:<26} effort={sc['effort_h']:.1f}h  {time.time() - t0:.0f}s"
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"arm": arm, "seed": seed, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"{arm:<22} s{seed}  FAILED {repr(e)[:120]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", required=True)
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--day", type=float, default=2190.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="results/campaign/kgx_c4")
    ap.add_argument("--fake-empty", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("EXP2_FIT_CACHE", str(ROOT / "results" / "_fitcache"))
    outdir = ROOT / a.out
    seeds = kgx_run.seeds_arg(a.seeds)
    if any(7000 <= s <= 7319 for s in seeds) or any(4000 <= s <= 4319 for s in seeds):
        raise SystemExit("sealed seeds are not used here")
    t0 = time.time()
    for arm in a.arms.split(","):          # one arm at a time: a worker process carries one degradation
        split(arm)
        (outdir / arm).mkdir(parents=True, exist_ok=True)
        jobs = [(arm, a.day, s, str(outdir), a.fake_empty) for s in seeds
                if not (outdir / arm / f"s{s}.json").exists()]
        print(f"{arm}: {len(jobs)} jobs to run on {a.workers} workers", flush=True)
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
