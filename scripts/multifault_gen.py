"""Generate training and held-out cases for multiple-fault attribution (src/adagent/tools/multifault.py).

Run from the project root (no API calls):
    python scripts/multifault_gen.py --n 8000 --start 5000000 --out data/multifault_train.pkl --workers 6
    python scripts/multifault_gen.py --n 1000 --start 6000000 --out data/multifault_held.pkl --workers 6

A run is resumable: completed cases are written in parts beside the output (<out>.parts/), and a rerun with the
same arguments skips the seeds already done, then merges the parts into <out>.

The training world differs from the test world on purpose (the plan's section 1.5):
- networks from the reference generator with other seeds (100 to 159, never 24) and sizes (8 to 16 sites), taken at
  one of four days of their history (800 to 2190), with at most gnn.REGION_MAX lightpaths, so the model sees the
  whole network as one graph; the world-to-records gap of each network (calibrated, scale 1) is its own draw;
- monitoring tiers as the network's history deployed them by that day, with sites upgraded at random;
- every numeric fault parameter scaled by a factor drawn from exp(U(-0.4, 0.4)); instrument noise scaled per case by
  a factor from 0.5 to 2; the twin's calculation error drawn per case from 0 to 0.36 dB (the test world uses 0.18);
- the onset estimate off by up to a minute, as the framer's estimates are.
Each case holds a first fault (a documented hard or soft fault the twin can fit) and, in 70 per cent of cases, a
second fault of a class the ranker knows, its onset 0 to 6 hours after the first (half within five minutes, as the
test world's coincident faults, half log-uniform from five minutes to six hours), placed inside, on the boundary of
or outside the first fault's frame in equal measure. The question is asked ten minutes after the later onset. The
leader is obtained as the agent obtains it: the training world's twin fitted to the first fault's documented
candidate. In the other 30 per cent there is no second fault, so the residual is only the twin's and the
instruments' error (the model learns to abstain).

Long separations: the lake is simulated around the first onset and the last twenty minutes only (aligned to the
15-minute PM bins), which is all the twin reads; simulating six hours of per-second streams would not fit the budget.
Case seeds start at --start (5,000,000 and up recommended), far from any experiment seed.
"""
from __future__ import annotations

import argparse
import math
import pickle
import random
import sys
import time
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import scipy.sparse as sp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

NET_SEEDS = [s for s in range(100, 160) if s != 24]
DAYS = (800.0, 1200.0, 1600.0, 2190.0)
FIRST_MODES = ["FIBRE_CUT", "CONNECTOR_PULLED", "AMP_PUMP_FAIL", "TX_LASER_FAIL", "CARD_FAIL", "WSS_PORT_FAIL",
               "SPAN_LOSS_INCREASE", "CONNECTOR_DIRTY", "AMP_NF_RISE", "AMP_GAIN_DROP", "WSS_ATTEN_DRIFT",
               "WSS_FILTER_SHIFT", "RX_DEGRADATION", "DGD_RISE"]
SECOND_MODES = ["FIBRE_CUT", "SPAN_LOSS_INCREASE", "DGD_RISE", "CONNECTOR_PULLED", "CONNECTOR_DIRTY",
                "AMP_PUMP_FAIL", "AMP_GAIN_DROP", "AMP_NF_RISE", "WSS_ATTEN_DRIFT", "WSS_FILTER_SHIFT",
                "RX_DEGRADATION", "TX_LASER_FAIL", "CARD_FAIL", "INSTRUMENT_MISREPORT"]
POSITIONS = ("inside", "boundary", "outside")
P_SINGLE = 0.3
T1 = 600.0
ASK_AFTER = 600.0
TIERS = ["baseline", "power", "ocm", "coherent", "full"]
PM = 900.0


