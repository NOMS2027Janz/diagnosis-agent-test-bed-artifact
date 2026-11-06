"""Experiment 1: set aside failed rows so that the cases are run again.

Run from the project root, on the laptop, only when no exp1_llm.py run is writing:
    python scripts/exp1_retry_failed.py [ARM_FILE ...]      (default: every results/exp1/L*_r*.jsonl)

exp1_llm.py records a failed call as a row with an error, and on restart it skips every case already in the file,
failed or not. This script moves the failed rows of each arm file to results/exp1/failed/<arm>.<time>.jsonl (kept
as the record of what failed and why) and rewrites the arm file with the remaining rows in their original order.
Running the same exp1_llm.py command again then runs only those cases. It reads no scores and changes no
successful row. It refuses to touch a file modified in the last two minutes, which would mean a run is active.

Written 2026-10-15 after the OpenAI account ran out of credit during arms L0 r3 and L3 r1 (429 "no credits
remaining"). Not part of the frozen manifest: it changes which cases are attempted, not how any case is run.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "results" / "exp1"


def main(files: list[Path]) -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (EXP / "failed").mkdir(exist_ok=True)
    busy = [f.name for f in files if time.time() - f.stat().st_mtime < 120]
    if busy:
        print(f"Refused: modified in the last two minutes, a run may be active: {', '.join(busy)}")
        return 1
    for f in files:
        lines = [x for x in f.read_text().splitlines() if x.strip()]
        rows = [json.loads(x) for x in lines]
        bad = [x for x, r in zip(lines, rows) if r.get("error")]
        good = [x for x, r in zip(lines, rows) if not r.get("error")]
        if not bad:
            print(f"{f.name}: {len(good)} rows, none failed")
            continue
        side = EXP / "failed" / f"{f.stem}.{stamp}.jsonl"
        side.write_text("\n".join(bad) + "\n")
        f.write_text("\n".join(good) + "\n")
        reasons = sorted({json.loads(x)["error"].split(":")[0] for x in bad})
        print(f"{f.name}: kept {len(good)}, set aside {len(bad)} failed ({'; '.join(reasons)}) -> failed/{side.name}")
    print("Now run the same exp1_llm.py commands again; only the set-aside cases will run.")
    return 0


if __name__ == "__main__":
    args = [Path(a) for a in sys.argv[1:]] or sorted(EXP.glob("L*_r*.jsonl"))
    sys.exit(main(args))
