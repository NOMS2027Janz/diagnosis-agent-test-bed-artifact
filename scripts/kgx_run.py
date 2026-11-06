"""The knowledge graph's quality: which parts matter (campaign to the NOMS 2027 paper; spec/PROTOCOL_CAMPAIGN.md,
amendment "the knowledge graph degraded").

The world is untouched; only what the agent and the procedures know about it is degraded. The agent's code is not
changed either: this wrapper builds each case as usual, makes a degraded copy of the knowledge graph, and hands the
agent's side (framer, generation lines, winnowing loop, procedures) a view of the substrate whose graph is the copy.
The world's side (checks, the data lake, scoring) keeps the true graph. One degradation is fixed for every case,
as an operator's records carry one set of errors, drawn with a fixed seed.

Degradations (arm suffix):
  -kg-inst   wrong or missing instance records: 5% of equipment recorded with the wrong type (another type of the
             same kind), 30% of spans' cable and duct records lost, 10% of protection records lost, 10% of
             instrument records lost, 5% of cross-connect records lost. The twin, built from the records, inherits them.
  -kg-flat   a weaker relation model: the layered dependency relations (what carries, traverses, terminates, hosts,
             protects what) are kept only as untyped connectivity between the same two nodes, without direction,
             order, ends or role; each node keeps its kind. The reasoning tools must take dependency from adjacency.
             The twin keeps the true line-design model, which in practice comes from planning data.
  -kg-sig    missing fault knowledge, signatures: for 30% of (equipment type, fault mode) pairs the documentation
             names the fault mode but gives no effects, and no alarm lists it as a probable cause.
  -kg-modes  missing fault knowledge, fault modes: 20% of (equipment type, fault mode) pairs removed from the
             documentation altogether (never all of a type's modes).
  -kg-none   the control: an undegraded copy through the same wrapper; it must reproduce the arms without it.
Base arms: A-full-cd (the agent with the "can this decide?" step) and B (manual troubleshooting), status quo.

Run from the project root, for example:
    python scripts/kgx_run.py --arms A-full-cd-kg-inst,B-kg-inst --seeds 2000-2159 --workers 6
Each job writes results/campaign/kgx/<arm>/s<seed>.json; finished jobs are skipped, so the command resumes.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

KINDS = ("none", "inst", "flat", "sig", "modes")
SEED = 20261023
LAYERED = ("carriedBy", "traverses", "oduHop", "working", "protecting", "crossConnectedAt", "feeds", "partOf",
           "terminatesAt", "endsAt", "hostedOn", "hasPort", "protectedBy")


# ---------------------------------------------------------------------------------------------- degradations
def _copy(kg):
    from adagent.substrate.kg import KnowledgeGraph, QueryCost
    new = KnowledgeGraph()
    new.g = kg.g.copy()
    new.cost = QueryCost()
    new.log = list(kg.log)
    new.record_quality = dict(kg.record_quality)
    return new


def _drop(g, rel: str, share: float, rng: random.Random, by=None) -> int:
    """Drop the edges of a relation for a share of their owners (by: 'u' the source node)."""
    edges = [(u, v, k) for u, v, k, d in g.edges(keys=True, data=True) if d.get("rel") == rel]
    owners = sorted({u for u, _, _ in edges}) if by == "u" else None
    if owners is not None:
        hit = set(rng.sample(owners, round(share * len(owners))))
        gone = [e for e in edges if e[0] in hit]
    else:
        gone = rng.sample(sorted(edges), round(share * len(edges)))
    g.remove_edges_from(gone)
    return len(gone)


def degrade_inst(kg, rng: random.Random) -> dict:
    g = kg.g
    out = {}
    # wrong type: another equipment type used by elements of the same kind
    typed = sorted((u, v) for u, v, d in g.edges(data=True) if d.get("rel") == "hasType")
    by_cls: dict = {}
    for u, v in typed:
        by_cls.setdefault(g.nodes[u].get("cls"), set()).add(v)
    cand = [(u, v) for u, v in typed if len(by_cls[g.nodes[u].get("cls")]) > 1]
    wrong = rng.sample(cand, round(0.05 * len(cand)))
    for u, v in wrong:
        other = rng.choice(sorted(by_cls[g.nodes[u].get("cls")] - {v}))
        for k in [k for k, d in g.get_edge_data(u, v).items() if d.get("rel") == "hasType"]:
            g.remove_edge(u, v, k)
        g.add_edge(u, other, rel="hasType")
    out["wrong_type"] = len(wrong)
    out["shared_risk_lost"] = _drop(g, "inCable", 0.30, rng, by="u") + _drop(g, "inDuct", 0.30, rng, by="u")
    out["protection_lost"] = _drop(g, "protectedBy", 0.10, rng) + _drop(g, "protecting", 0.10, rng)
    out["instrument_lost"] = _drop(g, "monitoredBy", 0.10, rng)
    out["cross_connect_lost"] = _drop(g, "crossConnectedAt", 0.05, rng)
    return out


class _Unknown(dict):
    """The attributes of a relation recorded as bare connectivity: order, direction, ends and role unknown."""
    def __missing__(self, key):
        return 0 if key == "order" else None


def flat_class(kg):
    """A graph whose layered relations are bare connectivity (see the module note)."""
    from adagent.substrate.kg import KnowledgeGraph

    class FlatGraph(KnowledgeGraph):
        def _adj(self, nid: str, rel: str, outward: bool) -> list:
            tc = self._targets.get((self.g.nodes[nid].get("cls") if nid in self.g else None, rel, outward), set())
            res = []
            for m in self._conn.get(nid, ()):
                if self.g.nodes[m].get("cls") in tc:
                    if rel == "carriedBy":           # direction unknown: the connection serves either direction
                        res += [(m, _Unknown(direction=d)) for d in ("fwd", "rev")]
                    else:
                        res.append((m, _Unknown()))
            self.cost.charge(1 + len(res))
            return res

        def out(self, nid, rel):
            return self._adj(nid, rel, True) if rel in LAYERED else KnowledgeGraph.out(self, nid, rel)

        def inn(self, nid, rel):
            if rel == "endsAt":            # a lightpath's two ends keep their names (trx.och138.a, trx.och138.z)
                res = [(lp, _Unknown(end=d.get("end"))) for lp, d in KnowledgeGraph.inn(self._true, nid, rel)]
                self.cost.charge(1 + len(res))
                return res
            return self._adj(nid, rel, False) if rel in LAYERED else KnowledgeGraph.inn(self, nid, rel)

        def lightpath_ends(self, lp):
            """Which transceiver is the a end and which the z end is part of its name, as inventory names carry it:
            flattening the relations loses their types and roles, not the names of the elements."""
            ends = KnowledgeGraph.lightpath_ends(self._true, lp)
            self.cost.charge(1 + len(ends))
            return ends

    new = FlatGraph()
    new.g = kg.g.copy()
    new.cost = kg.cost.__class__()
    new.log = list(kg.log)
    new.record_quality = dict(kg.record_quality)
    # what kind of node each relation reaches from each kind (the schema keeps node kinds, not relation kinds)
    tg: dict = {}
    conn: dict = {}
    for u, v, d in kg.g.edges(data=True):
        r = d.get("rel")
        if r in LAYERED:
            cu, cv = kg.g.nodes[u].get("cls"), kg.g.nodes[v].get("cls")
            tg.setdefault((cu, r, True), set()).add(cv)
            tg.setdefault((cv, r, False), set()).add(cu)
            conn.setdefault(u, set()).add(v)
            conn.setdefault(v, set()).add(u)
    new._targets = tg
    new._conn = {k: sorted(v) for k, v in conn.items()}
    new._true = kg
    return new


def _pairs(g):
    return sorted((u, v) for u, v, d in g.edges(data=True) if d.get("rel") == "hasFaultMode")


def degrade_sig(kg, rng: random.Random) -> dict:
    g = kg.g
    pairs = _pairs(g)
    hit = set(rng.sample(pairs, round(0.30 * len(pairs))))
    for u, v in hit:
        for k, d in list(g.get_edge_data(u, v).items()):
            if d.get("rel") == "hasFaultMode":
                g.edges[u, v, k]["effects"] = []
    n_pc = 0
    for u, a, k, d in list(g.edges(keys=True, data=True)):
        if d.get("rel") == "canRaise" and d.get("probable_causes"):
            keep = [pc for pc in d["probable_causes"] if (u, pc.get("mode")) not in hit]
            n_pc += len(d["probable_causes"]) - len(keep)
            g.edges[u, a, k]["probable_causes"] = keep
    return {"signatures_removed": len(hit), "probable_causes_removed": n_pc}


def degrade_modes(kg, rng: random.Random) -> dict:
    g = kg.g
    pairs = _pairs(g)
    by_type: dict = {}
    for u, v in pairs:
        by_type.setdefault(u, []).append(v)
    gone = []
    for u, v in rng.sample(pairs, len(pairs)):
        if len(gone) >= round(0.20 * len(pairs)):
            break
        if len(by_type[u]) > 1:
            by_type[u].remove(v)
            gone.append((u, v))
    for u, v in gone:
        for k in [k for k, d in g.get_edge_data(u, v).items() if d.get("rel") == "hasFaultMode"]:
            g.remove_edge(u, v, k)
    n_pc = 0
    for u, a, k, d in list(g.edges(keys=True, data=True)):
        if d.get("rel") == "canRaise" and d.get("probable_causes"):
            keep = [pc for pc in d["probable_causes"] if (u, pc.get("mode")) not in set(gone)]
            n_pc += len(d["probable_causes"]) - len(keep)
            g.edges[u, a, k]["probable_causes"] = keep
    return {"modes_removed": len(gone), "probable_causes_removed": n_pc}


def degraded(kg, kind: str):
    rng = random.Random(f"{SEED}:{kind}")
    if kind == "flat":
        return flat_class(kg), {"layered_relations": "connectivity only"}
    new = _copy(kg)
    if kind == "none":          # the control: the wrapper with an undegraded copy must change nothing
        return new, {}
    rep = {"inst": degrade_inst, "sig": degrade_sig, "modes": degrade_modes}[kind](new, rng)
    return new, rep


# ---------------------------------------------------------------------------------------------- the agent's view
class AgentView:
    """The substrate as the agent's side sees it: the degraded graph; everything else is the world's."""
    def __init__(self, sub, kg):
        object.__setattr__(self, "_sub", sub)
        object.__setattr__(self, "kg", kg)

    def __getattr__(self, name):
        return getattr(self._sub, name)

    def __setattr__(self, name, value):
        setattr(self._sub, name, value)


_CUR: dict = {}


def install(kind: str) -> None:
    """Patch the harness so that the agent's side of every case sees the degraded graph."""
    from adagent.harness import exp2
    from adagent.tools import twin as twin_mod

    orig_make = exp2.make_case2

    def make_case2(*a, **k):
        case = orig_make(*a, **k)
        kgd, rep = degraded(case.substrate.kg, kind)
        _CUR.update(sub=case.substrate, kg=case.substrate.kg, view=AgentView(case.substrate, kgd), kgd=kgd, rep=rep)
        return case

    def swap(x):
        if _CUR and x is _CUR.get("sub"):
            return _CUR["view"]
        if _CUR and x is _CUR.get("kg"):
            return _CUR["kgd"]
        return x

    def wrap(cls):
        def f(first, *a, **k):
            return cls(swap(first), *a, **k)
        return f

    exp2.make_case2 = make_case2
    exp2.DeterministicFramer = wrap(exp2.DeterministicFramer)
    exp2.WinnowingLoop = wrap(exp2.WinnowingLoop)
    exp2.ManualTroubleshooting = wrap(exp2.ManualTroubleshooting)
    exp2.AlarmCorrelation = wrap(exp2.AlarmCorrelation)
    exp2.GroundingTool = wrap(exp2.GroundingTool)
    for name in ("AbductionWalk", "RandomWalkRanking", "AlarmCorrelation", "ProcedureTool"):
        setattr(exp2, name, wrap(getattr(exp2, name)))
    orig_lines = exp2.lines_for

    def lines_for(case, *a, **k):
        if _CUR and case.substrate is _CUR.get("sub"):
            class C:            # the case as the agent's side sees it
                substrate = _CUR["view"]
            return orig_lines(C, *a, **k)
        return orig_lines(case, *a, **k)
    exp2.lines_for = lines_for
    # MinimumExplanation and AnalogySearch take the graph as first argument too
    exp2.MinimumExplanation = wrap(exp2.MinimumExplanation)
    exp2.AnalogySearch = wrap(exp2.AnalogySearch)
    if kind == "flat":
        # a procedure that moves its unit along untyped connectivity can step back the way it came: an engineer
        # does not revisit the same step at the same unit on one trace, so such a branch ends there
        from adagent.tools import procedures as proc_mod
        orig_step = proc_mod.ProcedureTool._step

        def _step(self, frame, pid, node, unit, found, depth, path, out, tests):
            stack = self.__dict__.setdefault("_kgx_stack", [])
            key = (pid, node, unit)
            if key in stack:
                out.append({"causes": [], "unit": unit, "path": path + [node, "(loop: unit revisited)"]})
                return
            stack.append(key)
            try:
                return orig_step(self, frame, pid, node, unit, found, depth, path, out, tests)
            finally:
                stack.pop()
        proc_mod.ProcedureTool._step = _step
        # the twin keeps the true line-design model
        on, od = twin_mod.twin_network, twin_mod.twin_deployment
        twin_mod.twin_network = lambda kg, design: on(getattr(kg, "_true", kg), design)
        twin_mod.twin_deployment = lambda kg: od(getattr(kg, "_true", kg))


