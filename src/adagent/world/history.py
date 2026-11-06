"""Simulated operating history of a network ("Reference Network, Data Sets and Knowledge Graph",
revision 3, §4.2).

The history has four strands, each emitting lifecycle events in time order:

1. build-out: sites, then links, come into service in phases; each equipment type is onboarded
   when its first element is installed;
2. provisioning: lightpaths and client services are provisioned over the years, and a few
   services are later removed;
3. instrumentation roll-out: each site moves from the baseline tier upwards over the years, hubs
   first;
4. operations: faults occur at the catalogue's base rates on elements in service, are repaired
   and closed as trouble tickets with the explanation recorded at the time and how it was
   verified. Maintenance windows are recorded, and a few of them cause faults.

Each event has an actual time and a recorded time (when the record reaches the management
systems); the lag is usually zero but sometimes days, and a few instruments are deployed without
being registered at all. The knowledge graph's lifecycle processes and the experience store's
closure process read events by recorded time; they never see `Incident.truth`, which is for the
harness only.

Times are in days from the start of the history.
"""
from __future__ import annotations

import copy
import math
import random
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .faults import CATALOGUE
from .network import Network
from .otn import client_port

TIER_ORDER = ["baseline", "power", "ocm", "coherent", "full"]


@dataclass
class LifecycleEvent:
    t: float                    # actual time (days)
    t_recorded: Optional[float] # when the record reaches management systems; None = never
    kind: str                   # site_in_service | link_in_service | equipment_type_onboarded |
                                # equipment_installed | lightpath_provisioned | service_provisioned |
                                # service_removed | instrument_tier_deployed | maintenance_window |
                                # incident_closed | ticket_followup
    payload: dict


@dataclass
class Incident:
    id: str
    truth: dict                 # fault_mode, targets, params, onset (days): harness only
    ticket: dict                # what operations recorded: visible to the closure process


@dataclass
class History:
    net: Network                # the network as finally built (world-side)
    horizon_days: float
    events: list[LifecycleEvent]
    incidents: list[Incident]
    site_in: dict
    link_in: dict
    lp_in: dict
    svc_in: dict
    svc_out: dict
    tier_steps: dict            # site -> [(t, tier)]
    equipment_type: dict        # element id -> equipment type id

    # ------------------------------------------------------------------ views at a time
    def recorded_by(self, t: float) -> list[LifecycleEvent]:
        return [e for e in self.events if e.t_recorded is not None and e.t_recorded <= t]

    def deployment_at(self, t: float) -> dict[str, str]:
        out = {}
        for s, steps in self.tier_steps.items():
            if self.site_in.get(s, math.inf) > t:
                continue
            tier = "baseline"
            for ts, tr in steps:
                if ts <= t:
                    tier = tr
            out[s] = tier
        return out

    def network_at(self, t: float) -> Network:
        """The part of the network in service at time t (world-side truth)."""
        n = self.net
        links = {k: v for k, v in n.links.items() if self.link_in[k] <= t}
        oms = {k: o for k, o in n.oms.items() if o.link in links}
        lps = {k: lp for k, lp in n.lightpaths.items() if self.lp_in[k] <= t}
        svcs = {k: s for k, s in n.services.items() if self.svc_in[k] <= t < self.svc_out.get(k, math.inf)}
        paths = {p for s in svcs.values() for p in (s.working, s.protect) if p}
        trx = {k: v for k, v in n.transponders.items() if k.rsplit(".", 1)[0][4:] in lps}
        sites = [s for s in n.nodes if self.site_in[s] <= t]
        sub = Network(n.name, sites, links, oms,
                      {k: v for k, v in n.spans.items() if v.oms in oms},
                      {k: v for k, v in n.amps.items() if v.oms in oms},
                      trx, lps, svcs, n.launch_dbm_per_ch, n.drop_target_dbm, n.channels_loaded, n.slot_ghz,
                      sites={s: n.sites[s] for s in sites}, hubs=[h for h in n.hubs if h in sites],
                      otn_switches={k: v for k, v in n.otn_switches.items() if v.site in sites},
                      odu_paths={k: v for k, v in n.odu_paths.items() if k in paths},
                      srlgs=n.srlgs, link_km={k: n.link_km[k] for k in links}, spectral=n.spectral,
                      gap_scale=n.gap_scale, calibration_db=n.calibration_db)
        sub.nli_factor, sub.mismatch = n.nli_factor, n.mismatch   # amendment 22: the structural mismatch travels with the network
        return sub


