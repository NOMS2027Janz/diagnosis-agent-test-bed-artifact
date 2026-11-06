"""Amendment 17, the answer-expression test rerun (corrects amendment 16's ae).

Two changes from amendment 16, nothing else:
  1. The summary the report is written from carries the runners-up (the six leading hypotheses of the final ledger
     with their probabilities, "none of these" included) and how sure the ledger is of what the fault is and of where
     it is, separately as well as together (standing rule: every answer reports the runners-up, and cause and place
     separately). The writer is told to report both; the coded template reports both.
  2. The faithfulness check compares a cited fact with its field after parsing JSON, so that a value cited in compact
     JSON matches the same value stored with spaces (amendment 16's checker flagged 3 faithful reports).
The conclusion check and the identifier check are unchanged. The frozen modules (tools/answer_llm.py) are imported
unchanged. Writes results/campaign/c4/ae17/s<seed>.json and blind.json; finished seeds are skipped.

Run from the project root:   ADAGENT_RECORD_RUN_C4=1 python scripts/a17_ae_run.py --seeds 7000-7079 --workers 3
"""
from __future__ import annotations

import argparse
import hashlib
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
OUT = C4 / "ae17"
PROMPT_VERSION_17 = "answer-writer-a17"
SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_client = {}

ADD = (" Always give the runners-up from the summary's runners_up list with their probabilities, including \"none "
       "of these\" when it is listed, and say separately how sure the system is of what the fault is "
       "(cause_and_place.what) and of where it is (cause_and_place.where). Keep it under 220 words.")


def system_17():
    from adagent.tools.answer_llm import SYSTEM
    s = " ".join(SYSTEM.split())
    k = "Keep it under 180 words."
    assert k in s, "frozen prompt text not found"
    return s.replace(" " + k, ADD)


def parts(h):
    """'A@el:x()+B@el:y()' -> (frozenset of modes, frozenset of elements)."""
    if h == "NONE_OF_THESE":
        return None
    modes, els = [], []
    for c in h.split("+"):
        m, _, e = c.partition("@")
        modes.append(m)
        els.append(e.replace("el:", "").replace("()", ""))
    return frozenset(modes), frozenset(els)


def readable(h):
    return h.replace("el:", "").replace("()", "")


def summarise_17(question_text, loop, effort_h, visits):
    from adagent.tools.answer_llm import summarise
    s = summarise(question_text, loop, effort_h, visits)
    traj = loop.get("trajectory") or []
    top = traj[-1]["top"] if traj else []
    s["runners_up"] = [{"hypothesis": readable(h) if h != "NONE_OF_THESE" else "none of these",
                        "probability": round(float(p), 3)} for h, p in top]
    named = [(parts(h), float(p)) for h, p in top if h != "NONE_OF_THESE"]
    if named:
        (lm, le), lp = named[0]
        s["cause_and_place"] = {
            "basis": "the six leading hypotheses of the final ledger",
            "leader": readable(top[0][0]) if top[0][0] != "NONE_OF_THESE" else readable([h for h, _ in top if h != "NONE_OF_THESE"][0]),
            "what": {"fault_modes": sorted(lm), "probability": round(sum(p for (m, _), p in named if m == lm), 3)},
            "where": {"elements": sorted(le), "probability": round(sum(p for (_, e), p in named if e == le), 3)},
            "both": round(lp, 3)}
    return s


def template_17(summary):
    from adagent.tools.answer_llm import template
    t = template(summary)
    ru = "; ".join(f"{r['hypothesis']} {r['probability']}" for r in summary.get("runners_up", [])[1:])
    cp = summary.get("cause_and_place")
    w = (f" What: {', '.join(cp['what']['fault_modes'])} ({cp['what']['probability']}). Where: "
         f"{', '.join(cp['where']['elements'])} ({cp['where']['probability']}). Both: {cp['both']}.") if cp else ""
    return t + (f" Runners-up: {ru}." if ru else "") + w


def check_17(rep, summary):
    from adagent.tools.answer_llm import check
    out = check(rep, summary)

    def at(path):
        cur = summary
        for p in path.split("."):
            if isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
                cur = cur[int(p)]
            elif isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return None
        return cur

    def found(f):
        v = at(f["field"])
        if v is None:
            return False
        sv = str(f["value"]).strip()
        try:
            if json.loads(sv) == v:
                return True
        except Exception:  # noqa: BLE001
            pass
        return sv in json.dumps(v) or sv == str(v)
    bad = [f for f in out["facts_not_found"] if not found(f)]
    out["facts_not_found_a16_checker"] = out["facts_not_found"]
    out["facts_not_found"] = bad
    out["faithful"] = out["conclusion_matches"] and not out["unsupported_identifiers"] and not bad
    txt = rep.report.lower()
    out["mentions_runners_up"] = ("runner" in txt or "alternative" in txt or "next most likely" in txt
                                  or "none of these" in txt)
    return out


def job(args):
    seed, = args
    f = OUT / f"s{seed}.json"
    t0 = time.time()
    try:
        from adagent.harness import exp2
        from adagent.llm.client import LLMClient
        from adagent.tools.answer_llm import Report
        if not _client:
            _client["w"] = LLMClient("openai", os.environ.get("AE_MODEL") or None)
        rec = json.loads((C4 / "A-full-cd" / f"s{seed}.json").read_text())
        case = exp2.make_case2(2190.0, seed, gap_scale=1.0, records_complete=False, procedures_in_graph=False,
                               gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
        summ = summarise_17(case.question.text, rec["loop"], rec["score"]["effort_h"], rec["score"]["site_visits"])
        rep = _client["w"].parse("answer_writer", PROMPT_VERSION_17, system_17(), json.dumps(summ, default=str), Report)
        row = {"seed": seed, "case": rec["case"], "outcome": rec["score"]["outcome"], "summary": summ,
               "llm": rep.model_dump(), "check": check_17(rep, summ), "template": template_17(summ),
               "prompt_version": PROMPT_VERSION_17, "script_sha256": SELF_SHA}
        f.write_text(json.dumps(row, default=str))
        return (f"ae17 s{seed}  faithful={row['check']['faithful']}  runners-up={row['check']['mentions_runners_up']}"
                f"  {time.time() - t0:.0f}s")
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"seed": seed, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"ae17 s{seed}  FAILED {repr(e)[:120]}"


def blind(seeds):
    rng = random.Random(17)
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
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    seeds = []
    for part in a.seeds.split(","):
        x, _, y = part.partition("-")
        seeds += list(range(int(x), int(y) + 1)) if y else [int(x)]
    jobs = [(s,) for s in seeds if not (OUT / f"s{s}.json").exists()]
    print(f"ae17: {len(jobs)} jobs to run on {a.workers} workers", flush=True)
    t0 = time.time()
    with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=8) as pool:
        for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
            print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] " + line, flush=True)
    blind(seeds)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