# ---------------------------------------------------------------------------------------------- running
def split(arm: str) -> tuple[str, str]:
    base, _, kind = arm.rpartition("-kg-")
    assert kind in KINDS and base in ("A-full-cd", "B"), arm
    return base, kind


def job(args):
    arm, day, seed, outdir = args
    base, kind = split(arm)
    f = Path(outdir) / arm / f"s{seed}.json"
    t0 = time.time()
    try:
        from adagent.harness import exp2
        if not getattr(exp2, "_kgx", None):
            install(kind)
            exp2._kgx = kind
        assert exp2._kgx == kind, "one degradation per worker process"
        row = exp2.run_job(base, day, seed)
        row["arm"] = arm
        row["kgx"] = {"kind": kind, "report": _CUR.get("rep")}
        _CUR.clear()
        f.write_text(json.dumps(row, default=str))
        sc = row["score"]
        return f"{arm:<16} s{seed}  {row['case']:<28} {sc['outcome']:<26} effort={sc['effort_h']:.1f}h  {time.time() - t0:.0f}s"
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"arm": arm, "seed": seed, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"{arm:<16} s{seed}  FAILED {repr(e)[:120]}"


def seeds_arg(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", required=True)
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--day", type=float, default=2190.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="results/campaign/kgx")
    a = ap.parse_args()
    import os
    os.environ.setdefault("EXP2_FIT_CACHE", str(ROOT / "results" / "_fitcache"))
    outdir = ROOT / a.out
    seeds = seeds_arg(a.seeds)
    if any(7000 <= s <= 7319 for s in seeds):
        raise SystemExit("seeds 7000-7319 are sealed for the C4 record run")
    if any(4000 <= s <= 4319 for s in seeds):
        raise SystemExit("seeds 4000-4319 are sealed for the record run")
    t0 = time.time()
    for arm in a.arms.split(","):          # one arm at a time: a worker process carries one degradation
        split(arm)
        (outdir / arm).mkdir(parents=True, exist_ok=True)
        jobs = [(arm, a.day, s, str(outdir)) for s in seeds if not (outdir / arm / f"s{s}.json").exists()]
        print(f"{arm}: {len(jobs)} jobs to run on {a.workers} workers", flush=True)
        if a.workers <= 1:
            for i, j in enumerate(jobs, 1):
                print(f"[{i}/{len(jobs)}] " + job(j), flush=True)
        else:
            with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=4) as pool:
                for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
                    print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] " + line, flush=True)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