# ---------------------------------------------------------------------- management-system records
# What inventory, plant and provisioning systems hold about each item when it is recorded. Plant
# records give the *recorded* cable and duct of each span, so unrecorded shared risk stays hidden.

def rec_site(net: Network, s: str) -> dict:
    x, y = net.sites[s]
    return {"site": s, "x_km": x, "y_km": y, "hub": s in net.hubs}


def rec_link(net: Network, lk: str) -> dict:
    a, b = net.links[lk]
    oms = []
    for o in net.oms.values():
        if o.link != lk:
            continue
        els = []
        for e in o.elements:
            if hasattr(e, "role"):
                els.append({"id": e.id, "kind": "amplifier", "role": e.role, "index": e.index})
            else:
                els.append({"id": e.id, "kind": "span", "index": e.index, "length_km": e.length_km,
                            "nominal_loss_db": round(e.nominal_loss_db, 2), "aerial": e.aerial,
                            "cable": e.recorded_cable or e.cable, "duct": e.recorded_duct or e.duct})
        oms.append({"id": o.id, "src": o.src, "dst": o.dst, "elements": els})
    return {"link": lk, "a": a, "b": b, "length_km": net.link_km.get(lk), "oms": oms}


def rec_element(net: Network, el: str, ty: Optional[str]) -> dict:
    rec = {"element": el, "type": ty}
    if el in net.amps:
        a = net.amps[el]
        o = net.oms[a.oms]
        rec.update(cls="Amplifier", oms=a.oms, role=a.role, index=a.index,
                   site=o.dst if a.role == "preamp" else o.src, gain_set_db=round(a.gain_db, 2))
    elif el in net.transponders:
        t = net.transponders[el]
        rec.update(cls="LineInterface", site=t.node, kind=t.kind, rate_g=t.rate_g, modulation=t.modulation,
                   baud_gbd=t.baud_gbd, host=t.host)
    elif el.startswith("roadm."):
        rec.update(cls="ROADM", site=el[6:])
    elif el.startswith("otn."):
        rec.update(cls="OTNSwitch", site=el[4:])
    elif el.startswith("cp."):
        _, sid, site = el.split(".")
        rec.update(cls="ClientPort", site=site, service=sid, rate=net.services[sid].rate)
    return rec


def rec_lightpath(net: Network, l: str) -> dict:
    lp = net.lightpaths[l]
    return {"lightpath": l, "nodes": list(lp.nodes), "oms_path": list(lp.oms_path), "slot": lp.slot,
            "rate_g": lp.rate_g, "kind": lp.kind, "src_trx": lp.src_trx, "dst_trx": lp.dst_trx}


def rec_service(net: Network, sid: str) -> dict:
    s = net.services[sid]

    def path(pid):
        if not pid:
            return None
        p = net.odu_paths[pid]
        return {"id": p.id, "odu": p.odu, "hops": [{"lightpath": h.lightpath, "a": h.a, "z": h.z} for h in p.hops]}
    return {"service": sid, "src": s.src, "dst": s.dst, "rate": s.rate, "protection": s.protection,
            "working": path(s.working), "protect": path(s.protect)}


# ---------------------------------------------------------------------- equipment types