@lru_cache(maxsize=6)
def _ctx(seed: int, n_sites: int):
    from adagent.world.history import generate_history
    from adagent.world.reference import build_reference_national
    net = build_reference_national(seed=seed, n_sites=n_sites, n_hubs=max(3, n_sites // 3),
                                   n_services=10 * n_sites, gap_scale=1.0, gap_calibrated=True)
    return net, generate_history(net, seed=1)


@lru_cache(maxsize=1)
def _pool() -> list:
    """(network seed, size, day): feasible layouts with 4 to REGION_MAX lightpaths in service that day."""
    from adagent.tools.gnn import REGION_MAX
    ok = []
    for sd in NET_SEEDS:
        for n in range(8, 17, 2):
            try:
                net, h = _ctx(sd, n)
            except Exception:
                continue
            for day in DAYS:
                k = len(h.network_at(day).lightpaths)
                if 4 <= k <= REGION_MAX:
                    ok.append((sd, n, day))
    return ok


@lru_cache(maxsize=6)
def _graph(key):
    """The ranker graph of the records' network on that day (shared by every case on it)."""
    from adagent.harness.cases import sources
    from adagent.substrate.lifecycle import build_graph
    from adagent.tools import gnn as G
    from adagent.tools.twin import twin_network
    net, h = _ctx(key[0], key[1])
    kg = build_graph(h.events, key[2], sources()).kg
    design = (sources().practice.get("line_design") or {}).get("design", {})
    return G.build_graph(twin_network(kg, design))


def hyp_loc(f) -> str:
    """The location the documented candidate names for a world fault."""
    t = f.targets[0]
    if f.mode == "WSS_PORT_FAIL":
        return f"{t}|port"
    return t


def _element(net, f) -> str:
    t = f.targets[0].split("|")[0]
    return t


def _oms_of(net, el: str) -> list:
    if el in net.oms:
        return [el]
    if el in net.spans:
        return [net.spans[el].oms]
    if el in net.amps:
        return [net.amps[el].oms]
    return []


def position(net, kg, f0, f1) -> str:
    """Where the second fault lies against the first fault's frame: the structural scope the framer builds from the
    first fault's element (its lightpaths' signal paths and its services' closures). Inside: the second element is in
    that scope; boundary: not in it, but on an OMS that starts or ends at a site the scope's OMSs touch (or a
    transponder at such a site); outside: elsewhere."""
    from adagent.lake import Lake
    from adagent.substrate.access import Substrate
    from adagent.tools.frame import DeterministicFramer
    fr = DeterministicFramer(Substrate(kg, Lake(), now=0.0))
    a = _element(net, f0)
    scope = set(fr._anchor_scope(a)) if kg.has(a) else {a}
    b = _element(net, f1)
    if b in scope:
        return "inside"
    oms = {o for el in scope for o in _oms_of(net, el)} | {el for el in scope if el in net.oms}
    sites = {net.oms[o].src for o in oms} | {net.oms[o].dst for o in oms}
    if b in net.transponders:
        return "boundary" if net.transponders[b].node in sites else "outside"
    for o in _oms_of(net, b):
        if net.oms[o].src in sites or net.oms[o].dst in sites:
            return "boundary"
    return "outside"


def _scale(rng, f):
    f.params = {k: (v * math.exp(rng.uniform(-0.4, 0.4)) if isinstance(v, (int, float)) and k != "bias" else v)
                for k, v in f.params.items()}
    if "bias" in f.params:
        f.params["bias"] *= math.exp(rng.uniform(-0.4, 0.4))


def _lake(world, now: float):
    """The lake around the first onset and over the last twenty minutes, on PM bin boundaries."""
    if now <= T1 + 2400.0:
        return world.run(0.0, now)
    from adagent.lake import Lake
    t1a = math.ceil((T1 + 900.0) / PM) * PM
    t2a = math.floor((now - 1200.0) / PM) * PM
    out = Lake()
    for part in (world.run(0.0, t1a), world.run(t2a, now)):
        for o in part.observations:
            out.add(source=o.source, observable=o.observable, element=o.element, instrument=o.instrument,
                    t_start=o.t_start, t_end=o.t_end, value=o.value, sigma=o.sigma, period=o.period)
    return out


def one(seed: int):
    """One case, or None. A network whose records are inconsistent that day (a lightpath recorded before its line
    interfaces, which the twin cannot simulate) gives None, as does a draw with no usable case."""
    try:
        return _one(seed)
    except (KeyError, ValueError) as e:
        print(f"seed {seed}: skipped ({type(e).__name__}: {e})", flush=True)
        return None


def _one(seed: int):
    from adagent.harness.cases import sources
    from adagent.harness.cases2 import _events, _single
    from adagent.substrate.access import Substrate
    from adagent.substrate.lifecycle import build_graph
    from adagent.tools import multifault as MF
    from adagent.tools.twin import GraphTwin
    from adagent.tools.walks import single
    from adagent.world import simulator
    from adagent.world.history import LifecycleEvent
    from adagent.world.simulator import WorldSim
    rng = random.Random(seed)
    key = rng.choice(_pool())
    net0, h = _ctx(key[0], key[1])
    day = key[2]
    net = h.network_at(day)
    dep = dict(h.deployment_at(day))
    upgrade = []
    for s in sorted(dep):
        if rng.random() < 0.4:
            tier = rng.choice(TIERS)
            if TIERS.index(tier) > TIERS.index(dep[s]):
                dep[s] = tier
                upgrade.append(LifecycleEvent(day - 30.0, day - 30.0, "instrument_tier_deployed",
                                              {"site": s, "tier": tier}))
    kg = build_graph(list(h.events) + upgrade, day, sources()).kg
    noise0 = dict(simulator.NOISE)
    fn = rng.uniform(0.5, 2.0)
    for k in simulator.NOISE:
        simulator.NOISE[k] = noise0[k] * fn
    two = rng.random() >= P_SINGLE
    want = rng.choice(POSITIONS) if two else None
    delta = rng.uniform(0.0, 300.0) if rng.random() < 0.5 else math.exp(rng.uniform(math.log(300.0),
                                                                                    math.log(21600.0)))
    now = T1 + delta + ASK_AFTER
    calc = rng.uniform(0.0, 0.36)
    try:
        for _ in range(40):
            world = WorldSim(net, seed=seed, deployment=dep)
            f0 = _single(rng, net, world, rng.choice(FIRST_MODES), onset=T1)
            if f0 is None:
                continue
            _scale(rng, f0)
            f1 = None
            if two:
                for _ in range(30):
                    c = _single(rng, net, world, rng.choice(SECOND_MODES), onset=T1 + delta)
                    if c is None or not set(c.targets).isdisjoint(f0.targets):
                        continue
                    if _element(net, c) == _element(net, f0) or position(net, kg, f0, c) != want:
                        continue
                    f1 = c
                    break
                if f1 is None:
                    continue
                _scale(rng, f1)
            for f in [f0] + ([f1] if f1 else []):
                for ev in _events(f):
                    world.inject(ev)
            lake = _lake(world, now)
            sub = Substrate(kg, lake, now=now, day=day)
            onset = T1 + rng.choice([-60.0, -30.0, 0.0, 0.0, 0.0, 30.0]) + rng.uniform(-10.0, 10.0)
            twin = GraphTwin(sub, sources(), onset, calc_err_db=calc, seed=seed)
            leader = single(f0.mode, hyp_loc(f0))
            g = _graph(key)
            f = MF.case_features(twin, leader, graph=g)
            if f is None:
                continue                         # the twin declines the documented candidate: draw again
            y_mode = f1.mode if f1 else None
            y_targets = list(f1.targets) if f1 else []
            if f1 is not None and not MF.labels(g, y_mode, y_targets):
                continue
            return {"net": key, "X": sp.csr_matrix(np.asarray(f["X"], dtype=np.float32)), "y_mode": y_mode,
                    "y_targets": y_targets, "unexplained": f["unexplained"],
                    "meta": {"seed": seed, "first": f"{f0.mode}@{f0.targets[0]}",
                             "second": f"{f1.mode}@{f1.targets[0]}" if f1 else None, "position": want,
                             "delta_s": round(delta, 1), "noise": round(fn, 2), "calc_db": round(calc, 3),
                             "twin_sims": twin.cost.get("simulations")}}
        return None
    finally:
        simulator.NOISE.update(noise0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--start", type=int, default=5_000_000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--part", type=int, default=200, help="cases per part file (the unit of resumption)")
    a = ap.parse_args()
    if a.start < 3_000_000:
        raise SystemExit("training seeds start at 3,000,000 (experiment and ranker seeds lie below)")
    t0 = time.time()
    out = Path(a.out)
    parts = Path(str(out) + ".parts")
    parts.mkdir(parents=True, exist_ok=True)
    seeds = list(range(a.start, a.start + a.n))
    blocks = [seeds[i:i + a.part] for i in range(0, len(seeds), a.part)]
    todo = [b for b in blocks if not (parts / f"{b[0]}.pkl").exists()]
    if len(todo) < len(blocks):
        print(f"resuming: {len(blocks) - len(todo)} of {len(blocks)} parts done", flush=True)
    flat = [s for b in todo for s in b]
    done_n, kept = 0, 0
    if flat:
        with Pool(a.workers) as pool:
            buf, bi = [], 0
            for c in pool.imap(one, flat, chunksize=4):
                done_n += 1
                if c is not None:
                    buf.append(c)
                    kept += 1
                if done_n == sum(len(b) for b in todo[:bi + 1]):
                    (parts / f"{todo[bi][0]}.pkl").write_bytes(pickle.dumps(
                        {"seeds": todo[bi], "cases": buf}, protocol=pickle.HIGHEST_PROTOCOL))
                    buf, bi = [], bi + 1
                if done_n % 100 == 0 or done_n == len(flat):
                    el = time.time() - t0
                    print(f"[{done_n}/{len(flat)} {el / 60:.1f} min, {el / done_n:.2f} s/case wall] kept {kept}",
                          flush=True)
    cases = []
    for b in blocks:
        cases += pickle.loads((parts / f"{b[0]}.pkl").read_bytes())["cases"]
    # each case names its network; the graphs are stored once (a graph per case does not fit in memory)
    keys = sorted({c["net"] for c in cases})
    with Pool(a.workers) as pool:
        graphs = dict(zip(keys, pool.map(_graph, keys, chunksize=8)))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        pickle.dump({"graphs": graphs, "cases": cases}, fh, protocol=pickle.HIGHEST_PROTOCOL)
    two = sum(1 for c in cases if c["y_mode"])
    print(f"wrote {len(cases)} cases ({two} two-fault) to {out} in {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
