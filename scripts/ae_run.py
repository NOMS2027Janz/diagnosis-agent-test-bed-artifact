"""Amendment 16, the answer-expression test: the agent's result written for an operator by an LLM, checked for
faithfulness by code, beside a coded template written from the same summary.

For each seed, the agent's record from its sealed run (results/campaign/c4/A-full-cd/s<seed>.json) is summarised
(tools/answer_llm.summarise) with the case's question; the LLM writes the report and states its conclusion and the
facts it used; code checks the conclusion against the ledger's, every identifier against the summary and every
cited fact against its field. The template report is written from the same summary. Writes
results/campaign/c4/ae/s<seed>.json; finished seeds are skipped. A blind pair (LLM and template, in random order)
for people to rate is written to results/campaign/c4/ae/blind.json at the end.

Run from the project root:   ADAGENT_RECORD_RUN_C4=1 python scripts/ae_run.py --seeds 7000-7079 --workers 6
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
C4 = ROOT / "results" / "campaign" / "c4"
OUT = C4 / "ae"
_client = {}


def job(args):
    seed, = args
    f = OUT / f"s{seed}.json"
    t0 = time.time()
    try:
        from adagent.harness import exp2
        from adagent.llm.client import LLMClient
        from adagent.tools.answer_llm import PROMPT_VERSION, SYSTEM, Report, check, summarise, template
        if not _client:
            _client["w"] = LLMClient("openai", os.environ.get("AE_MODEL") or None)
        rec = json.loads((C4 / "A-full-cd" / f"s{seed}.json").read_text())
        case = exp2.make_case2(2190.0, seed, gap_scale=1.0, records_complete=False, procedures_in_graph=False,
                               gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
        summ = summarise(case.question.text, rec["loop"], rec["score"]["effort_h"], rec["score"]["site_visits"])
        rep = _client["w"].parse("answer_writer", PROMPT_VERSION, SYSTEM, json.dumps(summ, default=str), Report)
        row = {"seed": seed, "case": rec["case"], "outcome": rec["score"]["outcome"], "summary": summ,
               "llm": rep.model_dump(), "check": check(rep, summ), "template": template(summ)}
        f.write_text(json.dumps(row, default=str))
        return f"ae s{seed}  faithful={row['check']['faithful']}  {time.time() - t0:.0f}s"
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"seed": seed, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"ae s{seed}  FAILED {repr(e)[:120]}"


def blind(seeds):
    rng = random.Random(16)
    items = []
    for s in seeds:
        p = OUT / f"s{s}.json"
        if not p.exists():
            continue
        r = json.loads(p.read_text())
        pair = [("llm", r["llm"]["report"]), ("template", r["template"])]
        rng.shuffle(pair)
        items.append({"seed": s, "question": r["summary"]["question"], "A": pair[0][1], "B": pair[1][1],
                      "key": {"A": pair[0][0], "B": pair[1][0]}})
    (OUT / "blind.json").write_text(json.dumps(items, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    seeds = []
    for part in a.seeds.split(","):
        x, _, y = part.partition("-")
        seeds += list(range(int(x), int(y) + 1)) if y else [int(x)]
    jobs = [(s,) for s in seeds if not (OUT / f"s{s}.json").exists()]
    print(f"ae: {len(jobs)} jobs to run on {a.workers} workers", flush=True)
    t0 = time.time()
    with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=8) as pool:
        for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
            print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] " + line, flush=True)
    blind(seeds)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
