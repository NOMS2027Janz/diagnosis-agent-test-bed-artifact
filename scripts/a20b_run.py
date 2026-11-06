"""Amendment 20 (version b, 2 Nov 17:45: adds --nodup, one fault one home among connected one-of-a-kind equipment): the agent over degraded documentation completed by the language model (spec/memos/AMENDMENT_20.md).
Derived from scripts/a18_run.py. Changes: completions from scripts/a20_complete.py (results/campaign/a20); draws
dev1-dev12, test, diag (--draw); trust rule for added modes: attested by comparable equipment (same element class) at
half the stated rate; unattested on a one-of-a-kind type (no other type of its element class) at --single times the
stated rate; other unattested modes dropped; alarm links (probable causes) restored for filled gaps by analogy with
comparable equipment that raises the same alarm for the same mode.
--- original description (amendment 18) ---

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

A18 = ROOT / "results" / "campaign" / "a20"
RATE_FACTOR = 0.5
UNATTESTED_FACTOR = 0.05     # trust "graded": an added mode no comparable type documents
TRUSTS = ("half", "sibling", "graded", "a20")

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


import os as _os
NODUP = _os.environ.get("A20_NODUP") == "1"   # --nodup (set through the environment so that worker processes see it): an unattested addition on a one-of-a-kind type is dropped when equipment directly connected
                #          to it already documents the same fault mode (the same physical fault described twice)


def _connected_types(kg, ty: str) -> set:
    g = kg.g
    els = [e for e, _, d in g.in_edges(ty, data=True) if d.get("rel") == "hasType"]
    tys = set()
    for e in els:
        for a, b, d in list(g.out_edges(e, data=True)) + list(g.in_edges(e, data=True)):
            if d.get("rel") == "hasType":
                continue
            other = b if a == e else a
            t = kg.type_of(other) if other in g else None
            if t and t != ty:
                tys.add(t)
    return tys


def duplicate_elsewhere(kg, ty: str, m: str, conf: float, comp: dict) -> bool:
    """One fault, one home among directly connected equipment. An unattested addition of m on one-of-a-kind type ty is
    a duplicate when a connected type's documentation as held names m (a vendor-named mode, with or without effects),
    or when the completion also adds m to a connected type with a higher stated confidence; on equal confidence it is
    kept on neither."""
    for t in _connected_types(kg, ty):
        if any(x == m and not d.get("_added") for x, d in kg.out(t, "hasFaultMode")):
            return True
        for ad in (comp or {}).get("types", {}).get(t, {}).get("added_modes", []):
            if ad["fault_mode"] == m and float(ad.get("confidence") or 0) >= conf:
                return True
    return False


def restore_links(g, ty: str, m: str) -> int:
    """Alarm links for a filled gap, by analogy: where a comparable type (same element class) lists m among the
    probable causes of an alarm that ty can also raise, ty's alarm gets the same probable cause."""
    cls = g.nodes[ty].get("element_class")
    mine = {a: k for _, a, k, d in g.out_edges(ty, keys=True, data=True) if d.get("rel") == "canRaise"}
    add = {}
    for u, a, d in g.edges(data=True):
        if d.get("rel") == "canRaise" and u != ty and a in mine and g.nodes[u].get("element_class") == cls:
            for pc in d.get("probable_causes") or []:
                if pc.get("mode") == m:
                    add.setdefault(a, dict(pc))
    k = 0
    for a, pc in add.items():
        e = g.edges[ty, a, mine[a]]
        pcs = list(e.get("probable_causes") or [])
        if not any(p.get("mode") == m for p in pcs):
            e["probable_causes"] = pcs + [dict(pc, provenance="analogy")]
            k += 1
    return k


