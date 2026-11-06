"""Generate training and held-out cases for the transient event recogniser (src/adagent/tools/transient.py).

Run from the project root (no API calls):
    python scripts/transient_gen.py --cases 8000 --start 3000000 --out data/transient_train.pkl --workers 6
    python scripts/transient_gen.py --cases 1000 --start 4000000 --out data/transient_held.pkl --workers 6

The training world differs from the test world on purpose:
- networks from the reference generator with other seeds (100 to 159) and sizes (8 to 16 sites); never seed 24;
- every numeric fault parameter scaled by a factor drawn from exp(U(-0.4, 0.4)), so ranges are wider than documented;
- instrument noise scaled per case by a factor from 0.5 to 2;
- monitoring tiers drawn per site at random;
- the onset estimate shifted by -120 to +60 s, as the framer's estimates are.
Each case holds one of: a transient of one of the four kinds, a lasting (hard or soft) fault, or nothing, in equal
measure across the six. Case seeds start at --start (3,000,000 and up), far from any experiment seed.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CATS = ["PROTECTION_SWITCH_HIT", "CHANNEL_LOADING_TRANSIENT", "MECHANICAL_DISTURBANCE", "SOP_TRANSIENT",
        "lasting", "nothing"]
TIER_CHOICES = ["baseline", "power", "power", "ocm", "coherent", "coherent", "full"]
NET_SEEDS = [s for s in range(100, 160) if s != 24]


@lru_cache(maxsize=8)
def _net(seed: int, n_sites: int):
    from adagent.world.reference import build_reference_national
    return build_reference_national(seed=seed, n_sites=n_sites, n_hubs=max(3, n_sites // 3),
                                    n_services=10 * n_sites)


@lru_cache(maxsize=1)
def _pool() -> list:
    """The (network seed, size) pairs the reference generator builds; a few combinations have no feasible layout."""
    ok = []
    for sd in NET_SEEDS:
        for n in range(8, 17, 2):
            try:
                net = _net(sd, n)
            except Exception:
                continue
            if net.lightpaths and net.oms:
                ok.append((sd, n))
    return ok


def one(seed: int):
    from adagent.harness.cases2 import MODES, _events, _single
    from adagent.tools import transient as T
    from adagent.world import simulator
    from adagent.world.simulator import WorldSim
    rng = random.Random(seed)
    key = rng.choice(_pool())
    net = _net(*key)
    dep = {s: rng.choice(TIER_CHOICES) for s in net.nodes}
    noise0 = dict(simulator.NOISE)
    f = rng.uniform(0.5, 2.0)
    for k in simulator.NOISE:
        simulator.NOISE[k] = noise0[k] * f
    try:
        for _ in range(30):
            cat = rng.choice(CATS)
            w = WorldSim(net, seed=seed, deployment=dep)
            fault = None
            if cat == "lasting":
                fault = _single(rng, net, w, rng.choice(MODES["hard"] + MODES["soft"]))
                if fault is None:
                    continue
            elif cat != "nothing":
                fault = _single(rng, net, w, cat)
                if fault is None:
                    continue
                fault.params = {k: (v * math.exp(rng.uniform(-0.4, 0.4)) if isinstance(v, (int, float)) else v)
                                for k, v in fault.params.items()}
            if fault is not None:
                for ev in _events(fault):
                    w.inject(ev)
            break
        else:
            return None
        lake = w.run(0.0, 1200.0)
        # the framer's onset estimate is off by whole minutes at times (seen: -120, -60, 0, +60 s)
        onset = 600.0 + rng.choice([-120.0, -60.0, 0.0, 0.0, 0.0, 60.0, 60.0]) + rng.uniform(-10.0, 10.0)
        g = T.build_graph(net, T.net_service_lightpaths(net))
        X = T.node_features(g, lambda ob: lake.query(observable=ob), onset)
        y_kind, y_loc, y_dur = 0, [], None
        if cat in T.KINDS:
            y_kind = T.KINDS.index(cat)
            tgt = fault.targets[0]
            host = tgt.rsplit(".", 1)[0] if cat == "SOP_TRANSIENT" else tgt
            if host not in g.index:
                return None
            y_loc = [g.index[host]]
            d = fault.params.get("duration_s")
            if d is None and "hit_ms" in fault.params:
                d = fault.params["hit_ms"] / 1000.0
            y_dur = math.log10(d) if d else None
        return {"net": key, "X": np.asarray(X, dtype=np.float32), "y_kind": y_kind, "y_loc": y_loc, "y_dur": y_dur,
                "meta": {"seed": seed, "cat": cat, "mode": getattr(fault, "mode", None), "noise": round(f, 2)}}
    finally:
        simulator.NOISE.update(noise0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, required=True)
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    if a.start < 3_000_000:
        raise SystemExit("training seeds start at 3,000,000 (experiment and ranker seeds lie below)")
    t0 = time.time()
    seeds = list(range(a.start, a.start + a.cases))
    out = []
    with Pool(a.workers) as pool:
        for i, c in enumerate(pool.imap(one, seeds, chunksize=8), 1):
            if c is not None:
                out.append(c)
            if i % 200 == 0 or i == len(seeds):
                print(f"[{i}/{len(seeds)} {(time.time() - t0) / 60:.0f} min] kept {len(out)}", flush=True)
    # each case names its network; the graphs are stored once (a graph per case does not fit in memory)
    from adagent.tools import transient as T
    graphs = {k: T.build_graph(_net(*k), T.net_service_lightpaths(_net(*k))) for k in sorted({c["net"] for c in out})}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "wb") as fh:
        pickle.dump({"graphs": graphs, "cases": out}, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"wrote {len(out)} cases to {a.out} in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
