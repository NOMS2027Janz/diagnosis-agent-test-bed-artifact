"""Amendment 18: a completion scored against the true documentation (no API).

For results/campaign/a18/completion_<kind>_<seed>.json: gaps (modes documented without effects) filled, effects
matching the true ones exactly (effect, scope, location) and by effect only, per gap and per effect; added modes
that the degradation removed (recovered) and that the true documentation lacks for that type (spurious); entries
dropped by validation are counted by a18_run.apply_completion.
Run from the project root:   python scripts/a18_score.py --kind sig --seed diag
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def norm(effs):
    return {(e["effect"], e.get("scope") or "all_channels", e.get("where")) for e in effs or []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True)
    ap.add_argument("--seed", default="diag")
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    import a18_complete
    from adagent.harness import exp2
    case = exp2.make_case2(2190.0, 2000, gap_scale=1.0, records_complete=False, procedures_in_graph=False,
                           gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
    kt = case.substrate.kg
    kgd, rep = a18_complete.degrade(kt, a.kind, a.seed)
    true = {(u, m): d for u, m, d in kt.g.edges(data=True) if d.get("rel") == "hasFaultMode"}
    held = {(u, m): d for u, m, d in kgd.g.edges(data=True) if d.get("rel") == "hasFaultMode"}
    gaps = {k for k, d in held.items() if not d.get("effects")}
    removed = set(true) - set(held)
    comp = json.loads((ROOT / "results/campaign/a18" / f"completion_{a.kind}_{a.seed}{a.label}.json").read_text())
    g_filled = g_exact = g_effect = 0
    e_true = e_given = e_hit = 0
    rec = spur = dup = 0
    spur_list, miss_list = [], []
    for ty, out in comp["types"].items():
        for gp in out.get("gaps", []):
            k = (ty, gp["fault_mode"])
            if k not in gaps:
                continue
            g_filled += 1
            T, G = norm(true[k].get("effects")), norm(gp["effects"])
            g_exact += T == G
            g_effect += {t[0] for t in T} == {x[0] for x in G}
            e_true += len(T); e_given += len(G); e_hit += len(T & G)
        for ad in out.get("added_modes", []):
            k = (ty, ad["fault_mode"])
            if k in held:
                dup += 1
            elif k in removed:
                rec += 1
            else:
                spur += 1
                spur_list.append(f"{ty}:{ad['fault_mode']} ({ad.get('confidence')})")
    added = {(ty, ad["fault_mode"]) for ty, out in comp["types"].items() for ad in out.get("added_modes", [])}
    miss_list = sorted(f"{u}:{m}" for u, m in removed - added)
    res = {"kind": a.kind, "seed": a.seed, "label": a.label, "degradation": rep,
           "gaps": len(gaps), "gaps_filled": g_filled, "gaps_exact": g_exact, "gaps_same_effects": g_effect,
           "effects_true": e_true, "effects_given": e_given, "effects_matching": e_hit,
           "removed_modes": len(removed), "recovered": rec, "spurious": spur, "already_documented": dup,
           "spurious_list": spur_list, "not_recovered": miss_list}
    print(json.dumps(res, indent=1))
    (ROOT / "results/campaign/a18" / f"score_{a.kind}_{a.seed}{a.label}.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
