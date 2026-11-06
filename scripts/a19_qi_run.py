"""Amendment 19, the question-interpretation test with the reported time defined (spec/memos/AMENDMENT_19.md).

The structured question's reported_at_s means the stated start for an event (a loss of service, an alarm, a change
since a time) and the time of asking for a gradual trend. Amendments 16 and 17 asked the LLM reader for the field
itself, which no single instruction can define for both. Here the reader extracts only what the text states (who
asks, the anchor, whether the problem is an event or a gradual trend, and an event's start time) and a fixed rule in
code forms reported_at_s: the stated start for an event, else the time of asking (the harness's own convention).
The coded reader, the controlled rewrites, the paraphraser (DeepSeek, prompt as amendment 16), the lookups and the
scoring are unchanged. The frozen modules are imported unchanged.

Sets: --set dev (development, cases 3000-3079) or --set test (the diagnosis cases 2000-2319, never examined for
this test). Writes results/campaign/qi19/<set>/s<seed>.json; finished seeds are skipped.

Run from the project root:   python scripts/a19_qi_run.py --set dev --seeds 3000-3079 --workers 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Literal, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "results" / "campaign" / "qi19"
PROMPT_VERSION_19 = "question-reader-a19"
SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SETS = {"dev": (3000, 3079), "test": (2000, 2319)}
_clients = {}

OLD = ("reported_at_s (when the problem was noticed or began, in seconds of the system clock: t = 0 s is 14:00:00 "
       "today; when the question gives no time, use the time of asking).")
NEW = ("problem (\"event\" when the question says or implies that the problem began at a particular moment: a loss "
       "of service, an alarm, a reading or quality that changed or stopped changing since a time; \"trend\" when it "
       "describes a quantity changing gradually over hours or days); and started_at_s (for an event, when it began "
       "or was first seen, in seconds of the system clock: t = 0 s is 14:00:00 today; the time the problem began, "
       "not the time a ticket was raised or the question was asked; null for a trend or when the question gives no "
       "time).")


def system_19():
    from adagent.tools import question_llm as Q
    flat = " ".join(Q.SYSTEM.split())
    assert " ".join(OLD.split()) in flat, "frozen prompt text not found"
    return flat.replace(" ".join(OLD.split()), NEW)


def schema_19():
    from pydantic import BaseModel

    class ReadQuestion19(BaseModel):
        kind: Literal["customer", "operations"]
        anchor: str
        problem: Literal["event", "trend"]
        started_at_s: Optional[float]
        note: str
    return ReadQuestion19


def reported_at(read: dict, now_s: float) -> float:
    """The convention, in code: an event's stated start, else the time of asking."""
    if read.get("problem") == "event" and read.get("started_at_s") is not None:
        return float(read["started_at_s"])
    return float(now_s)


def read_19(client, net, text, now_s, now_clock):
    from adagent.tools.question_llm import MAX_TURNS, TOOLS, Lookups
    look, trace = Lookups(net), []
    session = client.tool_session("question_reader", PROMPT_VERSION_19, system_19(), TOOLS)
    calls = session.start(json.dumps({"question": text, "time_of_asking": now_clock, "time_of_asking_s": now_s}))
    for turn in range(MAX_TURNS):
        if not calls:
            break
        res = {}
        for c in calls:
            try:
                args = json.loads(c.arguments or "{}")
                r = look.call(c.name, args)
            except Exception as e:  # noqa: BLE001
                args, r = {}, {"error": repr(e)[:200]}
            trace.append({"turn": turn, "tool": c.name, "args": args})
            res[c.id] = json.dumps(r)
        calls = session.submit(res)
    out = session.finish("Now give the structured question.", schema_19()).model_dump()
    out["reported_at_s"] = reported_at(out, now_s)
    return out, trace


def job(args):
    seed, set_name = args
    f = OUT / set_name / f"s{seed}.json"
    t0 = time.time()
    try:
        import qi_run
        from adagent.harness import exp2
        from adagent.harness.rephrase import rewrite
        from adagent.llm.client import LLMClient
        from adagent.tools.question_llm import coded_read
        if not _clients:
            _clients["reader"] = LLMClient("openai", os.environ.get("QI_MODEL") or None)
            _clients["para"] = LLMClient("deepseek", os.environ.get("QI_PARA_MODEL") or None)
        case = exp2.make_case2(2190.0, seed, gap_scale=1.0, records_complete=False, procedures_in_graph=False,
                               gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
        q, net, now = case.question, case.world.net, case.substrate.now
        ctl = rewrite(q, net, now, seed)
        fx = qi_run.facts_for(q, net, now)
        para = _clients["para"].parse("paraphrase", qi_run.PARA_VERSION, qi_run.PARA_SYSTEM, json.dumps(fx),
                                      qi_run.Para).message
        truth = {"kind": q.kind, "anchor": q.anchor, "reported_at_s": q.reported_at_s}
        row = {"seed": seed, "case": case.id, "set": set_name, "truth": truth, "original": q.text, "facts": fx,
               "texts": {"controlled": ctl.text, "free": para}, "controlled_form": ctl.form, "readings": {},
               "prompt_version": PROMPT_VERSION_19, "script_sha256": SELF_SHA}

        def parts(r):
            return {"kind": r.get("kind") == q.kind, "anchor": r.get("anchor") == q.anchor,
                    "time": r.get("reported_at_s") is not None and abs(float(r["reported_at_s"]) - q.reported_at_s) <= 300}
        for name, text in row["texts"].items():
            c = coded_read(text, net, now)
            try:
                lr, tr = read_19(_clients["reader"], net, text, now, ctl.now_clock)
            except Exception as e:  # noqa: BLE001
                lr, tr = {"error": repr(e)[:300]}, []
            pc, pl = parts(c), parts(lr)
            row["readings"][name] = {"coded": c, "coded_parts": pc, "coded_right": all(pc.values()),
                                     "llm": lr, "llm_parts": pl, "llm_right": all(pl.values()), "llm_lookups": tr}
        f.write_text(json.dumps(row, default=str))
        r = row["readings"]
        return (f"qi19 s{seed}  controlled coded={r['controlled']['coded_right']} llm={r['controlled']['llm_right']}  "
                f"free coded={r['free']['coded_right']} llm={r['free']['llm_right']}  {time.time() - t0:.0f}s")
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"seed": seed, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"qi19 s{seed}  FAILED {repr(e)[:120]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, choices=list(SETS))
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    lo, hi = SETS[a.set]
    seeds = []
    for part in a.seeds.split(","):
        x, _, y = part.partition("-")
        seeds += list(range(int(x), int(y) + 1)) if y else [int(x)]
    assert all(lo <= s <= hi for s in seeds), f"set {a.set} is seeds {lo}-{hi}"
    (OUT / a.set).mkdir(parents=True, exist_ok=True)
    jobs = [(s, a.set) for s in seeds if not (OUT / a.set / f"s{s}.json").exists()]
    print(f"qi19 {a.set}: {len(jobs)} jobs to run on {a.workers} workers", flush=True)
    t0 = time.time()
    with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=8) as pool:
        for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
            print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] " + line, flush=True)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
