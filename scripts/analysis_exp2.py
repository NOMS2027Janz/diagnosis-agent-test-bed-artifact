"""Experiment 2 analysis: outcomes, effort and visits by kind of anomaly for every arm on both case sets, paired
comparisons, and the planner and model comparisons. Rerunnable as results arrive; arms not yet run are skipped.

Run from the project root:  python scripts/analysis_exp2.py   (writes results/a4/ANALYSIS.md and analysis.json)
Amendment 4: every arm rerun under spec/EXP2_MANIFEST_A4.json, results under results/a4/.
"""
from __future__ import annotations

import glob
import json
import os
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "results"
RES = os.environ.get("EXP2_RESULTS", "a41")     # results/a41 (amendment 4.1); a4 for the amendment 4 rows
KINDS = ["hard", "soft", "transient", "drift", "monitoring", "coincident", "maintenance", "common_cause"]
KIND_NAME = {"hard": "Hard", "soft": "Soft", "transient": "Transient", "drift": "Drift", "monitoring": "Monitoring",
             "coincident": "Two faults", "maintenance": "Maintenance", "common_cause": "Duct cut"}
OUT = ["right", "partial", "wrong", "idk_j", "idk_u", "nonconv", "noproc"]


def cls(o: str) -> str:
    return {"commit_correct": "right", "commit_correct_ambiguous": "right", "commit_partial": "partial",
            "commit_wrong": "wrong", "dont_know_justified": "idk_j", "dont_know_unjustified": "idk_u",
            "non_convergence": "nonconv", "no_procedure": "noproc"}[o]


def load(d: str, arm: str) -> dict:
    out = {}
    for f in glob.glob(str(R / d / arm / "s*.json")):
        if f.endswith(".error.json"):
            continue
        r = json.load(open(f))
        out[r["seed"]] = r
    return out


def tally(rows: dict, seeds=None) -> dict:
    t = {"n": 0, "effort": 0.0, "visits": 0, "svc": 0, **{o: 0 for o in OUT}, "by_kind": {}}
    for s, r in rows.items():
        if seeds is not None and s not in seeds:
            continue
        sc = r["score"]
        o = cls(sc["outcome"])
        k = r["labels"]["kind"]
        t["n"] += 1
        t[o] += 1
        t["effort"] += sc["effort_h"]
        t["visits"] += sc["site_visits"]
        t["svc"] += sc.get("service_affecting", 0)
        bk = t["by_kind"].setdefault(k, {"n": 0, "effort": 0.0, "visits": 0, **{o2: 0 for o2 in OUT}})
        bk["n"] += 1
        bk[o] += 1
        bk["effort"] += sc["effort_h"]
        bk["visits"] += sc["site_visits"]
    return t


def boot_rate(rows: dict, key="right", n=2000, seed=0) -> tuple:
    vals = [1 if cls(r["score"]["outcome"]) == key else 0 for r in rows.values()]
    if not vals:
        return (0, 0)
    rng = random.Random(seed)
    m = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(n))
    return (m[int(0.025 * n)], m[int(0.975 * n)])


def paired(a: dict, b: dict, key="right", n=2000, seed=0) -> tuple:
    common = sorted(set(a) & set(b))
    d = [(cls(a[s]["score"]["outcome"]) == key) - (cls(b[s]["score"]["outcome"]) == key) for s in common]
    if not d:
        return (0.0, 0.0, 0.0, 0)
    rng = random.Random(seed)
    m = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
    return (sum(d) / len(d), m[int(0.025 * n)], m[int(0.975 * n)], len(d))


def row_line(name, t):
    return (f"| {name} | {t['n']} | {t['right']} | {t['partial']} | {t['wrong']} | {t['idk_j']} | {t['idk_u']} | "
            f"{t['nonconv'] + t['noproc']} | {t['effort']:.1f} | {t['visits']} |")


