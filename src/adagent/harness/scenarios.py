"""Scenarios and correct answers (specification §9).

A scenario injects one or more events into a reference network at a chosen instrumentation
tier. The correct-answer record is derived by script from the scenario and the world simulator,
never from the agent. Its central field is the equivalence class: every alternative cause that
the scenario's passive instrumentation could not tell apart from the truth (§9.2), found by
re-simulating alternatives with their free parameter scanned over its range.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from ..defaults import SCORING
from ..world.alarms import defects
from ..world.faults import CATALOGUE, Event, effects_at
from ..world.network import Network, reference_line, reference_mesh
from ..world.physics import FEC_BER, evaluate
from ..world.simulator import NOISE, TIERS

NETWORKS = {"line6": reference_line, "mesh6": reference_mesh}

# modes the world simulator can produce and that act on a single element (v0 scope)
SIMULABLE = {
    "FibreSpan": ["FIBRE_CUT", "SPAN_LOSS_INCREASE", "DGD_RISE"],
    "Amplifier": ["AMP_PUMP_FAIL", "AMP_NF_RISE", "AMP_GAIN_DROP"],
    "CoherentTransponder": ["TX_LASER_FAIL", "RX_DEGRADATION", "CARD_FAIL"],
    "OMS": ["CONNECTOR_DIRTY", "CONNECTOR_PULLED"],
}


@dataclass
class CauseSpec:
    fault_mode: str
    element: str
    params: dict[str, float] = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.fault_mode}@{self.element}"


@dataclass
class Gold:
    true_causes: list[CauseSpec]
    answerable: bool
    equivalence_class: list[str]            # cause keys indistinguishable from the truth
    affected_receivers: list[str]
    location_tolerance_km: float = SCORING.span_location_tolerance_km


@dataclass
class Scenario:
    id: str
    seed: int
    network: str
    tier: str
    events: list[Event]
    question: str
    anchors: list[str]
    ask_time: float
    window: tuple[float, float] = (0.0, 1200.0)   # the agent sees nothing after the question
    traps: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------ observable signatures

def elements_of_class(net: Network, cls: str) -> list[str]:
    if cls == "FibreSpan":
        return sorted(net.spans)
    if cls == "Amplifier":
        return sorted(net.amps)
    if cls == "CoherentTransponder":
        return sorted(net.transponders)
    if cls == "OMS":
        return sorted(net.oms)
    return []


def class_of(net: Network, element: str) -> str:
    if element in net.spans:
        return "FibreSpan"
    if element in net.amps:
        return "Amplifier"
    if element in net.transponders:
        return "CoherentTransponder"
    if element in net.oms:
        return "OMS"
    raise KeyError(element)


def signature(net: Network, causes: list[CauseSpec], tier: str) -> tuple[frozenset, dict[str, tuple[float, float]]]:
    """Discrete alarm set and continuous observables (value, sigma) under passive instrumentation."""
    evs = [Event(c.fault_mode, c.element, c.params, onset=0.0) for c in causes]
    fx = effects_at(evs, 1.0)
    snap = evaluate(net, fx)
    alarms = frozenset((d.type, d.element) for d in defects(net, snap, cards=fx.card_failed))
    streams = TIERS[tier]
    cont: dict[str, tuple[float, float]] = {}

    def put(k, v, s):
        cont[k] = (v if v > -math.inf else -99.0, s)
    for (lp, d), r in snap.rx.items():
        put(f"pm_rx:{r.trx}", r.rx_power_dbm, NOISE["power_db"])
        cont[f"es:{r.trx}"] = (1.0 if (r.pre_fec_ber >= FEC_BER or r.rx_power_dbm == -math.inf) else 0.0, 1e-6)
        if "coherent" in streams:
            put(f"osnr:{r.trx}", r.osnr_db, NOISE["osnr_db"])
            put(f"log10ber:{r.trx}", math.log10(max(r.pre_fec_ber, 1e-30)), 0.05)
            put(f"dgd:{r.trx}", r.dgd_ps, 0.3)
    if "amp_power" in streams:
        for aid, a in snap.amps.items():
            put(f"amp_in:{aid}", a.input_dbm_per_ch, NOISE["power_db"])
            put(f"amp_out:{aid}", a.output_dbm_per_ch, NOISE["power_db"])
    if "ocm" in streams:
        for (oms, slot), (p, o) in snap.ocm.items():
            put(f"ocm_p:{oms}|{slot}", p, NOISE["power_db"])
            put(f"ocm_osnr:{oms}|{slot}", o, NOISE["osnr_db"])
    return alarms, cont


def distinguishable(a, b, k_sigma: float = SCORING.equivalence_sigma) -> bool:
    (al_a, ca), (al_b, cb) = a, b
    if al_a != al_b:
        return True
    for key in ca.keys() | cb.keys():
        va, sa = ca.get(key, (0.0, 1e-6))
        vb, sb = cb.get(key, (0.0, 1e-6))
        if abs(va - vb) > k_sigma * math.hypot(sa, sb):
            return True
    return False


def _mismatch(target, alt) -> float:
    """Largest normalised residual between two signatures; infinite if the alarms differ."""
    (al_a, ca), (al_b, cb) = target, alt
    if al_a != al_b:
        return math.inf
    worst = 0.0
    for key in ca.keys() | cb.keys():
        va, sa = ca.get(key, (0.0, 1e-6))
        vb, sb = cb.get(key, (0.0, 1e-6))
        worst = max(worst, abs(va - vb) / math.hypot(sa, sb))
    return worst


def _best_fit(net: Network, target, mode: str, el: str, tier: str) -> float:
    """Smallest mismatch over the mode's free parameter: a coarse grid, then a bounded refine."""
    fm = CATALOGUE[mode]
    numeric = [p for p in fm.parameters if p.name != "position_km"]
    if not numeric:
        return _mismatch(target, signature(net, [CauseSpec(mode, el, {})], tier))
    p = numeric[0]
    f = lambda v: _mismatch(target, signature(net, [CauseSpec(mode, el, {p.name: float(v)})], tier))
    n = 24
    grid = [p.low + (p.high - p.low) * i / (n - 1) for i in range(n)]
    vals = [f(v) for v in grid]
    i = min(range(n), key=lambda j: vals[j])
    if vals[i] == math.inf:
        return math.inf
    from scipy.optimize import minimize_scalar
    lo, hi = grid[max(0, i - 1)], grid[min(n - 1, i + 1)]
    g = lambda v: min(f(v), 1e9)
    res = minimize_scalar(g, bounds=(lo, hi), method="bounded", options={"xatol": 1e-3})
    return min(vals[i], float(res.fun))


