"""The flagship table on the sealed cases (seeds 7000-7319, results/campaign/c4): right, partial, wrong, "I don't know", effort split into
field hours (field checks and travel) and remote hours (remote checks), site visits, right by kind, and the paired
comparison on the cases two approaches both get right. Effort is the elapsed hours of the checks an approach
commissions plus travel; not person-hours. Writes results/campaign/flagship_c4.json.
    python scripts/analysis_flagship_c4.py [--arms A-full-cd,B,AC,AC-fut,...]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from analysis_kgx import cat, kind_of, load, remote_h   # noqa: E402

REC = ROOT / "results" / "campaign" / "c4"


def summary(rows: dict) -> dict:
    s = sorted(rows)
    c = Counter(cat(rows[k]["score"]["outcome"]) for k in s)
    eff = sum(rows[k]["score"]["effort_h"] for k in s)
    rem = sum(remote_h(rows[k]) for k in s)
    right = c.get("right", 0)
    return {"cases": len(s), **{k: c.get(k, 0) for k in ("right", "partial", "wrong", "dont_know", "no_procedure")},
            "effort_h": round(eff, 1), "field_h": round(eff - rem, 1), "remote_h": round(rem, 1),
            "site_visits": sum(rows[k]["score"]["site_visits"] for k in s),
            "right_per_hour": round(right / eff, 2) if eff else None,
            "right_by_kind": dict(sorted(Counter(kind_of(rows[k]["case"]) for k in s
                                                 if cat(rows[k]["score"]["outcome"]) == "right").items()))}


def paired(a: dict, b: dict) -> dict:
    both = [k for k in a if k in b and cat(a[k]["score"]["outcome"]) == "right" == cat(b[k]["score"]["outcome"])]
    f = lambda r: (round(sum(r[k]["score"]["effort_h"] for k in both), 1), sum(r[k]["score"]["site_visits"] for k in both),
                   round(sum(r[k]["score"]["effort_h"] - remote_h(r[k]) for k in both), 1))
    (ea, va, fa), (eb, vb, fb) = f(a), f(b)
    return {"cases": len(both), "a_effort_h": ea, "a_visits": va, "a_field_h": fa, "b_effort_h": eb, "b_visits": vb, "b_field_h": fb}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="AC,AC-fut,B,B-fut,B-Lall,A-full-cd,A-full-cd-fut,A-Lall")
    a = ap.parse_args()
    out = {}
    rows = {arm: load(REC / arm) for arm in a.arms.split(",")}
    for arm, r in rows.items():
        if not r:
            continue
        out[arm] = summary(r)
        x = out[arm]
        print(f"{arm:14s} {x['cases']:3d}  right {x['right']:3d}  partial {x['partial']:3d}  wrong {x['wrong']:2d}  "
              f"IDK {x['dont_know']:3d}  no proc {x['no_procedure']:3d}  effort {x['effort_h']:6.1f} h "
              f"(field {x['field_h']:6.1f}, remote {x['remote_h']:5.1f})  visits {x['site_visits']:3d}  right/h {x['right_per_hour']}")
    if rows.get("A-full-cd") and rows.get("B"):
        p = paired(rows["A-full-cd"], rows["B"])
        out["paired_A-full-cd_B"] = p
        print("both right:", p)
    (ROOT / "results" / "campaign" / "flagship_c4.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
