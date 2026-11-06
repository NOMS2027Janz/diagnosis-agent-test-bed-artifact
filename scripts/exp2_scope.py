"""Bring arms up to a later freeze by rerunning only the cases a change can reach, listed in a file.

Run from the project root:
    python scripts/exp2_scope.py --from results/a41 --to results/a42 --set orig --affected spec/EXP2_A42_AFFECTED.json \
        --arms A,A-once --jobs results/a42/jobs_orig.txt
Amendment 4.2 changes the accuracy standard for series of readings (any case that reached the planner or a check:
--also-reached) and the twin's fit of a transient's duration (any case where a mechanical disturbance or a
polarisation transient is among the candidates, listed per set from arm A's provenance in --affected). For every arm
the rows of other seeds are copied unchanged and the rest listed for rerun, one "arm seed" per line. Baseline arms (B, B-graph) do
not use the twin and are copied whole. Arm A's provenance is copied for unaffected seeds. Nothing in --from is changed.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNCHANGED_ARMS = {"B", "B-graph"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--set", required=True, choices=["orig", "confirm"])
    ap.add_argument("--affected", required=True)
    ap.add_argument("--arms", required=True)
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--also-reached", action="store_true",
                    help="also rerun every case that reached the planner or a check, or did not commit")
    a = ap.parse_args()
    affected = set(json.loads((ROOT / a.affected).read_text())[a.set])
    src, dst = ROOT / a.src / a.set, ROOT / a.dst / a.set
    jobs, copied = [], 0
    for arm in a.arms.split(","):
        (dst / arm).mkdir(parents=True, exist_ok=True)
        for f in sorted((src / arm).glob("s*.json")):
            s = int(f.stem[1:])
            hit = s in affected
            if a.also_reached and not hit:
                loop = json.loads(f.read_text()).get("loop") or {}
                hit = bool(loop.get("plans") or loop.get("checks")) or loop.get("outcome") != "commit"
            if arm in UNCHANGED_ARMS or not hit:
                if not (dst / arm / f.name).exists():
                    shutil.copyfile(f, dst / arm / f.name)
                copied += 1
            elif not (dst / arm / f.name).exists():
                jobs.append(f"{arm} {s}")
    ps, pd = ROOT / a.src / f"provenance_{a.set}", ROOT / a.dst / f"provenance_{a.set}"
    pd.mkdir(parents=True, exist_ok=True)
    rerun_a = {int(x.split()[1]) for x in jobs if x.split()[0] == "A"}
    for f in ps.glob("s*.json"):
        if int(f.stem[1:]) not in rerun_a and not (pd / f.name).exists():
            shutil.copyfile(f, pd / f.name)
    (ROOT / a.jobs).write_text("\n".join(jobs) + ("\n" if jobs else ""))
    print(f"{copied} rows copied unchanged; {len(jobs)} cases to rerun listed in {a.jobs}")


if __name__ == "__main__":
    main()
