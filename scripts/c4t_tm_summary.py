"""The twin with a shifted mean (amendments 22 and 24): the frozen agent and manual troubleshooting under the
structural mismatch, and the agent with the margin levels judged against the fleet, against the accurate twin.

    python scripts/c4t_tm_summary.py

Right = commit_correct or commit_correct_ambiguous; wrong = commit_wrong. Hours = total effort over the cases.
Detection: the median offset the fleet-judged margin levels removed (margin_offset_decades; 0.5 decade per dB).
Diagnosis cases 2000-2159.
"""
import json, statistics
from pathlib import Path

C = Path("results/campaign")
SEEDS = range(2000, 2160)
RIGHT = {"commit_correct", "commit_correct_ambiguous"}


def load(d):
    return {s: json.loads((C / d / f"s{s}.json").read_text()) for s in SEEDS if (C / d / f"s{s}.json").exists()}


arms = [("Agent, accurate twin (frozen)", "c4r/A-full-cd"),
        ("Agent, shifted twin (frozen)", "c4t/A-full-cd-tm1"),
        ("Agent, fleet-judged margin, accurate twin", "c4v/A-full-cd"),
        ("Agent, fleet-judged margin, shifted twin", "c4v/A-full-cd-tm1"),
        ("Manual troubleshooting, accurate world", "c4_dev/B"),
        ("Manual troubleshooting, shifted world", "c4t/B-tm1")]
for name, d in arms:
    rs = load(d)
    oc = [r["score"]["outcome"] for r in rs.values()]
    line = (f"{name:44s} n={len(rs):3d} right={sum(o in RIGHT for o in oc):3d} wrong={oc.count('commit_wrong'):2d} "
            f"hours={sum(r['score']['effort_h'] for r in rs.values()):6.1f}")
    off = [r["loop"].get("margin_offset_decades") for r in rs.values() if "loop" in r]
    off = [o for o in off if o is not None]
    if off:
        line += f"  median offset {statistics.median(off):+.3f} decade ({statistics.median(off) / 0.5:+.2f} dB)"
    print(line)
