"""Diagnosis cases on the reference network ("Reference Network, Data Sets and Knowledge Graph" §3.3,
§4.2, §10).

A case is posed at a chosen day of the history. The world is the network as it then stood, with the
instruments then deployed; the knowledge graph is the graph as recorded by that day. One fault is
injected at `onset_s` on the lake's clock and the question is asked at `ask_s`, when the simulation
stops. The question is what someone would actually ask:

- if a customer's service is down or errored, the customer on that service reports it;
- otherwise, if alarms or performance thresholds fired, operations asks about the most severe alarm;
- a fault that nobody could notice is not a case, and another is drawn.

The harness keeps the truth (fault and impact); the agent receives only the question and the substrate.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from ..substrate.access import Substrate
from ..substrate.lifecycle import Sources, build_graph
from ..world.faults import CATALOGUE, Event
from ..world.history import History, generate_history
from ..world.otn import client_port
from ..world.physics import evaluate
from ..world.reference import build_reference_national
from ..world.simulator import WorldSim

CASE_MODES = {
    "span": ["FIBRE_CUT", "SPAN_LOSS_INCREASE", "DGD_RISE", "SOP_TRANSIENT"],
    "amp": ["AMP_PUMP_FAIL", "AMP_GAIN_DROP", "AMP_NF_RISE"],
    "trx": ["TX_LASER_FAIL", "CARD_FAIL", "RX_DEGRADATION"],
    "oms": ["CONNECTOR_DIRTY", "CONNECTOR_PULLED"],
    "wss": ["WSS_ATTEN_DRIFT", "WSS_FILTER_SHIFT"],
    "otn": ["OTN_SWITCH_FAIL", "XC_MISCONNECT"],
    "port": ["CLIENT_PORT_FAIL"],
}
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2}


@dataclass
class Fault:
    mode: str
    targets: list
    params: dict = field(default_factory=dict)
    onset_s: float = 600.0
    duration_s: Optional[float] = None

    def key(self) -> str:
        return f"{self.mode}@{self.targets[0]}"


@dataclass
class Question:
    kind: str                     # customer | operations
    text: str
    anchor: str                   # service id or alarmed element
    reported_at_s: float


@dataclass
class Truth:
    fault: Fault
    services_down: list
    services_hit: list
    services_degraded: list       # margin fell by more than 1 dB on a path carrying the service
    alarms: list                  # (type, element) raised after onset


@dataclass
class Case:
    id: str
    day: float
    question: Question
    truth: Truth                  # harness only
    substrate: Substrate
    world: WorldSim               # harness only


@lru_cache(maxsize=4)
def context(net_seed: int = 24, hist_seed: int = 1, gap_scale: float = 0.0, records_complete: bool = False,
            gap_calibrated: bool = False, mismatch: int = 0) -> History:
    """The network and its history, with each ticket's record of what was seen at the time (cached in
    .cache/ under the project root after the first build, which takes about two minutes). gap_scale > 0 is
    world version 2 (world/gap.py); 0 is world version 1, unchanged. records_complete=True removes every mismatch
    between the records and the network (late and missing records, unrecorded shared risk), as a control."""
    from pathlib import Path
    from ..world.history import attach_observations
    net = build_reference_national(net_seed, gap_scale=gap_scale, gap_calibrated=gap_calibrated, mismatch=mismatch)
    if records_complete:                 # plant records show every shared cable and duct (protection as planned)
        for sp in net.spans.values():
            sp.recorded_cable, sp.recorded_duct = sp.cable, sp.duct
    h = generate_history(net, seed=hist_seed, records_complete=records_complete)
    root = Path(__file__).resolve().parents[3]
    tag = ("" if gap_scale == 0 else f"_g{gap_scale:g}") + ("_cal" if gap_calibrated and gap_scale else "") + \
        ("_rc" if records_complete else "") + (f"_m{mismatch}" if mismatch else "")
    from ..world import spectral
    tag += "_sp" if spectral.on() else ""
    attach_observations(h, root / ".cache" / f"observed_{net_seed}_{hist_seed}{tag}.json")
    return h


@lru_cache(maxsize=1)
def sources() -> Sources:
    return Sources.load()


def _draw_fault(rng: random.Random, net, onset_s: float) -> Fault:
    family = rng.choice(list(CASE_MODES))
    mode = rng.choice(CASE_MODES[family])
    params = {}
    for p in CATALOGUE[mode].parameters:
        if p.name == "position_km":
            continue
        if getattr(p, "prior", None) == "log_uniform":
            params[p.name] = math.exp(rng.uniform(math.log(p.low), math.log(p.high)))
        else:
            params[p.name] = rng.uniform(p.low, p.high)
    dur = None
    if mode == "SOP_TRANSIENT":
        dur = params.get("duration_s", 0.01)
    if family == "span":
        sp = net.spans[rng.choice(sorted(net.spans))]
        targets = [sp.id]
        if mode == "FIBRE_CUT":                      # a dig-up cuts the cable: both fibres
            o = net.oms[sp.oms]
            rev = net.oms[f"oms.{o.dst}-{o.src}"].spans
            targets.append(rev[len(rev) - 1 - sp.index].id)
    elif family == "amp":
        targets = [rng.choice(sorted(net.amps))]
    elif family == "trx":
        targets = [rng.choice(sorted(net.transponders))]
    elif family == "oms":
        targets = [rng.choice(sorted(o for o in net.oms if any(o in lp.oms_path or
                                                                  o in _rev(lp) for lp in net.lightpaths.values())))]
    elif family == "wss":
        lp = net.lightpaths[rng.choice(sorted(net.lightpaths))]
        targets = [f"{rng.choice(lp.oms_path)}|{lp.slot}"]
    elif family == "otn":
        multi = [s for s in net.services.values() if s.working and len(net.odu_paths[s.working].hops) >= 2]
        if not multi:
            return _draw_fault(rng, net, onset_s)
        s = rng.choice(sorted(multi, key=lambda x: x.id))
        hop = rng.choice(net.odu_paths[s.working].hops[1:])
        targets = [f"otn.{hop.a}"] if mode == "OTN_SWITCH_FAIL" else [f"otn.{hop.a}|{s.working}"]
    else:
        s = net.services[rng.choice(sorted(net.services))]
        targets = [client_port(s.id, rng.choice([s.src, s.dst]))]
    return Fault(mode, targets, params, onset_s, dur)


def _rev(lp):
    n = lp.nodes
    return [f"oms.{n[j + 1]}-{n[j]}" for j in range(len(n) - 1)]


def _impact(world: WorldSim, fault: Fault, ask_s: float):
    segs = world.segments(0.0, ask_s)
    before = next(s for s in segs if s.t0 <= fault.onset_s - 1 < s.t1)
    after = [s for s in segs if s.t1 > fault.onset_s]
    down, hit = set(), set()
    for s in after:
        for (sid, eg), st in s.svc.items():
            if not st.up:
                down.add(sid)
            elif before.svc[(sid, eg)].active != st.active:
                hit.add(sid)
    net = world.net
    degraded = set()
    a0 = after[0].snap.rx
    for k, r in before.snap.rx.items():
        if k in a0 and r.margin_db - a0[k].margin_db > 1.0:
            for p in net.odu_paths.values():
                if any(h.lightpath == k[0] for h in p.hops) and net.services.get(p.id.split(".")[1]):
                    degraded.add(p.id.split(".")[1])
    from ..lake import Lake
    lake = Lake()
    world._alarms(segs, lake)
    alarms = sorted({(o.observable, o.element, o.value) for o in lake.query(source="alarm")
                     if o.t_start >= fault.onset_s and o.value != "not_alarmed"})
    return sorted(down), sorted(hit - down), sorted(degraded - down - hit), alarms


def make_case(day: float, seed: int, net_seed: int = 24, hist_seed: int = 1, fault: Optional[Fault] = None,
              onset_s: float = 600.0, ask_s: float = 1200.0, max_draws: int = 50,
              procedures_in_graph: bool = False, gap_scale: float = 0.0, records_complete: bool = False) -> Case:
    h = context(net_seed, hist_seed, gap_scale, records_complete)
    net = h.network_at(day)
    dep = h.deployment_at(day)
    rng = random.Random(seed)
    for _ in range(max_draws):
        f = fault or _draw_fault(rng, net, onset_s)
        world = WorldSim(net, seed=seed, deployment=dep)
        world.inject(Event(f.mode, f.targets[0], f.params, onset=f.onset_s, duration=f.duration_s))
        for extra in f.targets[1:]:
            world.inject(Event(f.mode, extra, f.params, onset=f.onset_s, duration=f.duration_s))
        down, hit, degraded, alarms = _impact(world, f, ask_s)
        q = _question(rng, net, f, down, hit, degraded, alarms)
        if q is not None:
            break
        if fault is not None:
            raise ValueError("the given fault is not noticeable")
    else:
        raise RuntimeError("no noticeable fault drawn")
    lake = world.run(0.0, ask_s)
    kg = build_graph(h.events, day, sources(), procedures_in_graph=procedures_in_graph).kg
    sub = Substrate(kg, lake, now=ask_s, day=day,
                    acquire_fn=_acquirer(world, dep))
    truth = Truth(f, down, hit, degraded, [(a, e) for a, e, _ in alarms])
    return Case(f"d{int(day)}-s{seed}", day, q, truth, sub, world)


def _question(rng, net, f: Fault, down, hit, degraded, alarms) -> Optional[Question]:
    t_rep = int(round((f.onset_s + rng.uniform(-120, 120)) / 60.0)) * 60   # people report times roughly
    if down or hit or degraded:
        pool = down or hit or degraded
        sid = rng.choice(pool)
        s = net.services[sid]
        what = "a loss of service" if sid in down else ("a brief interruption" if sid in hit else "errors")
        if sid in degraded and sid not in down and sid not in hit:
            # a margin loss is not visible to the customer unless errors follow; ask as operations
            return _ops_question(f, alarms) or Question(
                "operations", f"Performance monitoring shows degraded signal quality on service {sid} "
                f"({s.rate} between {s.src} and {s.dst}) from about t={t_rep} s. What is the cause?",
                sid, t_rep)
        return Question("customer", f"The customer on service {sid} ({s.rate} between {s.src} and {s.dst}) "
                        f"reports {what} from about t={t_rep} s. What is the cause?", sid, t_rep)
    return _ops_question(f, alarms)


def _ops_question(f: Fault, alarms) -> Optional[Question]:
    if not alarms:
        return None
    a, el, sev = min(alarms, key=lambda x: (SEVERITY_RANK.get(x[2], 9), x[0], x[1]))
    return Question("operations", f"Alarm {a} ({sev}) was raised on {el} at about t={int(f.onset_s)} s. "
                    f"What is the cause?", el, f.onset_s)


def _acquirer(world: WorldSim, deployment: dict):
    def acquire(kind: str, params: dict):
        if kind == "otdr":
            span = params["span"]
            site = world.site_of(world.net.amps[f"{world.net.spans[span].oms}.amp0"].id)
            if deployment.get(site) != "full":
                return None
            from ..lake import Lake
            trace = world.acquire_otdr(span, params.get("t", world.events[0].onset + 1))
            return Lake().add(source="instrument", observable="otdr_trace", element=span, instrument=span,
                              t_start=params.get("t", 0.0), t_end=None, value=trace)
        return None
    return acquire
