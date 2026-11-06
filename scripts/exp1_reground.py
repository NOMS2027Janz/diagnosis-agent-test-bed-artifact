"""Experiment 1 under amendment 4 without new calls: the LLM arms' stored proposals grounded again on the current code.

Run from the project root (no API calls):
    EXP1_OUT=results/a4/exp1 python scripts/exp1_reground.py
Reads every results/exp1/L*_r*.jsonl, and for each row grounds the proposals the model gave (trace.proposals) again
with the current grounding, on the frame the arm used (the deterministic frame rebuilt for L0 and L1; the LLM
framer's stored frame for L3), and writes the row with its new candidates to $EXP1_OUT/<same file name>. The model's
answers are those of the first run; only how they are placed on the network is redone. Resumable.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from adagent.harness.cases import make_case                                   # noqa: E402
from adagent.schemas import ScenarioFrame                                     # noqa: E402
from adagent.tools.frame import DeterministicFramer                          # noqa: E402
from adagent.tools.grounding import GroundingTool                            # noqa: E402
from adagent.tools.proposer_llm import LLMProposer                           # noqa: E402

KEYS = ("fault_mode", "location_kind", "term", "anchor_symptom", "anchor_service", "element", "probability",
        "rationale")


def main():
    src = ROOT / "results" / "exp1"
    out = ROOT / os.environ.get("EXP1_OUT", "results/a4/exp1")
    out.mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    for f in sorted(src.glob("L*_r*.jsonl")):
        g = out / f.name
        done = {json.loads(x)["case"] for x in g.open()} if g.exists() else set()
        n = 0
        with g.open("a") as fh:
            for line in f.open():
                r = json.loads(line)
                if r["case"] in done:
                    continue
                if r.get("error") or "trace" not in r:
                    fh.write(json.dumps(r) + "\n")          # a failed call stays a failed call
                    continue
                key = (r["day"], r["seed"])
                if key not in cache:
                    case = make_case(r["day"], r["seed"])
                    cache.clear()
                    cache[key] = (case, DeterministicFramer(case.substrate).build(case.question))
                case, det = cache[key]
                kg = case.substrate.kg
                frame = ScenarioFrame(**r["frame"]) if r.get("frame") else det
                prop = LLMProposer(None, kg, GroundingTool(kg), procedures_from_graph=r["arm"] == "L1")
                ans = SimpleNamespace(assessment=r["trace"]["assessment"],
                                      probability_other=r["trace"]["probability_other"],
                                      proposals=[{k: q.get(k) for k in KEYS} for q in r["trace"]["proposals"]])
                p = prop.ground(frame, ans, r["refs"])
                fh.write(json.dumps({**r, "regrounded": "amendment 4", "trace": prop.trace,
                                     "candidates": [[c.hypothesis.id, c.prior_mass] for c in p.candidates],
                                     "residual": p.residual_mass}, default=str) + "\n")
                n += 1
        print(f"{f.name}: {n} rows grounded again", flush=True)


if __name__ == "__main__":
    main()