def main():
    sets = {"original": (f"{RES}/orig", range(2000, 2160)), "confirmation": (f"{RES}/confirm", range(2160, 2320))}
    arms = ["A", "L", "A-once", "A-perfect", "A-2x", "A-uncal", "A-twin-eq", "A-twin-eq-doc", "A-rc", "A-no-twin", "A-no-c1", "A-G", "G", "A-ts0", "G-no-twin",
            "B", "B-graph"]
    md = ["# Experiment 2 (winnowing): analysis", "",
          "Outcomes: right (correct, including ambiguous), partial (one of two faults), wrong, I don't know justified "
          "and unjustified, other (non-convergence; for B, no procedure applies). Effort in hours as incurred, with "
          "actual travel; visits are site visits.", ""]
    J = {}
    for sname, (d, rng_) in sets.items():
        md += [f"## {sname.capitalize()} set", "", "| Arm | n | right | partial | wrong | IDK just. | IDK unjust. | other | "
               "effort h | visits |", "|---|---|---|---|---|---|---|---|---|---|"]
        J[sname] = {}
        loaded = {}
        extra = []
        for arm in arms + [a for a, _ in extra]:
            rows = load(dict(extra).get(arm, d), "L" if arm == "L-v3" else arm)
            if not rows:
                continue
            loaded[arm] = rows
            t = tally(rows)
            lo, hi = boot_rate(rows) if arm != "L" else (0, 0)
            t["right_ci"] = (lo, hi)
            J[sname][arm] = t
            md.append(row_line(arm, t))
        md.append("")
        md += ["Right by kind (right / n; effort h; visits):", "",
               "| Arm | " + " | ".join(KIND_NAME[k] for k in KINDS) + " |", "|---" * (len(KINDS) + 1) + "|"]
        for arm, t in J[sname].items():
            cells = []
            for k in KINDS:
                b = t["by_kind"].get(k)
                cells.append(f"{b['right']}/{b['n']}; {b['effort']:.1f}; {b['visits']}" if b else "-")
            md.append(f"| {arm} | " + " | ".join(cells) + " |")
        md.append("")
        if "A" in loaded:
            md += ["Paired differences in the rate of right diagnoses against A (positive: the arm does better):", ""]
            for arm, rows in loaded.items():
                if arm == "A":
                    continue
                m, lo, hi, n = paired(rows, loaded["A"])
                md.append(f"- {arm}: {m:+.3f} [{lo:+.3f}, {hi:+.3f}] (n={n})")
            md.append("")
    # planner and model comparisons on the planned cases of the confirmation set
    md += ["## Planners and models (confirmation set, the cases where A did not commit at once)", "",
           "| Planner | n | right | partial | wrong | IDK just. | IDK unjust. | effort h | calls | input tok | output tok |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    A = load(f"{RES}/confirm", "A")
    planned = {s for s, r in A.items() if (r.get("loop", {}).get("plans") or r.get("loop", {}).get("checks")
                                            or r.get("loop", {}).get("outcome") != "commit")}
    t = tally(A, planned)
    md.append(f"| advisor (A) | {t['n']} | {t['right']} | {t['partial']} | {t['wrong']} | {t['idk_j']} | {t['idk_u']} | "
              f"{t['effort']:.1f} | - | - | - |")
    J["models"] = {"advisor": t}
    for name, d in [("gpt-5.6-sol", f"{RES}/models/sol/L"), ("gpt-5.6-luna", f"{RES}/models/luna/L"),
                    ("gpt-5.4-mini", f"{RES}/models/mini/L"), ("deepseek-v4-pro", f"{RES}/models/dsv4pro/L")]:
        rows = {}
        for f in glob.glob(str(R / d / "s*.json")):
            if not f.endswith(".error.json"):
                r = json.load(open(f))
                rows[r["seed"]] = r
        if not rows:
            md.append(f"| {name} | pending | | | | | | | | | |")
            continue
        t = tally(rows)
        calls = [c for r in rows.values() for c in r.get("llm_calls", [])]
        t.update(calls=len(calls), tin=sum(c.get("input_tokens") or 0 for c in calls),
                 tout=sum(c.get("output_tokens") or 0 for c in calls))
        J["models"][name] = t
        md.append(f"| {name} | {t['n']} | {t['right']} | {t['partial']} | {t['wrong']} | {t['idk_j']} | {t['idk_u']} | "
                  f"{t['effort']:.1f} | {t['calls']} | {t['tin']:,} | {t['tout']:,} |")
    md.append("")
    (R / RES).mkdir(exist_ok=True)
    (R / RES / "ANALYSIS.md").write_text("\n".join(md))
    (R / RES / "analysis.json").write_text(json.dumps(J, indent=1, default=str))
    print("\n".join(md))


if __name__ == "__main__":
    main()