def equipment_types(net: Network) -> dict[str, str]:
    """Vendor-neutral equipment types: vendor VA in the western half of the plane, VB in the east."""
    xs = sorted(x for x, _ in net.sites.values())
    mid = xs[len(xs) // 2]
    vendor = {s: ("VA" if net.sites[s][0] < mid else "VB") for s in net.sites}
    out = {}
    for s in net.nodes:
        out[f"roadm.{s}"] = f"{vendor[s]}-ROADM-CDC"
    for a in net.amps.values():
        v = vendor[net.oms[a.oms].src]
        out[a.id] = f"{v}-OA-{ {'booster': 'BST', 'inline': 'ILA', 'preamp': 'PRE'}[a.role] }"
    for t in net.transponders.values():
        v = vendor[t.node]
        kind = {"transponder": "TPD", "muxponder": "MXP", "otn_line": "OTN-LC"}[t.kind]
        out[t.id] = f"{v}-{kind}-{t.rate_g}"
    for sw in net.otn_switches.values():
        out[sw.id] = f"{vendor[sw.site]}-OTNSW"
    for sid, s in net.services.items():
        for site in (s.src, s.dst):
            out[client_port(sid, site)] = f"{vendor[site]}-CP-{s.rate}"
    return out


# ---------------------------------------------------------------------- tickets

COARSE = {
    "FIBRE_CUT": "fibre break, cable repaired", "SPAN_LOSS_INCREASE": "high span loss, fibre route checked",
    "CONNECTOR_DIRTY": "connectors cleaned, cleared", "CONNECTOR_PULLED": "patch cord reseated, cleared",
    "AMP_PUMP_FAIL": "amplifier card replaced", "AMP_NF_RISE": "amplifier card replaced",
    "AMP_GAIN_DROP": "amplifier card replaced", "PUMP_CURRENT_DRIFT": "amplifier card replaced",
    "TX_LASER_FAIL": "transponder replaced", "CARD_FAIL": "card replaced", "RX_DEGRADATION": "transponder replaced",
    "LASER_BIAS_DRIFT": "transponder replaced", "MARGIN_EROSION": "channel re-tuned",
    "DGD_RISE": "PMD issue suspected, route under observation", "SOP_TRANSIENT": "line hits, no fault found",
    "MECHANICAL_DISTURBANCE": "line hits, no fault found", "WSS_ATTEN_DRIFT": "WSS re-calibrated",
    "WSS_FILTER_SHIFT": "WSS re-calibrated", "OTN_SWITCH_FAIL": "switch fabric card replaced",
    "CLIENT_PORT_FAIL": "client optic replaced", "XC_MISCONNECT": "cross-connect corrected",
}
CONFUSED = {
    "SPAN_LOSS_INCREASE": "CONNECTOR_DIRTY", "CONNECTOR_DIRTY": "SPAN_LOSS_INCREASE",
    "AMP_NF_RISE": "RX_DEGRADATION", "RX_DEGRADATION": "AMP_NF_RISE", "AMP_GAIN_DROP": "SPAN_LOSS_INCREASE",
    "TX_LASER_FAIL": "CARD_FAIL", "CARD_FAIL": "TX_LASER_FAIL", "DGD_RISE": "RX_DEGRADATION",
    "CONNECTOR_PULLED": "FIBRE_CUT", "FIBRE_CUT": "CONNECTOR_PULLED", "WSS_ATTEN_DRIFT": "CONNECTOR_DIRTY",
    "WSS_FILTER_SHIFT": "RX_DEGRADATION", "MARGIN_EROSION": "RX_DEGRADATION",
}


def _ticket(rng: random.Random, inc_id: str, mode: str, targets: list[str], onset: float) -> dict:
    nature = CATALOGUE[mode].nature.value
    r = rng.random()
    if nature == "transient":
        if r < 0.6:
            rec_mode, loc, quality = None, None, "no_fault_found"
        else:
            rec_mode, loc, quality = mode, targets[0], "coarse"
        verification = "engineer_concluded"
    else:
        p_acc, p_coarse = {"hard": (0.80, 0.15), "soft": (0.60, 0.25)}.get(nature, (0.70, 0.20))
        if r < p_acc:
            rec_mode, loc, quality = mode, targets[0], "accurate"
        elif r < p_acc + p_coarse:
            rec_mode, loc, quality = None, targets[0], "coarse"
        else:
            rec_mode, loc, quality = CONFUSED.get(mode, mode), targets[0], "wrong"
        p_rep = 0.9 if nature == "hard" else 0.5
        verification = "repair_confirmed" if rng.random() < p_rep else "engineer_concluded"
    shown = rec_mode or mode
    text = "no fault found, monitoring" if quality == "no_fault_found" else COARSE.get(shown, "resolved")
    dur = {"hard": rng.uniform(0.1, 0.6), "soft": rng.uniform(0.5, 10), "transient": rng.uniform(0.05, 2),
           "incipient": rng.uniform(2, 30)}.get(nature, 1.0)
    return {"id": inc_id, "opened": round(onset, 4), "closed": round(onset + dur, 4),
            "recorded_fault_mode": rec_mode, "recorded_location": loc, "explanation": text,
            "explanation_quality": quality, "verification": verification,
            "outcome": "no_fault_found" if quality == "no_fault_found" else "resolved"}


def _incident(inc_id: str, truth: dict, tk: dict) -> "Incident":
    """How good the recorded explanation is, is known only to the harness: it moves to the truth."""
    truth["ticket_quality"] = tk.pop("explanation_quality")
    return Incident(inc_id, truth, tk)


# ---------------------------------------------------------------------- generator

def _sample_params(rng: random.Random, mode: str) -> dict:
    out = {}
    for p in CATALOGUE[mode].parameters:
        if p.name == "position_km":
            continue
        if getattr(p, "prior", None) == "log_uniform":
            out[p.name] = math.exp(rng.uniform(math.log(p.low), math.log(p.high)))
        else:
            out[p.name] = rng.uniform(p.low, p.high)
    return out


def generate_history(net: Network, seed: int = 0, horizon_days: float = 6 * 365.0,
                     records_complete: bool = False) -> History:
    """records_complete=True removes every mismatch between the records and the network (an experimental
    control, decision of 2026-10-16): every lifecycle event is recorded at once, every instrument deployment is
    registered, and the plant records show every shared cable and duct. The network, its faults and the
    schedule are the same either way (the random stream is consumed identically)."""
    rng = random.Random(seed)
    ev: list[LifecycleEvent] = []

    def lag() -> float:
        v = 0.0 if rng.random() < 0.95 else rng.uniform(1, 14)
        return 0.0 if records_complete else v

    # ---- build-out: hubs first, then the rest in waves
    site_in = {}
    for s in net.nodes:
        site_in[s] = rng.uniform(0, 120) if s in net.hubs else rng.uniform(60, 540)
    link_in = {lk: max(site_in[a], site_in[b]) + rng.uniform(0, 60) for lk, (a, b) in net.links.items()}
    etype = equipment_types(net)
    onboarded: set[str] = set()
    install = []                                       # (t, element)
    for s in net.nodes:
        ev.append(LifecycleEvent(site_in[s], site_in[s], "site_in_service", rec_site(net, s)))
        install.append((site_in[s], f"roadm.{s}"))
        if f"otn.{s}" in net.otn_switches:
            install.append((site_in[s], f"otn.{s}"))
    for lk, (a, b) in net.links.items():
        t = link_in[lk]
        spans = [sp.id for o in net.oms.values() if o.link == lk for sp in o.spans]
        ev.append(LifecycleEvent(t, t + lag(), "link_in_service", rec_link(net, lk)))
        for o in net.oms.values():
            if o.link == lk:
                install += [(t, am.id) for am in o.amps]

    # ---- provisioning: each service after its lightpaths' links are in service
    def lp_ready(lid):
        lp = net.lightpaths[lid]
        return max(link_in[net.oms[o].link] for o in lp.oms_path)

    svc_in, svc_out, lp_in = {}, {}, {}
    for sid, s in net.services.items():
        lids = {h.lightpath for p in (s.working, s.protect) if p for h in net.odu_paths[p].hops}
        ready = max(lp_ready(l) for l in lids)
        svc_in[sid] = rng.uniform(ready + 7, max(ready + 14, horizon_days * 0.85))
        if rng.random() < 0.05:
            svc_out[sid] = rng.uniform(svc_in[sid] + 30, horizon_days + 365)
        for l in sorted(lids):                         # sorted: a set's order varies between processes
            lp_in[l] = min(lp_in.get(l, math.inf), svc_in[sid] - rng.uniform(1, 7))
    for l in net.lightpaths:
        lp_in.setdefault(l, lp_ready(l) + 30)
        lp_in[l] = max(lp_in[l], lp_ready(l))
        lp = net.lightpaths[l]
        ev.append(LifecycleEvent(lp_in[l], lp_in[l] + lag(), "lightpath_provisioned", rec_lightpath(net, l)))
        install += [(lp_in[l], lp.src_trx), (lp_in[l], lp.dst_trx)]
    for sid, s in net.services.items():
        ev.append(LifecycleEvent(svc_in[sid], svc_in[sid] + lag(), "service_provisioned", rec_service(net, sid)))
        install += [(svc_in[sid], client_port(sid, s.src)), (svc_in[sid], client_port(sid, s.dst))]
        if sid in svc_out and svc_out[sid] < horizon_days:
            ev.append(LifecycleEvent(svc_out[sid], svc_out[sid] + lag(), "service_removed", {"service": sid}))
    for t, el in sorted(install, key=lambda x: (x[0], x[1])):     # onboard each type at its first install
        ty = etype.get(el)
        if ty and ty not in onboarded:
            onboarded.add(ty)
            ev.append(LifecycleEvent(t - 1, t - 1, "equipment_type_onboarded", {"type": ty}))
        ev.append(LifecycleEvent(t, t + lag(), "equipment_installed", rec_element(net, el, ty)))

    # ---- instrumentation roll-out: power within the first year, then OCM, coherent, full
    tier_steps = {}
    for s in net.nodes:
        hub = s in net.hubs
        t0 = site_in[s]
        steps = [(t0 + rng.uniform(30, 300), "power"),
                 (rng.uniform(400, 900) if hub else rng.uniform(700, 1500), "ocm"),
                 (rng.uniform(800, 1300) if hub else rng.uniform(1100, 2000), "coherent")]
        if hub and rng.random() < 0.5:
            steps.append((rng.uniform(1500, 2100), "full"))
        times = sorted(max(t, t0) for t, _ in steps)          # tiers only ever improve
        steps = list(zip(times, ["power", "ocm", "coherent", "full"][:len(times)]))
        tier_steps[s] = steps
        for t, tr in steps:
            if t < horizon_days:
                ev.append(LifecycleEvent(t, t + lag(), "instrument_tier_deployed", {"site": s, "tier": tr}))
    # about 5 per cent of deployments are never registered in the management systems
    deps = [e for e in ev if e.kind == "instrument_tier_deployed"]
    for e in rng.sample(deps, max(1, round(0.05 * len(deps)))):
        if not records_complete:
            e.t_recorded = None

    # ---- operations
    incidents: list[Incident] = []
    pools = {
        "FibreSpan": [(sp.id, net.oms[sp.oms].link) for sp in net.spans.values()],
        "Cable": [(sp.id, net.oms[sp.oms].link) for sp in net.spans.values()       # one per cable section
                  if net.oms[sp.oms].src < net.oms[sp.oms].dst],
        "Amplifier": [(a.id, net.oms[a.oms].link) for a in net.amps.values()],
        "CoherentTransponder": [(t, None) for t in net.transponders],
        "OMS": [(o.id, o.link) for o in net.oms.values()],
        "OTNSwitch": [(s, None) for s in net.otn_switches],
        "ClientPort": [(client_port(sid, x), sid) for sid, s in net.services.items() for x in (s.src, s.dst)],
        "OCh": [(l, None) for l in net.lightpaths],
    }

    def in_service(el, extra):
        if el in net.spans or el in net.amps or el in net.oms:
            return link_in[extra]
        if el in net.transponders:
            return lp_in[el.rsplit(".", 1)[0][4:]]
        if el in net.lightpaths:
            return lp_in[el]
        if el.startswith("cp."):
            return svc_in[extra]
        if el.startswith("otn."):
            return site_in[el[4:]]
        return 0.0

    skip = {"WSS_PORT_FAIL", "AMP_GAIN_TILT", "PROTECTION_SWITCH_HIT", "CHANNEL_LOADING_TRANSIENT",
            "XC_MISCONNECT", "MAINTENANCE_ACTIVITY", "PROVISIONING_CHANGE", "WEATHER_EXPOSURE",
            "INSTRUMENT_MISREPORT", "TELEMETRY_STALE", "INVENTORY_DRIFT"}
    n_inc = 0
    for mode, fm in CATALOGUE.items():
        if mode in skip or not fm.base_rate_per_year:
            continue
        classes = ["Cable"] if mode == "FIBRE_CUT" else fm.applies_to
        for cls in classes:
            for el, extra in pools.get(cls, []):
                rate = fm.base_rate_per_year
                if el in net.spans and net.spans[el].aerial and mode in ("FIBRE_CUT", "SOP_TRANSIENT",
                                                                           "MECHANICAL_DISTURBANCE"):
                    rate *= 3
                t = in_service(el, extra)
                while True:
                    t += rng.expovariate(rate / 365.0)
                    if t >= horizon_days:
                        break
                    targets = [el]
                    if mode == "MARGIN_EROSION":       # the erosion shows at the channel's receiver
                        targets = [net.lightpaths[el].dst_trx]
                    if mode == "FIBRE_CUT":            # a dig-up cuts the cable, and sometimes the duct
                        sp = net.spans[el]
                        o = net.oms[sp.oms]
                        rev = net.oms[f"oms.{o.dst}-{o.src}"].spans
                        targets = [sp.id, rev[len(rev) - 1 - sp.index].id]
                        if sp.duct.startswith("srlg.") and rng.random() < 0.5:
                            targets = sorted(set(targets) | set(net.srlgs[sp.duct].spans))
                    inc_id = f"inc{n_inc:05d}"
                    n_inc += 1
                    truth = {"fault_mode": mode, "targets": targets, "params": _sample_params(rng, mode),
                             "onset": t}
                    tk = _ticket(rng, inc_id, mode, targets, t)
                    # most transients pass unnoticed; only some lead to a ticket
                    p_ticket = {"SOP_TRANSIENT": 0.05, "MECHANICAL_DISTURBANCE": 0.15}.get(mode, 1.0)
                    tk["ticketed"] = rng.random() < p_ticket
                    incidents.append(_incident(inc_id, truth, tk))
    # maintenance windows, a few of which leave a connector fault behind
    for s in net.nodes:
        t = site_in[s]
        while True:
            t += rng.expovariate(4 / 365.0)
            if t >= horizon_days:
                break
            ev.append(LifecycleEvent(t, t, "maintenance_window", {"site": s, "duration_h": rng.choice([2, 4, 6])}))
            if rng.random() < 0.08:
                oms_here = [o.id for o in net.oms.values() if o.src == s and link_in[o.link] <= t]
                if oms_here:
                    o = rng.choice(oms_here)
                    mode = rng.choice(["CONNECTOR_DIRTY", "CONNECTOR_PULLED"])
                    inc_id = f"inc{n_inc:05d}"
                    n_inc += 1
                    t1 = t + rng.uniform(0.01, 0.2)
                    truth = {"fault_mode": mode, "targets": [o], "params": _sample_params(rng, mode),
                             "onset": t1, "after_maintenance": True}
                    tk = _ticket(rng, inc_id, mode, [o], t1)
                    tk["ticketed"] = True
                    incidents.append(_incident(inc_id, truth, tk))
    incidents.sort(key=lambda i: i.truth["onset"])
    for inc in incidents:
        if inc.ticket["ticketed"] and inc.ticket["closed"] < horizon_days:
            ev.append(LifecycleEvent(inc.ticket["closed"], inc.ticket["closed"], "incident_closed",
                                     {"ticket": copy.deepcopy(inc.ticket)}))
    # Follow-up findings: some engineer-concluded explanations are later confirmed, completed or refuted (a
    # returned unit's failure analysis, a later OTDR trace, a vendor report). Drawn from a generator of their own
    # so that adding them leaves the rest of the history unchanged.
    frng = random.Random(seed * 7919 + 17)
    for inc in sorted(incidents, key=lambda i: i.id):
        tk = inc.ticket
        if (not tk["ticketed"] or tk["closed"] >= horizon_days or tk["verification"] != "engineer_concluded"
                or tk["outcome"] == "no_fault_found" or CATALOGUE[inc.truth["fault_mode"]].nature.value == "transient"):
            continue
        draw, t = frng.random(), tk["closed"] + frng.uniform(14.0, 60.0)
        if draw >= FOLLOWUP_P or t >= horizon_days:
            continue
        q = inc.truth["ticket_quality"]
        upd = ({"verification": "repair_confirmed"} if q == "accurate" else
               {"verification": "repair_confirmed", "recorded_fault_mode": inc.truth["fault_mode"]} if q == "coarse" else
               {"verification": "refuted"})
        ev.append(LifecycleEvent(t, t, "ticket_followup", {"ticket_id": tk["id"], **upd}))
    ev.sort(key=lambda e: e.t)
    return History(net, horizon_days, ev, incidents, site_in, link_in, lp_in, svc_in, svc_out, tier_steps, etype)


FOLLOWUP_P = 0.3            # share of eligible engineer-concluded tickets that get a follow-up finding

OBSERVED_VERSION = "3"        # bump when the world simulator or observed_summary changes (2: seeds no longer from hash(); 3: the switch-fabric alarm renamed OTN_FABRIC_FAIL)


def history_digest(h: History) -> str:
    """A digest of the history's events and incidents, without what tickets observed."""
    import hashlib

    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if k != "observed"}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x
    text = repr([(e.t, e.t_recorded, e.kind, clean(e.payload)) for e in h.events]) + \
        repr([(i.id, i.truth, clean(i.ticket)) for i in h.incidents])
    return hashlib.sha1(text.encode()).hexdigest()


