"""Experiment 1: equivalence classes for the held-out cases (harness side; spec/PROTOCOL_EXP1_GENERATION.md §5).

Run from the project root, after every arm has run:  python scripts/exp1_equivalence.py [day ...]
The locations tested for each case are those of the top 10 candidates of every line and arm in results/exp1/
(offline.jsonl and every LLM arm file). Writes results/exp1/equivalence/<case>.json; files extend incrementally,
so it can be re-run when an arm is added. Slow (one to several minutes per case). Takes an optional --part i/n to split the cases between processes.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adagent.harness.cases import make_case                 # noqa: E402
from adagent.harness.equivalence import K_SIGMA, equivalence_class  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from exp1_llm import DAYS, OUT, SEEDS                       # noqa: E402

TOP = 10


def locations() -> dict:
    out: dict = {}
    for f in sorted((ROOT / OUT).glob("*.jsonl")):
        for line in f.open():
            r = json.loads(line)
            lists = ([v["candidates"] for v in r["lines"].values()] if "lines" in r else [r.get("candidates") or []])
            for cands in lists:
                for cid, _ in cands[:TOP]:
                    for part in cid.split("+"):
                        out.setdefault(r["case"], {}).setdefault(part.split("@", 1)[1], set()).add(f.stem)
    return out


def main(days, part=(0, 1)):
    eqdir = ROOT / OUT / "equivalence"
    eqdir.mkdir(parents=True, exist_ok=True)
    locs = locations()
    for day in days:
        for seed in SEEDS:
            if seed % part[1] != part[0]:
                continue
            case_id = f"d{int(day)}-s{seed}"
            src = locs.get(case_id, {})
            f = eqdir / f"{case_id}.json"
            old = json.loads(f.read_text()) if f.exists() else {"fits": {}, "sources": {}, "members": []}
            todo = [loc for loc in src if loc not in old["sources"]]
            if not todo and f.exists():
                continue
            case = make_case(day, seed)
            t0 = time.time()
            res = equivalence_class(case, todo) if todo else {"members": [], "fits": {}}
            members = sorted(set(old["members"]) | set(res["members"]) | {f"{case.truth.fault.mode}@{t}"
                                                                          for t in case.truth.fault.targets})
            f.write_text(json.dumps({"case": case_id, "truth": case.truth.fault.key(),
                                     "truth_targets": case.truth.fault.targets, "mode": case.truth.fault.mode,
                                     "members": members, "fits": {**old["fits"], **res["fits"]},
                                     "sources": {**old["sources"], **{k: sorted(v) for k, v in src.items()}},
                                     "k_sigma": K_SIGMA}, indent=1))
            print(f"{case_id:>11} tested {len(res['fits']):4d} ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    part = (0, 1)
    if "--part" in a:
        i = a.index("--part")
        k, n = a[i + 1].split("/")
        part, a = (int(k), int(n)), a[:i] + a[i + 2:]
    main([float(x) for x in a] or list(DAYS), part)