def equivalence_class(net: Network, truth: list[CauseSpec], tier: str, candidates: list[str]) -> list[str]:
    """Single-cause alternatives on the candidate elements that the tier cannot separate from the
    truth: some value of the alternative's free parameter reproduces every passive observable
    within the equivalence threshold and raises the same alarms (specification §9.2)."""
    target = signature(net, truth, tier)
    eq = {"+".join(sorted(c.key() for c in truth))}
    for el in candidates:
        for mode in SIMULABLE.get(class_of(net, el), []):
            if _best_fit(net, target, mode, el, tier) <= SCORING.equivalence_sigma:
                eq.add(CauseSpec(mode, el).key())
    return sorted(eq)


def affected_receivers(net: Network, causes: list[CauseSpec]) -> list[str]:
    nom = evaluate(net)
    fx = effects_at([Event(c.fault_mode, c.element, c.params, onset=0.0) for c in causes], 1.0)
    bad = evaluate(net, fx)
    out = []
    for k, r in bad.rx.items():
        n = nom.rx[k]
        if (abs(r.rx_power_dbm - n.rx_power_dbm) > 0.5 or abs(r.gsnr_db - n.gsnr_db) > 0.3
                or r.pre_fec_ber >= FEC_BER):
            out.append(r.trx)
    return sorted(set(out))


def upstream_elements(net: Network, rx_trx: str) -> list[str]:
    out = []
    for lp in net.lightpaths.values():
        for direction, (tx, rx) in (("fwd", (lp.src_trx, lp.dst_trx)), ("rev", (lp.dst_trx, lp.src_trx))):
            if rx != rx_trx:
                continue
            nodes = lp.nodes if direction == "fwd" else lp.nodes[::-1]
            path = [net.oms_between(nodes[j], nodes[j + 1]) for j in range(len(nodes) - 1)]
            out.append(tx)
            for o in path:
                out.append(o)
                out += [e.id for e in net.oms[o].elements]
            out.append(rx)
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------ generation

def make_scenario(seed: int, network: str = "line6", tier: str = "power",
                  mode: Optional[str] = None) -> tuple[Scenario, Gold, Network]:
    rng = random.Random(seed)
    net = NETWORKS[network]()
    for _ in range(50):
        m = mode or rng.choice([x for xs in SIMULABLE.values() for x in xs])
        cls = next(c for c, ms in SIMULABLE.items() if m in ms)
        el = rng.choice(elements_of_class(net, cls))
        params = {}
        for p in CATALOGUE[m].parameters:
            if p.name == "position_km":
                params[p.name] = round(rng.uniform(1, net.spans[el].length_km - 1), 1) if el in net.spans else 0.0
            else:
                lo = max(p.low, 0.3 * p.high)          # keep faults large enough to matter
                params[p.name] = round(rng.uniform(lo, p.high), 2)
        truth = [CauseSpec(m, el, params)]
        hit = affected_receivers(net, truth)
        if hit:
            break
    else:
        raise RuntimeError("could not draw a consequential fault")
    ev = Event(m, el, params, onset=600.0)
    svc_rx = hit[0]
    question = (f"The customer on the service terminating at {svc_rx} reports a problem from about "
                f"t=600 s. What is the cause?")
    candidates = list(dict.fromkeys(e for rx in hit for e in upstream_elements(net, rx)))
    gold = Gold(true_causes=truth, answerable=True,
                equivalence_class=equivalence_class(net, truth, tier, candidates),
                affected_receivers=hit)
    sc = Scenario(id=f"{network}-{tier}-{seed}", seed=seed, network=network, tier=tier, events=[ev],
                  question=question, anchors=[svc_rx], ask_time=1200.0)
    return sc, gold, net
