"""Amendment 20: completions scored against the true documentation over several draws (no API).

For each completion results/campaign/a20/completion_<kind>_<draw>.json present: gaps filled; exact (effects, scopes and
locations as the truth); same effect names; locations given that the truth lacks or differs on; removed modes the
completion proposes, and those the trust rule keeps; modes kept that the truth lacks; alarm links (probable causes)
missing after completion. Writes results/campaign/a20/scores.json and prints a table.
Run from the project root:   python scripts/a20_score.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
A20 = ROOT / "results" / "campaign" / "a20"


def norm(effs):
    return {(e["effect"], e.get("scope") or "all_channels", e.get("where")) for e in effs or []}


def main():
    import a18_complete, a20_complete, a20_run
    from adagent.harness import exp2
    case = exp2.make_case2(2190.0, 2000, gap_scale=1.0, records_complete=False, procedures_in_graph=False,
                           gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
    kt = case.substrate.kg
    cat = a18_complete.vocab(kt)["scenarios"]
    true = {(u, m): d for u, m, d in kt.g.edges(data=True) if d.get("rel") == "hasFaultMode"}
    plant = {t for t in kt.g.nodes if str(t).startswith("PLANT-")}

    def pcs(k):
        return {(u, a, p.get("mode")) for u, a, d in k.g.edges(data=True) if d.get("rel") == "canRaise"
                for p in d.get("probable_causes") or []}
    rows = []
    for f in sorted(A20.glob("completion_*_*.json")):
        _, kind, draw = f.stem.split("_", 2)
        if draw not in a20_complete.DRAWS:
            continue
        comp = json.loads(f.read_text())
        kgd, rep = a20_complete.degrade(kt, kind, draw)
        held = {(u, m): d for u, m, d in kgd.g.edges(data=True) if d.get("rel") == "hasFaultMode"}
        gaps = {k for k, d in held.items() if not d.get("effects")}
        removed = set(true) - set(held)
        r = {"kind": kind, "draw": draw, "gaps": len(gaps), "filled": 0, "exact": 0, "same_effects": 0,
             "where_wrong": 0, "plant_gaps": sum(1 for k in gaps if k[0] in plant), "plant_exact": 0,
             "removed": len(removed), "plant_removed": sum(1 for k in removed if k[0] in plant), "proposed": 0}
        for ty, out in comp["types"].items():
            for gp in out.get("gaps", []):
                k = (ty, gp["fault_mode"])
                if k not in gaps:
                    continue
                T, G = norm(true[k].get("effects")), norm(gp["effects"])
                r["filled"] += 1
                r["exact"] += T == G
                r["plant_exact"] += (T == G) and ty in plant
                r["same_effects"] += {t[0] for t in T} == {x[0] for x in G}
                tw = {(t[0], t[2]) for t in T}
                r["where_wrong"] += sum(1 for x in G if x[2] and (x[0], x[2]) not in tw)
            r["proposed"] += sum(1 for ad in out.get("added_modes", []) if (ty, ad["fault_mode"]) in removed)
        kg2, _ = a20_complete.degrade(kt, kind, draw)
        cnt = a20_run.apply_completion(kg2, comp, cat, "both", "a20", 0.5)
        kept = {(u, m) for u, m, d in kg2.g.edges(data=True) if d.get("rel") == "hasFaultMode" and d.get("provenance") == "llm"
                and (u, m) not in held}
        r["kept_recovered"] = len(kept & removed)
        r["kept_plant_recovered"] = sum(1 for k in kept & removed if k[0] in plant)
        r["kept_spurious"] = len(kept - set(true))
        r["links_missing"] = len(pcs(kt) - pcs(kg2))
        r["links_extra"] = len(pcs(kg2) - pcs(kt))
        r["apply"] = cnt
        rows.append(r)
    cols = ["kind", "draw", "gaps", "filled", "exact", "same_effects", "where_wrong", "plant_gaps", "plant_exact",
            "removed", "proposed", "kept_recovered", "plant_removed", "kept_plant_recovered", "kept_spurious",
            "links_missing", "links_extra"]
    print(" ".join(f"{c[:11]:>11}" for c in cols))
    for r in rows:
        print(" ".join(f"{str(r[c])[:11]:>11}" for c in cols))
    (A20 / "scores.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
