"""Bring an arm's results up to a later code freeze, rerunning only the cases the change can reach.

Run from the project root:
    python scripts/exp2_refresh.py --from results/a4/orig --to results/a41/orig --arms A-once,A-no-twin --seeds 2000-2159
A case the loop committed at once (no planning step, no check) never reaches the planner, the checks or their
reading, so a change confined to those (amendment 4.1: the documented reading of checks) leaves it identical: its
row is copied. Every other case is listed for rerun (printed, one "arm seed" per line, to --jobs). Baseline arms
(B, B-graph) do not use the agent's reading of checks and are copied whole. Nothing is deleted or overwritten in
--from.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNCHANGED_ARMS = {"B", "B-graph"}


def seeds_arg(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def reached(row: dict) -> bool:
    loop = row.get("loop") or {}
    return bool(loop.get("plans") or loop.get("checks")) or loop.get("outcome") != "commit"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--arms", required=True)
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--jobs", required=True, help="file to write the cases to rerun to")
    ap.add_argument("--provenance-from", default=None, help="arm A's provenance directory, copied for unreached cases")
    ap.add_argument("--provenance-to", default=None)
    a = ap.parse_args()
    src, dst = ROOT / a.src, ROOT / a.dst
    jobs, copied = [], 0
    for arm in a.arms.split(","):
        (dst / arm).mkdir(parents=True, exist_ok=True)
        for s in seeds_arg(a.seeds):
            f = src / arm / f"s{s}.json"
            if not f.exists():
                jobs.append(f"{arm} {s}")
                continue
            row = json.loads(f.read_text())
            if arm in UNCHANGED_ARMS or not reached(row):
                if not (dst / arm / f.name).exists():
                    shutil.copyfile(f, dst / arm / f.name)
                copied += 1
                if arm == "A" and a.provenance_from:
                    pf = ROOT / a.provenance_from / f.name
                    pt = ROOT / a.provenance_to
                    pt.mkdir(parents=True, exist_ok=True)
                    if pf.exists() and not (pt / f.name).exists():
                        shutil.copyfile(pf, pt / f.name)
            else:
                jobs.append(f"{arm} {s}")
    (ROOT / a.jobs).write_text("\n".join(jobs) + ("\n" if jobs else ""))
    print(f"{copied} rows copied unchanged; {len(jobs)} cases to rerun listed in {a.jobs}")


if __name__ == "__main__":
    main()
