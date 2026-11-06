"""Amendment 18, reference completions (no API): _empty (nothing completed: the degraded baseline through the same
wrapper) and _oracle (the true documentation's entries for exactly what was removed: the ceiling of the method).
Writes results/campaign/a18/completion_<kind>_<seed>_{empty,oracle}.json.
Run from the project root:   python scripts/a18_oracle.py --kind sig --seed dev
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["none", "sig", "modes"])
    ap.add_argument("--seed", default="dev", choices=["diag", "dev"])
    a = ap.parse_args()
    import a18_complete, a18_run
    from adagent.harness import exp2
    case = exp2.make_case2(2190.0, 2000, gap_scale=1.0, records_complete=False, procedures_in_graph=False,
                           gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
    kt = case.substrate.kg
    kgd, rep = a18_complete.degrade(kt, a.kind, a.seed)
    V = a18_complete.vocab(kt)
    rev = {json.dumps(v, sort_keys=True): k for k, v in V["scenarios"].items()}
    true = {(u, m): d for u, m, d in kt.g.edges(data=True) if d.get("rel") == "hasFaultMode"}
    held = {(u, m): d for u, m, d in kgd.g.edges(data=True) if d.get("rel") == "hasFaultMode"}
    types = {}
    for (u, m), d in sorted(true.items()):
        t = types.setdefault(u, {"gaps": [], "added_modes": []})
        eff = [{"effect": e["effect"], "scope": e.get("scope", "all_channels"), "where": e.get("where")} for e in d.get("effects") or []]
        if (u, m) in held and not held[(u, m)].get("effects") and eff:
            t["gaps"].append({"fault_mode": m, "effects": eff})
        elif (u, m) not in held:
            sc = d.get("twin_scenario")
            t["added_modes"].append({"fault_mode": m, "description": d.get("description", ""), "nature": d.get("nature") or "hard",
                                     "rate_per_year_per_unit": float((d.get("documented_rate") or {}).get("per_year") or 0.01) / a18_run.RATE_FACTOR,
                                     "effects": eff, "twin_scenario": rev.get(json.dumps(sc, sort_keys=True), "none") if sc else "none",
                                     "confidence": 1.0})
    out = A = ROOT / "results" / "campaign" / "a18"
    A.mkdir(parents=True, exist_ok=True)
    base = {"kind": a.kind, "seed": a.seed, "degradation": rep}
    (A / f"completion_{a.kind}_{a.seed}_oracle.json").write_text(json.dumps({**base, "types": types}, indent=1))
    (A / f"completion_{a.kind}_{a.seed}_empty.json").write_text(json.dumps({**base, "types": {}}, indent=1))
    print(a.kind, a.seed, rep, sum(len(t["gaps"]) for t in types.values()), "gaps,", sum(len(t["added_modes"]) for t in types.values()), "removed modes")


if __name__ == "__main__":
    main()