def attach_observations(h: History, cache: Optional["Path"] = None) -> None:
    """Record, in each ticket and its closure event, what was seen at the time (observed_summary).
    Re-simulating every incident takes about two minutes, so results can be cached in a file keyed by
    OBSERVED_VERSION and a digest of the whole history (every event and every incident), so that any change to
    the history invalidates the cache."""
    import json
    obs = None
    key = {"version": OBSERVED_VERSION, "history": history_digest(h)}
    if cache is not None and cache.exists():
        d = json.loads(cache.read_text())
        if d.get("key") == key:
            obs = d["observed"]
    if obs is None:
        obs = {i.id: observed_summary(h, i) for i in h.incidents if i.ticket["ticketed"]}
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"key": key, "observed": obs}))
    for i in h.incidents:
        if i.id in obs:
            i.ticket["observed"] = obs[i.id]
    for e in h.events:
        if e.kind == "incident_closed":
            e.payload["ticket"]["observed"] = obs[e.payload["ticket"]["id"]]


def summary(h: History) -> dict:
    from collections import Counter
    return {
        "events": dict(Counter(e.kind for e in h.events)),
        "equipment_types": len({e.payload["type"] for e in h.events if e.kind == "equipment_type_onboarded"}),
        "incidents": len(h.incidents),
        "ticketed": sum(1 for i in h.incidents if i.ticket["ticketed"]),
        "incidents_by_mode": dict(Counter(i.truth["fault_mode"] for i in h.incidents).most_common()),
        "ticket_quality": dict(Counter(i.truth["ticket_quality"] for i in h.incidents if i.ticket["ticketed"])),
        "verification": dict(Counter(i.ticket["verification"] for i in h.incidents if i.ticket["ticketed"])),
        "unregistered_deployments": sum(1 for e in h.events if e.t_recorded is None),
        "lagged_records": sum(1 for e in h.events if e.t_recorded is not None and e.t_recorded > e.t),
        "multi_span_cuts": sum(1 for i in h.incidents if i.truth["fault_mode"] == "FIBRE_CUT"
                               and len(i.truth["targets"]) > 2),
        "after_maintenance": sum(1 for i in h.incidents if i.truth.get("after_maintenance")),
    }


