"""Results must not depend on Python's per-process string hashing (PYTHONHASHSEED): no seed may come from
hash(), and no output may depend on the iteration order of a set. Runs the same small pipeline in two
processes with different hash seeds and compares digests."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r"""
import hashlib, json, sys
sys.path.insert(0, "src")
from adagent.harness.cases import context, make_case, sources
from adagent.world.history import observed_summary
from adagent.substrate.lifecycle import build_graph
from adagent.tools.frame import DeterministicFramer
from adagent.tools.walks import AbductionWalk, RandomWalkRanking, MinimumExplanation
from adagent.tools.correlation import AlarmCorrelation
from adagent.tools.analogy import AnalogySearch
from adagent.tools.experience import ExperienceStore
from adagent.tools.grounding import GroundingTool
h = context()
d = {"events": repr([(e.t, e.kind, sorted(e.payload.items(), key=str)) for e in h.events])}
inc = [i for i in h.incidents if i.ticket["ticketed"]][:12]
d["observed"] = repr([observed_summary(h, i) for i in inc])
store = ExperienceStore(); build_graph(h.events, 2190.0, sources(), observers=[store])
for seed in (5, 15, 24):
    c = make_case(2190.0, seed); kg = c.substrate.kg
    d[f"lake{seed}"] = repr([(o.id, o.observable, o.element, o.t_start, o.value) for o in c.substrate.lake.observations])
    fr = DeterministicFramer(c.substrate).build(c.question)
    d[f"frame{seed}"] = fr.model_dump_json()
    abd = AbductionWalk(kg)
    for name, g in (("abd", abd), ("pr", RandomWalkRanking(kg)), ("sc", MinimumExplanation(kg, abd)),
                    ("ac", AlarmCorrelation(kg)), ("an", AnalogySearch(kg, store, GroundingTool(kg)))):
        p = g.run(fr)
        d[f"{name}{seed}"] = repr([(x.hypothesis.id, round(x.prior_mass, 12)) for x in p.candidates] + [round(p.residual_mass, 12)])
print(json.dumps({k: hashlib.sha1(v.encode()).hexdigest() for k, v in d.items()}))
"""


def _run(hashseed: str) -> dict:
    import json
    env = dict(os.environ, PYTHONHASHSEED=hashseed)
    r = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=1800)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_results_do_not_depend_on_hash_seed():
    a, b = _run("1"), _run("2")
    assert [k for k in a if a[k] != b[k]] == []
