"""Amendment 18: the agent over degraded documentation completed by the language model (spec/memos/AMENDMENT_18.md).

Arms: A-full-cd[-fut]-kg-<kind>-comp[-gaps|-adds], kind none, sig or modes (-fut: future instrumentation). The documentation is degraded as in
scripts/kgx_run.py (seed "diag", the diagnosis arms' draw; or "dev", the development draw of a18_complete.py), then
completed from results/campaign/a18/completion_<kind>_<seed>.json: gaps filled (effects for modes documented without
effects) and modes added (half the stated rate, provenance "llm"). Suffix -gaps applies only the gaps, -adds only the
additions; none applies both. The agent's code is unchanged; only the graph its side reads differs.

Run from the project root, for example:
    python scripts/a18_run.py --arms A-full-cd-kg-sig-comp --seed dev --seeds 3000-3039 --workers 2
Each job writes results/campaign/a18/<seed>/<arm>/s<seed>.json; finished jobs are skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

A18 = ROOT / "results" / "campaign" / "a18"
RATE_FACTOR = 0.5
UNATTESTED_FACTOR = 0.05     # trust "graded": an added mode no comparable type documents
TRUSTS = ("half", "sibling", "graded")
FROZEN_TRUST = "sibling"     # amendment 18, frozen 2 Nov 2026 on development cases 3000-3039
SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def split(arm: str):
    base, _, rest = arm.rpartition("-kg-")
    parts = rest.split("-")
    kind, what = parts[0], (parts[2] if len(parts) > 2 else "both")
    assert base in ("A-full-cd", "A-full-cd-fut") and kind in ("none", "sig", "modes") and parts[1] == "comp" and what in ("both", "gaps", "adds"), arm
    return base, kind, what


def attested(kg) -> dict:
    """Fault modes documented, as held, for types of each element class (comparable equipment)."""
    g, by = kg.g, {}
    for u, m, d in g.edges(data=True):
        if d.get("rel") == "hasFaultMode":
            by.setdefault(g.nodes[u].get("element_class"), set()).add(m)
    return by


def apply_completion(kg, comp: dict, catalogue: dict, what: str = "both", trust: str = "half") -> dict:
    """Fill the visible gaps and add the modes of a completion record, in place. Returns counts.
    trust: "half" adds every mode at half the stated rate; "sibling" adds only modes that the documentation as held
    gives for another type of the same element class; "graded" adds those at half the stated rate and the others at
    UNATTESTED_FACTOR of it."""
    assert trust in TRUSTS, trust
    g = kg.g
    by = attested(kg)
    n = {"gaps_filled": 0, "gaps_not_gaps": 0, "modes_added": 0, "modes_already_documented": 0, "effects_added": 0,
         "modes_unattested": 0, "modes_unattested_dropped": 0}
    for ty, out in sorted(comp.get("types", {}).items()):
        if ty not in g:
            continue
        have = {v: k for _, v, k, d in g.out_edges(ty, keys=True, data=True) if d.get("rel") == "hasFaultMode"}
        if what in ("both", "gaps"):
            for gap in out.get("gaps", []):
                m = gap["fault_mode"]
                if m not in have or g.edges[ty, m, have[m]].get("effects"):
                    n["gaps_not_gaps"] += 1         # only visible gaps are filled
                    continue
                eff = [{k: v for k, v in e.items() if v is not None} for e in gap["effects"]]
                g.edges[ty, m, have[m]]["effects"] = eff
                g.edges[ty, m, have[m]]["provenance"] = "llm"
                n["gaps_filled"] += 1
                n["effects_added"] += len(eff)
        if what in ("both", "adds"):
            for add in out.get("added_modes", []):
                m = add["fault_mode"]
                if m in have:
                    n["modes_already_documented"] += 1
                    continue
                factor = RATE_FACTOR
                if m not in by.get(g.nodes[ty].get("element_class"), set()):
                    n["modes_unattested"] += 1
                    if trust == "sibling":
                        n["modes_unattested_dropped"] += 1
                        continue
                    if trust == "graded":
                        factor = UNATTESTED_FACTOR
                if m not in g:
                    g.add_node(m, cls="FaultMode")
                sc = catalogue.get(add["twin_scenario"]) if add["twin_scenario"] != "none" else None
                g.add_edge(ty, m, rel="hasFaultMode", description=add["description"],
                           effects=[{k: v for k, v in e.items() if v is not None} for e in add["effects"]],
                           nature=add["nature"],
                           documented_rate={"per_year": factor * max(0.0, float(add["rate_per_year_per_unit"])),
                                            "per": "unit"},
                           twin_scenario=dict(sc) if sc else None, provenance="llm")
                have[m] = None
                n["modes_added"] += 1
                n["effects_added"] += len(add["effects"])
    return n


_STATE: dict = {}


def install(kind: str, seed: str, what: str, label: str = "", trust: str = "half") -> None:
    import a18_complete
    import kgx_run
    comp = json.loads((A18 / f"completion_{kind}_{seed}{label}.json").read_text())
    orig = kgx_run.degraded          # the diagnosis draw, kept before kgx_run.degraded is replaced below

    def degraded(kg, k):
        assert k == kind
        new, rep = orig(kg, kind) if seed == "diag" else a18_complete.degrade(kg, kind, seed)
        if "catalogue" not in _STATE:
            _STATE["catalogue"] = a18_complete.vocab(kg)["scenarios"]
        rep = dict(rep or {})
        rep["completion"] = apply_completion(new, comp, _STATE["catalogue"], what, trust)
        return new, rep
    kgx_run.degraded = degraded
    kgx_run.install(kind)
    _STATE["comp_sha"] = hashlib.sha256((A18 / f"completion_{kind}_{seed}{label}.json").read_bytes()).hexdigest()


def job(args):
    arm, day, seed_case, seed_deg, outdir, label, trust = args
    base, kind, what = split(arm)
    f = Path(outdir) / arm / f"s{seed_case}.json"
    t0 = time.time()
    try:
        import kgx_run
        from adagent.harness import exp2
        if not getattr(exp2, "_a18", None):
            install(kind, seed_deg, what, label, trust)
            exp2._a18 = arm
        assert exp2._a18 == arm, "one arm per worker process"
        row = exp2.run_job(base, day, seed_case)
        row["arm"] = arm
        row["a18"] = {"kind": kind, "seed": seed_deg, "what": what, "label": label, "trust": trust, "report": kgx_run._CUR.get("rep"),
                      "completion_sha256": _STATE.get("comp_sha"), "script_sha256": SELF_SHA}
        kgx_run._CUR.clear()
        f.write_text(json.dumps(row, default=str))
        sc = row["score"]
        return f"{arm:<26} s{seed_case}  {row['case']:<28} {sc['outcome']:<26} effort={sc['effort_h']:.1f}h  {time.time() - t0:.0f}s"
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"arm": arm, "seed": seed_case, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"{arm:<26} s{seed_case}  FAILED {repr(e)[:120]}"


def main():
    import os
    import kgx_run
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", required=True)
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--seed", default="diag", choices=["diag", "dev"])
    ap.add_argument("--day", type=float, default=2190.0)
    ap.add_argument("--label", default="", help="completion file suffix, e.g. _oracle or _empty")
    ap.add_argument("--trust", default="half", choices=list(TRUSTS), help="trust rule for added modes")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    os.environ.setdefault("EXP2_FIT_CACHE", str(ROOT / "results" / "_fitcache"))
    seeds = kgx_run.seeds_arg(a.seeds)
    if any(7000 <= s <= 7319 or 4000 <= s <= 4319 for s in seeds):
        raise SystemExit("sealed seeds are not run here")
    outdir = A18 / (a.seed + a.label + ("" if a.trust == "half" else "_" + a.trust))
    t0 = time.time()
    for arm in a.arms.split(","):
        split(arm)
        (outdir / arm).mkdir(parents=True, exist_ok=True)
        jobs = [(arm, a.day, s, a.seed, str(outdir), a.label, a.trust) for s in seeds if not (outdir / arm / f"s{s}.json").exists()]
        print(f"{arm} ({a.seed}): {len(jobs)} jobs to run on {a.workers} workers", flush=True)
        with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=4) as pool:
            for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
                print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] " + line, flush=True)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
