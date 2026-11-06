"""Experiment 1: run an LLM arm on a fixed subset of the held-out seeds (both days).

Run from the project root, on the laptop:
    python scripts/exp1_llm_subset.py ARM REPEAT FIRST_SEED LAST_SEED
e.g. python scripts/exp1_llm_subset.py L3 1 1000 1024   (seeds 1000 to 1024 at days 730 and 2190: 50 cases)

Runs exp1_llm.py's own main() unchanged, with its seed range narrowed; it writes to the same file and skips cases
already done, exactly as exp1_llm.py does. Written 2026-10-15 when arm L3 (about 90 s and $0.35 a case) was cut
to a subset fixed by seed, not by any result, to save about three hours. Not part of the frozen manifest: it
changes which cases are run, not how a case is run.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exp1_llm  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    arm, repeat, first, last = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    if not (1000 <= first <= last <= 1099):
        raise SystemExit("seeds must lie within the held-out range 1000 to 1099")
    exp1_llm.SEEDS = range(first, last + 1)
    print(f"subset: seeds {first} to {last} at days {', '.join(str(int(d)) for d in exp1_llm.DAYS)}", flush=True)
    exp1_llm.main(arm, repeat)
