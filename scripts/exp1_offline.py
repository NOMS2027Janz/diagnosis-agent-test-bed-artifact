"""Experiment 1 (generation), offline lines, on the held-out cases (spec/PROTOCOL_EXP1_GENERATION.md).

Run from the project root:  python scripts/exp1_offline.py
Runs the abduction walk, PageRank walk, set cover, alarm correlation, analogy search and procedures (tool arm) on
seeds 1000 to 1099 at days 730 and 2190 and writes results/exp1/offline.jsonl: per case and line, every candidate
with its prior mass, the residual, graph nodes touched and wall time. Scores nothing. Resumable.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adagent.harness.cases import context, make_case, sources                # noqa: E402
from adagent.substrate.lifecycle import build_graph                          # noqa: E402
from adagent.tools.analogy import AnalogySearch                             # noqa: E402
from adagent.tools.correlation import AlarmCorrelation                      # noqa: E402
from adagent.tools.experience import ExperienceStore                        # noqa: E402
from adagent.tools.frame import DeterministicFramer                         # noqa: E402
from adagent.tools.grounding import GroundingTool                           # noqa: E402
from adagent.tools.procedures import ProcedureLibrary, ProcedureTool        # noqa: E402
from adagent.tools.walks import AbductionWalk, MinimumExplanation, RandomWalkRanking  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from exp1_llm import DAYS, OUT, SEEDS, manifest_digest                      # noqa: E402


def main():
    out = ROOT / OUT
    out.mkdir(parents=True, exist_ok=True)
    f = out / "offline.jsonl"
    done = {json.loads(x)["case"] for x in f.open()} if f.exists() else set()
    store = ExperienceStore()
    build_graph(context().events, max(DAYS), sources(), observers=[store])
    lib = ProcedureLibrary.from_sources(sources())
    digest = manifest_digest()
    for day in DAYS:
        for seed in SEEDS:
            case = make_case(day, seed)
            if case.id in done:
                continue
            kg = case.substrate.kg
            frame = DeterministicFramer(case.substrate).build(case.question)
            abd = AbductionWalk(kg)
            lines = {"abduction": abd, "pagerank": RandomWalkRanking(kg), "setcover": MinimumExplanation(kg, abd),
                     "alarm_correlation": AlarmCorrelation(kg),
                     "analogy": AnalogySearch(kg, store.as_of(day), GroundingTool(kg)),
                     "procedures": ProcedureTool(case.substrate, lib)}
            row = {"case": case.id, "day": day, "seed": seed, "manifest": digest,
                   "question_kind": case.question.kind, "n_symptoms": len(frame.symptoms), "lines": {}}
            for name, g in lines.items():
                n0, t0 = kg.cost.nodes_touched, time.time()
                p = g.run(frame)
                row["lines"][name] = {"candidates": [[c.hypothesis.id, c.prior_mass] for c in p.candidates],
                                      "residual": p.residual_mass, "seconds": round(time.time() - t0, 3),
                                      "nodes_touched": kg.cost.nodes_touched - n0}
                if name == "procedures":
                    row["lines"][name]["tests_pending"] = len(g.trace.get("tests_pending", []))
            with f.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            print(f"{case.id:>11} done", flush=True)
    print(f"finished: {f.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