# ---------------------------------------------------------------------- what was seen at the time

def observed_summary(h: History, inc: Incident, window_s: float = 1200.0) -> dict:
    """Simulate the incident on the network as it stood, with the instruments then deployed, and
    return what operations would have recorded: the alarms raised (type and element), the client
    ports that lost service, and the protection switches. Incipient faults show only as trends."""
    from .faults import Event
    from .simulator import WorldSim
    tr = inc.truth
    mode = tr["fault_mode"]
    nature = CATALOGUE[mode].nature.value
    if nature == "incipient":
        return {"alarms": [], "pm_degraded": [], "client_sf": [], "prot_switches": 0, "note": "slow trend"}
    day = tr["onset"]
    net = h.network_at(day)
    onset = 300.0
    params = dict(tr["params"])
    dur = params.get("duration_s") if nature == "transient" else None
    w = WorldSim(net, seed=zlib.crc32(inc.id.encode()) % 10_000, deployment=h.deployment_at(day))  # not hash(): salted per process
    for target in tr["targets"]:
        if target not in net.spans and target not in net.amps and target not in net.oms \
                and target not in net.transponders and not target.startswith(("cp.", "otn.")):
            continue
        if target.startswith("cp.") and target.split(".")[1] not in net.services:
            continue
        w.inject(Event(mode, target, params, onset=onset, duration=dur))
    from .simulator import Lake
    segs = w.segments(0.0, window_s)          # alarms and protection events only; no sampled streams
    lake = Lake()
    w._alarms(segs, lake)
    w._protection_events(segs, lake)
    alarms = sorted({(o.observable, o.element) for o in lake.query(source="alarm")
                     if o.value != "not_alarmed" and o.t_start >= onset})
    before = next(sg for sg in segs if sg.t0 <= onset - 1 < sg.t1).snap.rx
    after = next(sg for sg in segs if sg.t0 <= onset + 1 < sg.t1).snap.rx
    degraded = sorted({after[k].trx for k in after if k in before
                       and before[k].margin_db - after[k].margin_db > 1.0})
    return {"alarms": [list(a) for a in alarms], "pm_degraded": degraded,
            "client_sf": sorted({o.element for o in lake.query(observable="CLIENT_SF")}),
            "prot_switches": len(lake.query(source="event", observable="PROT_SWITCH"))}
