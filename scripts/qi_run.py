"""Amendment 16, the question-interpretation test: can the structured question the agent starts from be read from
a question as people put it, and is a coded reader adequate or is an LLM needed?

For each case (seeds given; the sealed cases 7000-7319 under the record-run flag), the case's templated question
(kind, anchor, reported time) is the truth. Two free-text versions are made:
  controlled: harness/rephrase.py's hand-written rewrite (a fixed vocabulary of forms);
  free:       a paraphrase written by a different model (DeepSeek, deepseek-chat), given only the facts needed to
              keep the question resolvable from the network's records, and told to write as an operator or a
              customer would.
Each text is read by the LLM reader (tools/question_llm.py, the study's model, with four record lookups) and by the
coded reader (tools/question_llm.coded_read). A reading is right when the kind and the anchor match and the time is
within 300 s. Writes results/campaign/c4/qi/s<seed>.json; finished seeds are skipped.

Run from the project root:   ADAGENT_RECORD_RUN_C4=1 python scripts/qi_run.py --seeds 7000-7319 --workers 6
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

from pydantic import BaseModel  # noqa: E402

OUT = ROOT / "results" / "campaign" / "c4" / "qi"
PARA_VERSION = "paraphrase-0.1"
PARA_SYSTEM = """You write the message that a network operations engineer or a customer would actually send about \
a problem on an optical transport network, given the facts of the situation. Write one message, as that person \
would: informal or terse is fine, a ticket, a chat line or a phone note. Use a clock time (hh:mm, today) instead of \
seconds. Refer to the service or equipment either by its identifier or by the descriptive facts given, but the \
message must let someone with the network records identify exactly which service or element is meant. Do not \
guess or mention a cause. Do not add facts you were not given."""


class Para(BaseModel):
    message: str


_clients = {}


def facts_for(q, net, now_s):
    from adagent.harness.rephrase import clock
    f = {"who_asks": "a customer" if q.kind == "customer" else "network operations staff",
         "what_was_reported_in_system_terms": q.text.replace("t=", "simulation time t="),
         "clock": "simulation time t = 0 s is 14:00:00 today; time of asking " + clock(now_s),
         "reported_time_on_clock": clock(q.reported_at_s)}
    a = q.anchor
    if a in net.services:
        s = net.services[a]
        same = [x for x in net.services.values() if x.rate == s.rate and {x.src, x.dst} == {s.src, s.dst}]
        f["subject"] = {"service_id": a, "rate": s.rate, "end_sites": [s.src, s.dst],
                        "unique_by_rate_and_sites": len(same) == 1}
    elif a in net.amps:
        oms, n = a.rsplit(".amp", 1)
        sa, sb = oms[4:].split("-")
        f["subject"] = {"element_id": a, "kind": "optical amplifier", "section": f"from {sa} to {sb}",
                        "position_counting_from_first_site": int(n) + 1, "is_booster": n == "0"}
    elif a in net.transponders:
        och, end = a.split(".")[1], a.split(".")[2]
        lp = net.lightpaths[och]
        f["subject"] = {"element_id": a, "kind": "transponder (receiver end)", "wavelength": och,
                        "site": lp.nodes[0] if end == "a" else lp.nodes[-1]}
    else:
        f["subject"] = {"element_id": a}
    return f


def job(args):
    seed, = args
    f = OUT / f"s{seed}.json"
    t0 = time.time()
    try:
        from adagent.harness import exp2
        from adagent.harness.rephrase import rewrite
        from adagent.llm.client import LLMClient
        from adagent.tools.question_llm import LLMQuestionReader, coded_read
        if not _clients:
            _clients["reader"] = LLMClient("openai", os.environ.get("QI_MODEL") or None)
            _clients["para"] = LLMClient("deepseek", os.environ.get("QI_PARA_MODEL") or None)
        case = exp2.make_case2(2190.0, seed, gap_scale=1.0, records_complete=False, procedures_in_graph=False,
                               gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
        q, net, now = case.question, case.world.net, case.substrate.now
        ctl = rewrite(q, net, now, seed)
        fx = facts_for(q, net, now)
        para = _clients["para"].parse("paraphrase", PARA_VERSION, PARA_SYSTEM, json.dumps(fx), Para).message
        truth = {"kind": q.kind, "anchor": q.anchor, "reported_at_s": q.reported_at_s}
        row = {"seed": seed, "case": case.id, "truth": truth, "original": q.text, "facts": fx,
               "texts": {"controlled": ctl.text, "free": para}, "controlled_form": ctl.form, "readings": {}}

        def right(r):
            return (r.get("kind") == q.kind and r.get("anchor") == q.anchor
                    and r.get("reported_at_s") is not None and abs(float(r["reported_at_s"]) - q.reported_at_s) <= 300)
        for name, text in row["texts"].items():
            c = coded_read(text, net, now)
            rd = LLMQuestionReader(_clients["reader"], net)
            try:
                lr = rd.read(text, now, ctl.now_clock).model_dump()
            except Exception as e:  # noqa: BLE001
                lr = {"error": repr(e)[:300]}
            row["readings"][name] = {"coded": c, "coded_right": right(c), "llm": lr, "llm_right": right(lr),
                                     "llm_lookups": rd.trace}
        f.write_text(json.dumps(row, default=str))
        r = row["readings"]
        return (f"qi s{seed}  controlled coded={r['controlled']['coded_right']} llm={r['controlled']['llm_right']}  "
                f"free coded={r['free']['coded_right']} llm={r['free']['llm_right']}  {time.time() - t0:.0f}s")
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"seed": seed, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"qi s{seed}  FAILED {repr(e)[:120]}"


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
    print(f"qi: {len(jobs)} jobs to run on {a.workers} workers", flush=True)
    t0 = time.time()
    with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=8) as pool:
        for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
            print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] " + line, flush=True)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
