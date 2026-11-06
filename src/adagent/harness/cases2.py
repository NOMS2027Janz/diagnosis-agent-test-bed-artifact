"""Experiment 2 cases: anomalies drawn by kind ("The Diagnosis Agent Test Bed" §4; decision D9).

Experiment 1 drew a fault family uniformly and kept faults that someone would notice, which favours hard
failures. Experiment 2 draws by kind of anomaly, so that each kind has enough cases to be reported on its own:

  hard         a hard failure (all nine documented hard modes, including WSS port failure)
  soft         a soft degradation (all nine, including amplifier gain tilt)
  transient    a polarisation transient, mechanical disturbance, channel-loading transient or protection hit
  drift        an incipient drift (pump current, laser bias, margin), posed as early warning before any alarm
  monitoring   the monitoring itself is at fault: an instrument misreporting, or telemetry gone stale
  coincident   two independent faults a few minutes apart
  maintenance  a maintenance window at a site just before the fault: half the time the maintenance left the
               fault behind (a dirty or pulled connector there), half the time the fault is elsewhere
  common_cause a dig-up in a shared duct that cuts every cable in it

Each case carries labels for the six dimensions of the anomaly space; the question is what someone would
actually ask, as in Experiment 1, with two additions: operations asks about a reading that changed with no alarm
(monitoring faults), and about a slow trend (drifts, as early warning). The harness keeps every cause; the agent
receives only the question and the substrate. World version 2 (gap_scale) and the record-mismatch control
(records_complete) are passed through.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from ..substrate.access import Substrate
from ..substrate.lifecycle import build_graph
from ..world.faults import CATALOGUE, Event
from ..world.history import LifecycleEvent, equipment_types
from ..world.otn import client_port
from ..world.simulator import WorldSim
from .cases import (Case, Fault, Question, Truth, _acquirer, _impact, _question, _rev, context, sources)

KINDS = ["hard", "soft", "transient", "drift", "monitoring", "coincident", "maintenance", "common_cause"]
MODES = {
    "hard": ["FIBRE_CUT", "CONNECTOR_PULLED", "AMP_PUMP_FAIL", "TX_LASER_FAIL", "CARD_FAIL", "WSS_PORT_FAIL",
             "OTN_SWITCH_FAIL", "CLIENT_PORT_FAIL", "XC_MISCONNECT"],
    "soft": ["SPAN_LOSS_INCREASE", "CONNECTOR_DIRTY", "AMP_NF_RISE", "AMP_GAIN_DROP", "AMP_GAIN_TILT",
             "WSS_ATTEN_DRIFT", "WSS_FILTER_SHIFT", "RX_DEGRADATION", "DGD_RISE"],
    "transient": ["SOP_TRANSIENT", "MECHANICAL_DISTURBANCE", "CHANNEL_LOADING_TRANSIENT", "PROTECTION_SWITCH_HIT"],
    "drift": ["PUMP_CURRENT_DRIFT", "LASER_BIAS_DRIFT", "MARGIN_EROSION"],
    "monitoring": ["INSTRUMENT_MISREPORT", "TELEMETRY_STALE"],
}
# the spectral case set (spectral mode only; docs/campaign/spectral_design.md): filter faults, the laser look-alike,
# and the faults a filtering penalty is confused with; singles ("spectral") and pairs ("spectral_pair")
SPECTRAL_MODES = ["WSS_FILTER_SHIFT", "WSS_FILTER_NARROWING", "LASER_FREQ_DRIFT", "WSS_ATTEN_DRIFT", "AMP_NF_RISE",
                  "RX_DEGRADATION"]
SPECTRAL_KINDS = ["spectral", "spectral_pair"]
ONSET_S, ASK_S = 600.0, 1200.0


class NotObservable(ValueError):
    """The kind of anomaly cannot be noticed on the network as it stands that day (for example a drift before any
    site has health telemetry)."""


@dataclass
class TruthV2(Truth):
    faults: list = field(default_factory=list)      # every cause injected, primary first
    labels: dict = field(default_factory=dict)      # the six dimensions, plus kind


# ---------------------------------------------------------------------- targets

def _used_oms(net) -> list[str]:
    return sorted(o for o in net.oms if any(o in lp.oms_path or o in _rev(lp) for lp in net.lightpaths.values()))


def _params(rng: random.Random, mode: str) -> dict:
    out = {}
    for p in CATALOGUE[mode].parameters:
        if p.name == "position_km":
            continue
        if getattr(p, "prior", None) == "log_uniform":
            out[p.name] = math.exp(rng.uniform(math.log(p.low), math.log(p.high)))
        else:
            out[p.name] = rng.uniform(p.low, p.high)
    return out


def _single(rng: random.Random, net, world: WorldSim, mode: str, onset: float = ONSET_S) -> Optional[Fault]:
    """One fault of `mode` at a randomly drawn place, or None if the network offers no suitable place."""
    params = _params(rng, mode)
    dur = None
    if mode in ("FIBRE_CUT", "SPAN_LOSS_INCREASE", "DGD_RISE", "SOP_TRANSIENT", "MECHANICAL_DISTURBANCE"):
        sp = net.spans[rng.choice(sorted(net.spans))]
        targets = [sp.id]
        if mode == "FIBRE_CUT":
            o = net.oms[sp.oms]
            rev = net.oms[f"oms.{o.dst}-{o.src}"].spans
            targets.append(rev[len(rev) - 1 - sp.index].id)
        if mode in ("SOP_TRANSIENT", "MECHANICAL_DISTURBANCE"):
            dur = params["duration_s"]
    elif mode in ("AMP_PUMP_FAIL", "AMP_GAIN_DROP", "AMP_NF_RISE", "AMP_GAIN_TILT"):
        targets = [rng.choice(sorted(net.amps))]
    elif mode in ("TX_LASER_FAIL", "CARD_FAIL", "RX_DEGRADATION", "LASER_FREQ_DRIFT"):
        targets = [rng.choice(sorted(net.transponders))]
    elif mode in ("CONNECTOR_DIRTY", "CONNECTOR_PULLED", "WSS_PORT_FAIL", "CHANNEL_LOADING_TRANSIENT"):
        targets = [rng.choice(_used_oms(net))]
        if mode == "CHANNEL_LOADING_TRANSIENT":
            dur = params["duration_s"]
    elif mode in ("WSS_ATTEN_DRIFT", "WSS_FILTER_SHIFT", "WSS_FILTER_NARROWING"):
        lp = net.lightpaths[rng.choice(sorted(net.lightpaths))]
        targets = [f"{rng.choice(lp.oms_path)}|{lp.slot}"]
    elif mode in ("OTN_SWITCH_FAIL", "XC_MISCONNECT"):
        multi = [s for s in net.services.values() if s.working and len(net.odu_paths[s.working].hops) >= 2]
        if not multi:
            return None
        s = rng.choice(sorted(multi, key=lambda x: x.id))
        hop = rng.choice(net.odu_paths[s.working].hops[1:])
        targets = [f"otn.{hop.a}"] if mode == "OTN_SWITCH_FAIL" else [f"otn.{hop.a}|{s.working}"]
    elif mode == "CLIENT_PORT_FAIL":
        s = net.services[rng.choice(sorted(net.services))]
        targets = [client_port(s.id, rng.choice([s.src, s.dst]))]
    elif mode == "PROTECTION_SWITCH_HIT":          # a hit on a lightpath carrying protected services
        prot = sorted({h.lightpath for s in net.services.values() if s.working and s.protect
                       for h in net.odu_paths[s.working].hops})
        if not prot:
            return None
        targets = [rng.choice(prot)]
        dur = params["hit_ms"] / 1000.0
    elif mode in ("PUMP_CURRENT_DRIFT", "LASER_BIAS_DRIFT", "MARGIN_EROSION"):
        pool = (sorted(net.amps) if mode == "PUMP_CURRENT_DRIFT" else sorted(net.transponders))
        pool = [e for e in pool if world.has("health" if mode != "MARGIN_EROSION" else "coherent", e)]
        if not pool:
            return None
        targets = [rng.choice(pool)]
        # early warning: the drift started weeks ago and is still below any alarm threshold (120 per cent)
        if mode == "MARGIN_EROSION":
            level = rng.uniform(0.5, 2.5)                       # dB of margin lost so far
            days = level / params["db_per_day"]
        else:
            params["pct_per_day"] = rng.uniform(0.1, 1.0)       # slow ageing, so that early warning is meaningful
            level = rng.uniform(105.0, 118.0)                   # per cent of nominal so far
            days = (level / 100.0 - 1.0) * 100.0 / params["pct_per_day"]
        params["days_running"] = days
        return Fault(mode, targets, params, onset - days * 86400.0, None)
    elif mode in ("INSTRUMENT_MISREPORT", "TELEMETRY_STALE"):
        pool = [a for a in sorted(net.amps) if world.has("amp_power", a)] + \
               [t for t in sorted(net.transponders) if world.has("rx_power", t)]
        if not pool:
            return None
        targets = [rng.choice(pool)]
        if mode == "INSTRUMENT_MISREPORT":
            params["bias"] = rng.choice([-1, 1]) * rng.uniform(2.0, 6.0)
    else:
        raise ValueError(mode)
    return Fault(mode, targets, params, onset, dur)


def _events(f: Fault) -> list[Event]:
    prof = "ramp" if f.mode in ("PUMP_CURRENT_DRIFT", "LASER_BIAS_DRIFT", "MARGIN_EROSION") else "step"
    return [Event(f.mode, t, f.params, onset=f.onset_s, duration=f.duration_s, profile=prof) for t in f.targets]


# ---------------------------------------------------------------------- questions for anomalies with no impact

def _monitoring_question(net, f: Fault) -> Question:
    el = f.targets[0]
    what = "input and output power" if el in net.amps else "received power"
    t = int(f.onset_s)
    if f.mode == "INSTRUMENT_MISREPORT":
        return Question("operations", f"Monitoring shows the {what} reported by {el} changed by about "
                        f"{f.params['bias']:+.1f} dB at about t={t} s, with no alarm. What is the cause?", el, t)
    return Question("operations", f"Monitoring shows the {what} reported by {el} unchanged since about t={t} s "
                    "while neighbouring readings vary. What is the cause?", el, t)


def _drift_question(net, f: Fault) -> Question:
    el = f.targets[0]
    d = f.params["days_running"]
    span = f"about {d / 7.0:.0f} weeks" if d >= 14 else f"about {max(1, round(d))} days"
    if f.mode == "PUMP_CURRENT_DRIFT":
        txt = f"Monitoring shows the pump current of amplifier {el} rising over {span}"
    elif f.mode == "LASER_BIAS_DRIFT":
        txt = f"Monitoring shows the transmit laser bias current of {el} rising over {span}"
    else:
        txt = f"Monitoring shows the pre-FEC bit error rate on the channel received at {el} rising over {span}"
    return Question("operations", txt + ", with no alarm and no service affected yet. What is happening, and is "
                    "action needed?", el, ASK_S)


# ---------------------------------------------------------------------- labels

def _doc_modes(src, type_id: str) -> set:
    return {m["id"] for m in src.package_view(type_id).get("fault_modes", [])}


def _documented(src, types: dict, net, mode: str, target: str) -> str:
    """Whether documentation lists the mode for the equipment at the target: 'documented' (this vendor's type),
    'class_only' (the other vendor documents it for the same class), 'undocumented', or 'monitoring'."""
    if CATALOGUE[mode].nature.value == "substrate":
        return "monitoring"
    base = target.split("|")[0]
    if mode.startswith("WSS_"):
        el = f"roadm.{net.oms[base].dst}"
    elif base.startswith("otn."):
        el = base
    else:
        el = base
    ty = types.get(el)
    if ty is None:                                   # fibre plant: documented in operator plant practice
        plant = set().union(*(_doc_modes(src, t) for t, v in src.types.items() if v["path"].startswith("practice")))
        return "documented" if mode in plant else "undocumented"
    if mode in _doc_modes(src, ty):
        return "documented"
    cls_ = src.types[ty]["entry"]["class"]
    peers = [t for t, v in src.types.items() if v["entry"]["class"] == cls_ and t != ty]
    return "class_only" if any(mode in _doc_modes(src, t) for t in peers) else "undocumented"


def _site(net, world: WorldSim, target: str, mode: str = "") -> str:
    """The site whose instrumentation tier governs what can be seen of a fault at `target` (a WSS is at the node
    where its OMS ends)."""
    base = target.split("|")[0]
    if mode.startswith("WSS_") and base in net.oms:
        return net.oms[base].dst
    if base in net.spans:
        return net.oms[net.spans[base].oms].dst
    if base in net.oms:
        return net.oms[base].src
    if base in net.lightpaths:
        return net.transponders[net.lightpaths[base].dst_trx].node
    if base.startswith("otn."):
        return base[4:]
    return world.site_of(base)


def _how(q: Question) -> str:
    t = q.text
    if t.startswith("Alarm"):
        return "alarm"
    if "rising over" in t:
        return "trend"
    if t.startswith("Monitoring") or t.startswith("Performance"):
        return "monitoring"
    return "loss" if "a loss of service" in t else "interruption" if "brief interruption" in t else "errors"


# ---------------------------------------------------------------------- checks the world answers

def _checker(world: WorldSim, deployment: dict, case_seed: int):
    """The world's answer to each check, taken when the check completes: the question time plus the time of the
    checks before it, the travel to any site not yet visited, and the check's own minutes. A repeated request of
    the same check is a fresh trial."""
    from dataclasses import asdict
    from ..world.checks import check_sites, check_spec, perform, travel_hours
    state = {"hours": 0.0, "sites": set(), "attempts": {}}

    def run(check: str, params: dict) -> dict:
        key = (check, tuple(sorted(params.items())))
        attempt = state["attempts"].get(key, 0)
        state["attempts"][key] = attempt + 1
        hours = state["hours"]
        new_sites = [s for s in check_sites(world, check, params) if s not in state["sites"]]
        hours += sum(travel_hours(case_seed, s) for s in new_sites)
        hours += check_spec(check)[1] / 60.0 if check_spec(check) else 0.0
        r = perform(world, deployment, check, params, ASK_S + 3600.0 * hours, case_seed, attempt=attempt,
                    t_question=ASK_S)
        if r.status != "unavailable":
            state["hours"] = hours
            state["sites"].update(r.dispatch_sites)
        return asdict(r)
    return run


# ---------------------------------------------------------------------- the case

def make_case2(day: float, seed: int, kind: Optional[str] = None, net_seed: int = 24, hist_seed: int = 1,
               gap_scale: float = 1.0, records_complete: bool = False, max_draws: int = 60,
               procedures_in_graph: bool = False, gap_calibrated: bool = False,
               instrumentation: str = "status_quo", mismatch: int = 0, ladder: tuple = ()) -> Case:
    """An Experiment 2 case. kind None takes KINDS[seed % len(KINDS)], so consecutive seeds cycle the kinds.
    ladder: the capability ladder's rungs switched on in the world (world/ladder.py); they add observables only, so
    the fault drawn and the question asked are the same with or without them."""
    if kind is None and 5000 <= seed <= 5999:     # the spectral case set (development 5900-5915)
        from ..world import spectral
        if not spectral.on():
            raise ValueError("seeds 5000-5999 are the spectral case set: set ADAGENT_SPECTRAL=1")
        kind = SPECTRAL_KINDS[seed % len(SPECTRAL_KINDS)]
    kind = kind or KINDS[seed % len(KINDS)]
    h = context(net_seed, hist_seed, gap_scale, records_complete, gap_calibrated, mismatch)
    net = h.network_at(day)
    dep = h.deployment_at(day)
    upgrade: list = []
    if instrumentation == "future":
        # every site in service at the top tier the test bed models (coherent receiver quantities, triggered capture,
        # embedded OTDR, health and context), deployed and registered a month before the case
        dep = {s: "full" for s in dep}
        upgrade = [LifecycleEvent(day - 30.0, day - 30.0, "instrument_tier_deployed", {"site": s, "tier": "full"})
                   for s in sorted(dep)]
    elif instrumentation != "status_quo":
        raise ValueError(instrumentation)
    types = equipment_types(h.net)
    src = sources()
    rng = random.Random(f"exp2/{kind}/{seed}")
    if kind == "drift" and not any(t in ("coherent", "full") for t in dep.values()):
        raise NotObservable(f"no site has health telemetry on day {day:g}: early warning of a drift cannot be posed")
    for _ in range(max_draws):
        world = WorldSim(net, seed=seed, deployment=dep, ladder=frozenset(ladder))
        extra_events: list[LifecycleEvent] = []
        maint_related = None
        if kind in MODES:
            f = _single(rng, net, world, rng.choice(MODES[kind]))
            faults = [f] if f else []
        elif kind == "spectral":
            f = _single(rng, net, world, rng.choice(SPECTRAL_MODES))
            faults = [f] if f else []
        elif kind == "spectral_pair":
            a = _single(rng, net, world, rng.choice(SPECTRAL_MODES))
            b = _single(rng, net, world, rng.choice(SPECTRAL_MODES), ONSET_S + rng.uniform(30, 240))
            faults = [a, b] if a and b and set(a.targets).isdisjoint(b.targets) else []
        elif kind == "coincident":
            a = _single(rng, net, world, rng.choice(MODES["hard"] + MODES["soft"]))
            b = _single(rng, net, world, rng.choice(MODES["hard"] + MODES["soft"]), ONSET_S + rng.uniform(30, 240))
            faults = [a, b] if a and b and set(a.targets).isdisjoint(b.targets) else []
        elif kind == "maintenance":
            site = rng.choice(sorted(s for s in net.nodes if any(o.src == s for o in net.oms.values())))
            maint_related = rng.random() < 0.5
            if maint_related:
                oms = [o for o in _used_oms(net) if net.oms[o].src == site]
                f = Fault(rng.choice(["CONNECTOR_DIRTY", "CONNECTOR_PULLED"]), [rng.choice(oms)], {}, ONSET_S) \
                    if oms else None
                if f and f.mode == "CONNECTOR_DIRTY":
                    f.params = {"added_loss_db": rng.uniform(1.0, 6.0)}
            else:
                f = _single(rng, net, world, rng.choice(MODES["hard"] + MODES["soft"]))
            faults = [f] if f else []
            hours = rng.choice([2, 4])
            extra_events.append(LifecycleEvent(day - (hours + 0.5) / 24.0, day - (hours + 0.5) / 24.0,
                                               "maintenance_window", {"site": site, "duration_h": hours}))
        elif kind == "common_cause":
            ducts = [g for g in net.srlgs.values() if g.kind == "duct" and all(s in net.spans for s in g.spans)]
            g = rng.choice(sorted(ducts, key=lambda x: x.id)) if ducts else None
            faults = [Fault("FIBRE_CUT", sorted(g.spans), {"srlg": g.id}, ONSET_S)] if g else []
        else:
            raise ValueError(kind)
        if not faults:
            continue
        for f in faults:
            for ev in _events(f):
                world.inject(ev)
        f0 = faults[0]
        probe = Fault(f0.mode, f0.targets, f0.params, max(f0.onset_s, ONSET_S), f0.duration_s)   # drifts began long ago
        down, hit, degraded, alarms = _impact(world, probe, ASK_S)
        if kind == "monitoring":
            q = _monitoring_question(net, f0)
        elif kind == "drift":
            # early warning means before any alarm: none in the case at all (a drift that began long ago may have
            # raised one before the nominal onset)
            # (amendment 23: alarms the network raises with no fault at all, as a world with structural error can, are
            # not the drift's and do not bar the question)
            from ..lake import Lake
            def _raised(w):
                lk = Lake()
                w._alarms(w.segments(0.0, ASK_S), lk)
                return {(o.observable, o.element) for o in lk.query(source="alarm") if o.value != "not_alarmed"}
            standing = _raised(WorldSim(net, seed=seed, deployment=dep, ladder=frozenset(ladder)))
            on_alarm = {e.split(".")[1] for _, e in standing if e.startswith("trx.")}
            if alarms or _raised(world) - standing or any(t.startswith("trx.") and t.split(".")[1] in on_alarm
                                                          for t in f0.targets):
                continue        # (nor is a drift posed on a lightpath that already carries a standing alarm)
            q = _drift_question(net, f0)
        else:
            q = _question(rng, net, f0, down, hit, degraded, alarms)
        if q is None:
            continue
        break
    else:
        raise RuntimeError(f"no noticeable {kind} anomaly drawn")
    lake = world.run(0.0, ASK_S)
    kg = build_graph(list(h.events) + extra_events + upgrade, day, src, procedures_in_graph=procedures_in_graph).kg
    sub = Substrate(kg, lake, now=ASK_S, day=day, acquire_fn=_acquirer(world, dep),
                    check_fn=_checker(world, dep, seed))
    natures = sorted({CATALOGUE[f.mode].nature.value for f in faults})
    labels = {
        "kind": kind,
        "how": _how(q),
        "nature": natures[0] if len(natures) == 1 else "+".join(natures),
        "where": sorted({CATALOGUE[f.mode].applies_to[0] for f in faults}),
        "multiplicity": {"coincident": "coincident", "common_cause": "common_cause",
                         "spectral_pair": "coincident"}.get(kind, "single"),
        "documentation": sorted({_documented(src, types, net, f.mode, f.targets[0]) for f in faults}),
        "tier": dep.get(_site(net, world, f0.targets[0], f0.mode), "baseline"),
        "alarms": bool(alarms),
        "maintenance_related": maint_related,
        "gap_scale": gap_scale, "gap_calibrated": gap_calibrated, "records_complete": records_complete,
        **({"mismatch": mismatch} if mismatch else {}),
        "instrumentation": instrumentation,
        **({"ladder": sorted(set(ladder))} if ladder else {}),
    }
    truth = TruthV2(f0, down, hit, degraded, [(a, e) for a, e, _ in alarms], faults=faults, labels=labels)
    return Case(f"x{int(day)}-s{seed}-{kind}", day, q, truth, sub, world)
