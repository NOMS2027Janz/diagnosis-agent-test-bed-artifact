"""Experiment 3 spot check (spec/PROTOCOL_EXP3.md, section 4): arm A rerun under the Experiment 3 manifest must
reproduce the stored Experiment 2 results exactly (outcome, decision, checks, effort and site visits).

Run from the project root:  python scripts/exp2_x3_spot.py --new results/x3/spot --old results/a42
Exits 1 if any case differs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEEDS = [2000, 2003, 2005, 2021, 2034, 2042, 2069, 2088, 2117, 2149, 2160, 2190, 2222, 2251, 2280, 2311]


def key(r: dict) -> dict:
    s, lp = r["score"], r["loop"]
    dec = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in (lp.get("decision") or {}).items()}
    return {"outcome": s["outcome"], "class_whole": s.get("class_whole"), "effort_h": s.get("effort_h"),
            "site_visits": s.get("site_visits"), "decision": dec,
            "checks": json.dumps(lp.get("checks", []), sort_keys=True, default=str)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", default="results/x3/spot")
    ap.add_argument("--old", default="results/a42")
    a = ap.parse_args()
    bad = 0
    for s in SEEDS:
        new = ROOT / a.new / "A" / f"s{s}.json"
        old = [p for p in (ROOT / a.old / "orig" / "A" / f"s{s}.json", ROOT / a.old / "confirm" / "A" / f"s{s}.json")
               if p.exists()]
        if not new.exists() or not old:
            print(f"s{s}: missing ({'new' if not new.exists() else 'stored'})")
            bad += 1
            continue
        kn, ko = key(json.loads(new.read_text())), key(json.loads(old[0].read_text()))
        same = kn == ko
        bad += not same
        print(f"s{s}: {'same' if same else 'DIFFERS'}  {ko['outcome']}" +
              ("" if same else f" -> {kn['outcome']}; " + ", ".join(k for k in kn if kn[k] != ko[k])))
    print("spot check: all 16 reproduced" if not bad else f"spot check: {bad} of 16 differ")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