def apply_completion(kg, comp: dict, catalogue: dict, what: str = "both", trust: str = "a20", single: float = 0.5) -> dict:
    """Fill the visible gaps and add the modes of a completion record, in place. Returns counts.
    trust: "half" adds every mode at half the stated rate; "sibling" adds only modes that the documentation as held
    gives for another type of the same element class; "graded" adds those at half the stated rate and the others at
    UNATTESTED_FACTOR of it."""
    assert trust in TRUSTS, trust
    g = kg.g
    by = attested(kg)
    ncls = {}
    for t, d in g.nodes(data=True):
        if d.get("cls") == "EquipmentType" or any(e.get("rel") == "hasFaultMode" for _, _, e in g.out_edges(t, data=True)):
            ncls.setdefault(d.get("element_class"), set()).add(t)
    n = {"alarm_links_restored": 0, "modes_unattested_single": 0, "gaps_filled": 0, "gaps_not_gaps": 0, "modes_added": 0, "modes_already_documented": 0, "effects_added": 0,
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
                if trust == "a20":
                    n["alarm_links_restored"] += restore_links(g, ty, m)
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
                    one_of_a_kind = len(ncls.get(g.nodes[ty].get("element_class"), set())) <= 1
                    if trust == "a20" and one_of_a_kind and NODUP and duplicate_elsewhere(kg, ty, m, float(add.get("confidence") or 0), comp):
                        n["modes_duplicate_dropped"] = n.get("modes_duplicate_dropped", 0) + 1
                        continue
                    if trust == "a20" and one_of_a_kind:
                        n["modes_unattested_single"] += 1
                        factor = single
                    elif trust in ("sibling", "a20"):
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
                           twin_scenario=dict(sc) if sc else None, provenance="llm", _added=True)
                have[m] = None
                n["modes_added"] += 1
                if trust == "a20":
                    n["alarm_links_restored"] += restore_links(g, ty, m)
                n["effects_added"] += len(add["effects"])
    return n


_STATE: dict = {}


def install(kind: str, seed: str, what: str, label: str = "", trust: str = "a20", single: float = 0.5) -> None:
    import a18_complete as a18_vocab
    import a20_complete as a18_complete
    import kgx_run
    comp = json.loads((A18 / f"completion_{kind}_{seed}{label}.json").read_text())
    orig = kgx_run.degraded          # the diagnosis draw, kept before kgx_run.degraded is replaced below

    def degraded(kg, k):
        assert k == kind
        new, rep = orig(kg, kind) if seed == "diag" else a18_complete.degrade(kg, kind, seed)
        if "catalogue" not in _STATE:
            _STATE["catalogue"] = a18_vocab.vocab(kg)["scenarios"]
        rep = dict(rep or {})
        rep["completion"] = apply_completion(new, comp, _STATE["catalogue"], what, trust, single)
        return new, rep
    kgx_run.degraded = degraded
    kgx_run.install(kind)
    _STATE["comp_sha"] = hashlib.sha256((A18 / f"completion_{kind}_{seed}{label}.json").read_bytes()).hexdigest()


def job(args):
    arm, day, seed_case, seed_deg, outdir, label, trust, single = args
    base, kind, what = split(arm)
    f = Path(outdir) / arm / f"s{seed_case}.json"
    t0 = time.time()
    try:
        import kgx_run
        from adagent.harness import exp2
        if not getattr(exp2, "_a18", None):
            install(kind, seed_deg, what, label, trust, single)
            exp2._a18 = arm
        assert exp2._a18 == arm, "one arm per worker process"
        row = exp2.run_job(base, day, seed_case)
        row["arm"] = arm
        row["a18"] = {"kind": kind, "seed": seed_deg, "what": what, "label": label, "trust": trust, "single": single, "report": kgx_run._CUR.get("rep"),
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
    import a20_complete
    ap.add_argument("--seed", required=True, choices=a20_complete.DRAWS, help="the degradation draw")
    ap.add_argument("--day", type=float, default=2190.0)
    ap.add_argument("--label", default="", help="completion file suffix, e.g. _oracle or _empty")
    ap.add_argument("--trust", default="a20", choices=list(TRUSTS), help="trust rule for added modes")
    ap.add_argument("--nodup", action="store_true", help="drop one-of-a-kind additions documented on connected equipment")
    ap.add_argument("--single", type=float, default=0.5, help="rate factor for unattested modes on one-of-a-kind types")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    os.environ.setdefault("EXP2_FIT_CACHE", str(ROOT / "results" / "_fitcache"))
    seeds = kgx_run.seeds_arg(a.seeds)
    if any(7000 <= s <= 7319 or 4000 <= s <= 4319 for s in seeds):
        raise SystemExit("sealed seeds are not run here")
    if a.nodup:
        os.environ["A20_NODUP"] = "1"
    outdir = A18 / (a.seed + a.label + ("" if a.trust == "a20" else "_" + a.trust) + ("" if a.single == 0.5 else f"_s{a.single:g}")
                    + ("_nodup" if a.nodup else ""))
    t0 = time.time()
    for arm in a.arms.split(","):
        split(arm)
        (outdir / arm).mkdir(parents=True, exist_ok=True)
        jobs = [(arm, a.day, s, a.seed, str(outdir), a.label, a.trust, a.single) for s in seeds if not (outdir / arm / f"s{s}.json").exists()]
        print(f"{arm} ({a.seed}): {len(jobs)} jobs to run on {a.workers} workers", flush=True)
        with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=4) as pool:
            for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
                print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] " + line, flush=True)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
