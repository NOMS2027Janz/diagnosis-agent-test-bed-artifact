"""Experiment 1 (generation), LLM arms, on the held-out cases (spec/PROTOCOL_EXP1_GENERATION.md).

Run from the project root, on the laptop:
    python scripts/exp1_llm.py ARM [REPEAT]
ARM is L0 (reference), L1 (procedures from the graph in the brief) or L3 (LLM framer, then the proposer).
REPEAT (default 1) numbers the repeats of L0 (1, 2, 3). L2 needs no calls (re-grounding of L0, done afterwards).

Each run covers seeds 1000 to 1099 at days 730 and 2190 and writes results/exp1/<ARM>_r<REPEAT>.jsonl, one row
per case: the arm, the frozen-manifest digest, the model and prompt versions, the proposals as given, how each
was grounded, every candidate with its prior mass, the residual, tokens, latency and call records (never keys).
It does not score anything: scoring happens after every run is in, against the equivalence classes.
A run can be stopped and started again: cases already in the file are skipped. Arms can run at the same time in
separate Terminal windows.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adagent.harness.cases import make_case                         # noqa: E402
from adagent.llm.client import LLMClient                            # noqa: E402
from adagent.tools.frame import DeterministicFramer                 # noqa: E402
from adagent.tools.frame_llm import PROMPT_VERSION as FRAMER_VERSION, LLMFramer  # noqa: E402
from adagent.tools.grounding import GroundingTool                    # noqa: E402
from adagent.tools.proposer_llm import PROMPT_VERSION, LLMProposer   # noqa: E402

DAYS = (730.0, 2190.0)
SEEDS = range(1000, 1100)
OUT = os.environ.get("EXP1_OUT", "results/exp1")   # amendment 4: EXP1_OUT=results/a4/exp1 for the rerun
# A dry run of the whole pipeline on development cases, never on the held-out ones:
#   EXP1_DRYRUN=1 uses seeds 0 to 4 and writes to results/exp1_dryrun/
if os.environ.get("EXP1_DRYRUN"):
    SEEDS, OUT = range(0, 5), "results/exp1_dryrun"
ARMS = {"L0": "deterministic frame, standard brief", "L1": "deterministic frame, procedures from the graph in the brief",
        "L3": "LLM framer, standard brief"}


def manifest_digest() -> str:
    f = ROOT / os.environ.get("EXP1_MANIFEST", "spec/EXP1_MANIFEST.json")   # the rerun: spec/EXP2_MANIFEST_A4.json
    return hashlib.sha1(f.read_bytes()).hexdigest() if f.exists() else "none"


def main(arm: str, repeat: int):
    if arm not in ARMS:
        raise SystemExit(f"arm must be one of {sorted(ARMS)}")
    out = ROOT / OUT
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{arm}_r{repeat}.jsonl"
    done = {json.loads(x)["case"] for x in f.open()} if f.exists() else set()
    client = LLMClient("openai")
    digest = manifest_digest()
    print(f"arm={arm} ({ARMS[arm]}) repeat={repeat} model={client.model} proposer={PROMPT_VERSION} "
          f"framer={FRAMER_VERSION} manifest={digest[:12]} already done={len(done)}", flush=True)
    for day in DAYS:
        for seed in SEEDS:
            case_id = f"d{int(day)}-s{seed}"
            if case_id in done:
                continue
            case = make_case(day, seed, procedures_in_graph=(arm == "L1"))
            kg = case.substrate.kg
            n0, t0 = len(client.log), time.time()
            row = {"case": case.id, "day": day, "seed": seed, "arm": arm, "repeat": repeat, "manifest": digest,
                   "model": client.model, "proposer_version": PROMPT_VERSION,
                   "framer": "llm:" + FRAMER_VERSION if arm == "L3" else "deterministic", "error": None}
            try:
                if arm == "L3":
                    framer = LLMFramer(client, case.substrate)
                    frame = framer.build(case.question)
                    row["framer_trace"] = framer.trace
                    row["frame"] = frame.model_dump()
                else:
                    frame = DeterministicFramer(case.substrate).build(case.question)
                prop = LLMProposer(client, kg, GroundingTool(kg), procedures_from_graph=(arm == "L1"))
                brief, refs = prop.prompt(frame)[:2]
                row["brief_sha"] = hashlib.sha1(brief.encode()).hexdigest()
                row["refs"] = refs
                p = prop.run(frame)
                row["candidates"] = [[c.hypothesis.id, c.prior_mass] for c in p.candidates]
                row["residual"] = p.residual_mass
                row["trace"] = prop.trace
            except Exception as e:  # noqa: BLE001
                row["error"] = str(e)[:500]
            calls = client.records()[n0:]
            row["seconds"] = round(time.time() - t0, 1)
            row["input_tokens"] = sum(c["input_tokens"] or 0 for c in calls)
            row["output_tokens"] = sum(c["output_tokens"] or 0 for c in calls)
            row["reasoning_tokens"] = sum(c["reasoning_tokens"] or 0 for c in calls)
            row["calls"] = calls
            with f.open("a") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
            print(f"{case.id:>11} {'ERROR ' + row['error'][:80] if row['error'] else 'ok'} "
                  f"tokens {row['input_tokens']}/{row['output_tokens']} {row['seconds']}s", flush=True)
    print(f"finished: {f.relative_to(ROOT)}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    main(a[0], int(a[1]) if len(a) > 1 else 1)
