"""The network twin, rebuilt from the knowledge graph (likelihood tool A2; "The Diagnosis Agent Test Bed" §9.4).

The twin is the operator's transmission model. It knows the network only as recorded: the topology, routes and
slots in the inventory and provisioning records, the recorded span losses, the amplifier gains set at
commissioning, datasheet noise figures and the line design rules (knowledge/practice/line_design.yaml). It knows
nothing the records do not hold: not the true fibre attenuation or connector losses, not the true noise figures,
not the instruments' calibration offsets, and not changes not yet recorded. Its physics engine is the transmission
model of version 1 of the world (the premise of decision D2: the twin's error comes from what it does not know,
not from missing physics).

Used counterfactually. For a hypothesis it applies the change the equipment documentation describes for that fault
mode (the "twin scenario" on the fault-mode edge: span cut, added span loss, amplifier gain change, a receiver
penalty and so on), fits the scenario's one parameter to the data, and scores how well the predicted change in
every instrument stream matches the observed change. Where the documentation gives no twin scenario (gain tilt,
pump or laser ageing, undocumented modes), the twin declines: it says nothing about that
hypothesis rather than guessing. Faults of the monitoring itself (an instrument misreporting or frozen) are
modelled for any instrument (knowledge/practice/twin.yaml).

What is compared, for every stream both the twin and the lake have:
- the change in level from before the onset to after it (a change cancels most of the twin's static error, the
  hidden gap and the calibration offsets);
- the extreme during the onset (lowest power, highest bit error rate, fastest polarisation change), against the
  level before, so that brief events are seen;
- whether the signal is lost;
(equipment health streams are not compared: the twin does not model health; the health term reads them);
- the 15-minute errored-second counts of line ports (from the bit error rate).
The twin predicts transmission: optical powers, OSNR, bit error rate, DGD, polarisation, loss of signal and what
measurements of these show. It does not predict equipment behaviour (alarms raised, protection switching, client-side
counts, the answers of equipment checks such as a cross-connect audit or a loopback); those are judged from the
documentation (graph consistency, and the documented meaning of a check). With equipment=True (an experimental arm
only) it also predicts them, by running the world's own equipment logic on its network.

Each continuous comparison is a normal term with the instrument's noise and an allowance for the twin's own error
(its stated accuracy, knowledge/practice/twin.yaml: a floor plus a fraction of the predicted change), capped so that one stray reading cannot rule a
hypothesis out on its own; discrete differences cost a fixed amount each. The result is a log-likelihood per
hypothesis for the ledger, with a background (an unconsidered cause) set at what a correct explanation would
typically score, less an allowance. These constants are provisional and are set on development cases.

The twin also predicts what a check would show under each hypothesis (predict_check), which the advisor and the
instrument interpreters use.
"""
from __future__ import annotations

import copy
import math
import random
import time
import zlib
from dataclasses import dataclass, field
from typing import Optional

from ..lake import Lake
from ..schemas import Hypothesis, HypothesisKind, LikelihoodPayload, LikelihoodValue
from ..world.faults import Event
from ..world.network import (Amplifier, ClientService, Lightpath, Network, ODUHop, ODUPath, OMS, OTNSwitch, SRLG,
                             Span, Transponder)
from ..world.simulator import NOISE, WorldSim

# the twin's physics engine applies each documented scenario kind as one of its primitive changes
SCENARIO_PRIMITIVE = {
    "span_cut": "FIBRE_CUT", "span_added_loss": "SPAN_LOSS_INCREASE", "span_dgd": "DGD_RISE",
    "span_sop": "SOP_TRANSIENT", "oms_input_open": "CONNECTOR_PULLED", "oms_input_loss": "CONNECTOR_DIRTY",
    "amp_failed": "AMP_PUMP_FAIL", "amp_gain_change": "AMP_GAIN_DROP", "amp_nf_change": "AMP_NF_RISE",
    "tx_off": "TX_LASER_FAIL", "card_off": "CARD_FAIL", "rx_penalty": "RX_DEGRADATION",
    "wss_channel_loss": "WSS_ATTEN_DRIFT", "wss_filter_offset": "WSS_FILTER_SHIFT", "wss_port_blocked": "WSS_PORT_FAIL",
    "switch_failed": "OTN_SWITCH_FAIL", "xc_wrong": "XC_MISCONNECT", "client_port_failed": "CLIENT_PORT_FAIL",
    "instrument_bias": "INSTRUMENT_MISREPORT", "instrument_frozen": "TELEMETRY_STALE",
    "oms_power_excursion": "CHANNEL_LOADING_TRANSIENT",
    "amp_gain_tilt": "AMP_GAIN_TILT",                     # C4: the operator's twin practice (amendment 8)
    "wss_filter_narrowing": "WSS_FILTER_NARROWING", "tx_laser_offset": "LASER_FREQ_DRIFT",   # spectral mode only
}
PRIMITIVE_PARAM = {"SPAN_LOSS_INCREASE": "added_loss_db", "DGD_RISE": "dgd_increase_ps",
                   "SOP_TRANSIENT": "peak_rate_rad_s", "CONNECTOR_DIRTY": "added_loss_db",
                   "AMP_GAIN_DROP": "gain_drop_db", "AMP_NF_RISE": "nf_increase_db", "RX_DEGRADATION": "penalty_db",
                   "WSS_ATTEN_DRIFT": "attenuation_db", "WSS_FILTER_SHIFT": "offset_ghz",
                   "INSTRUMENT_MISREPORT": "bias", "MECHANICAL_DISTURBANCE": "loss_excursion_db",
                   "CHANNEL_LOADING_TRANSIENT": "excursion_db",
                   "AMP_GAIN_TILT": "tilt_db",
                   "WSS_FILTER_NARROWING": "narrowing_ghz", "LASER_FREQ_DRIFT": "offset_ghz"}

# streams: lake observable -> (twin stream family, kind of comparison)
POWER_OBS = ("amp_input_power", "amp_output_power", "rx_power", "ocm_channel_power")
QUALITY_OBS = ("est_osnr", "ocm_osnr", "pre_fec_ber", "dgd")
HEALTH_OBS = ("pump_current_pct", "laser_bias_pct")
# the twin's stated accuracy on predicted changes (knowledge/practice/twin.yaml, stated_accuracy.changes): a floor
# plus a fraction of the predicted change; these are the defaults if the practice document does not state them
TWIN_ERR = {"power": (0.25, 0.15), "est_osnr": (0.4, 0.2), "ocm_osnr": (0.4, 0.2), "pre_fec_ber": (0.3, 0.2),
            "dgd": (1.0, 0.2), "sop_rate": (0.35, 0.0)}   # (floor, fraction of change)
# fields of a check's answer that the twin computes, and the scale of its calculation error on each (dB fields take
# the error itself; DGD a fraction of the value, per dB of error)
CALC_FIELDS = {"est_osnr_db": "db", "margin_db": "db", "rx_min_dbm": "db", "tx_power_dbm": "db",
               "input_dbm_per_ch": "db", "loss_db": "db", "dgd_ps": "rel", "pre_fec_ber": "ber"}
BER_DECADES_PER_DB = 0.5            # the twin's error on log10 BER per dB of its error on margin
CHANGE_ERR_PER_DB = 0.05 / 0.18     # relative error of a predicted change per dB of calculation error (5 % at 0.18 dB)
TRANSMISSION_PM = ("errored_seconds_pm15",)          # line errored seconds follow the bit error rate
TRANSMISSION_CHECKS = ("coherent_diag", "pm_detail", "tx_power", "power_meter", "span_loss", "otdr_embedded",
                       "otdr_portable")              # checks that measure transmission
Z_CAP = 4.0                 # a single stream costs at most Z_CAP**2 / 2 = 8 nats
LOST_PEN = 6.0              # signal lost in one and present in the other
DISCRETE_PEN = 2.0          # each alarm or protection switch predicted and absent, or present and not predicted
BACKGROUND_ALLOWANCE = 12.0 # an unconsidered cause scores this much below a typical correct explanation (set on dev cases 9100-9114)
GRID, GOLDEN = 8, 6
PEEL_SHIFTS_S = (0.0, 60.0, 120.0, 180.0, 300.0)   # a later cause's onset after the leader's, tried in peeling
NO_CHANGE_NATS = 0.5        # a hypothesis must improve on no fault by this much to be credited by the twin
NO_SCENARIO = "the documentation gives no twin scenario for this fault mode"
UNTESTABLE_AS_NO_CHANGE = True   # C4: a cause with no twin scenario is fitted as predicting no transmission change
                                 # (it no longer scores as the background, which credited it with every changed
                                 # reading); its residual, check predictions and planning then follow from that


# ---------------------------------------------------------------------- the network as recorded

def _spectral_on() -> bool:
    from ..world import spectral
    return spectral.on()


def twin_network(kg, design: dict) -> Network:
    """A Network built from the graph's records and the line design rules only."""
    g = kg.g
    nodes = lambda c: [n for n, d in g.nodes(data=True) if d.get("cls") == c]  # noqa: E731
    sites = {n[5:]: (g.nodes[n]["x_km"], g.nodes[n]["y_km"]) for n in nodes("Site")}
    hubs = [n[5:] for n in nodes("Site") if g.nodes[n].get("hub")]
    links, link_km = {}, {}
    for n in nodes("Link"):
        d = g.nodes[n]
        links[n[5:]] = (d["a"], d["b"])
        link_km[n[5:]] = d.get("length_km")
    nf_typ = {}
    oms, spans, amps = {}, {}, {}
    duct_members: dict = {}
    for n in nodes("OMS"):
        d = g.nodes[n]
        o = OMS(n, d["src"], d["dst"], d.get("link", ""))
        prev_span = None
        for el, ed in sorted(kg.out(n, "traverses"), key=lambda x: x[1].get("order", 0)):
            a = g.nodes[el]
            if a.get("cls") == "Amplifier":
                ty = kg.type_of(el)
                if ty not in nf_typ:
                    ds = (g.nodes[ty].get("datasheet") or {}) if ty else {}
                    nf_typ[ty] = float((ds.get("noise_figure_db") or {}).get("typical", 5.5))
                gain = a.get("gain_set_db")
                if (gain is None or gain == 0.0) and a.get("index", 0) > 0 and prev_span is not None:
                    gain = prev_span.nominal_loss_db       # commissioning rule: gain = loss of the preceding span
                amp = Amplifier(el, n, a.get("role", "inline"), a.get("index", 0), nf_db=nf_typ[ty],
                                gain_db=float(gain or 0.0))
                o.elements.append(amp)
                amps[el] = amp
            elif a.get("cls") == "FibreSpan":
                L = float(a["length_km"])
                cable = next((c for c, _ in kg.out(el, "inCable")), "")
                duct = next((c for c, _ in kg.out(el, "inDuct")), "")
                sp = Span(el, n, a.get("index", 0), L, loss_db_per_km=float(a["nominal_loss_db"]) / L,
                          connector_loss_db=0.0, aerial=bool(a.get("aerial")), cable=cable, duct=duct)
                o.elements.append(sp)
                spans[el] = sp
                prev_span = sp
                if duct:
                    duct_members.setdefault(duct, []).append(el)
        oms[n] = o
    trx = {}
    for n in nodes("LineInterface"):
        d = g.nodes[n]
        trx[n] = Transponder(n, d.get("site", ""), d.get("modulation", "DP-QPSK"), float(d.get("baud_gbd", 64.0)),
                             tx_power_dbm=float(design.get("transmit_power_dbm", 0.0)),
                             trx_snr_db=float(design.get("transceiver_snr_db", 22.0)),
                             kind=d.get("kind", "transponder"), rate_g=int(d.get("rate_g", 100)), host=d.get("host"))
    lps = {}
    for n in nodes("OCh"):
        d = g.nodes[n]
        ends = {e.get("end"): t for t, e in kg.out(n, "endsAt")}
        fwd = [o for o, e in sorted(kg.out(n, "carriedBy"), key=lambda x: x[1].get("order", 0))
               if e.get("direction") == "fwd"]
        if not fwd or "a" not in ends or "z" not in ends or any(o not in oms for o in fwd):
            continue
        lps[n] = Lightpath(n, ends["a"], ends["z"], list(d.get("nodes") or []), fwd, int(d.get("slot", 0)),
                           rate_g=int(d.get("rate_g", 100)), kind=d.get("kind", "direct"))
    odu = {}
    for n in nodes("ODUPath"):
        hops = [ODUHop(lp, e.get("a"), e.get("z")) for lp, e in sorted(kg.out(n, "oduHop"),
                                                                        key=lambda x: x[1].get("order", 0))]
        if hops and all(h.lightpath in lps for h in hops):
            odu[n] = ODUPath(n, g.nodes[n].get("odu", "ODU4"), hops)
    svcs = {}
    for n in nodes("ClientService"):
        d = g.nodes[n]
        paths = kg.service_paths(n)
        w, p = paths["working"], paths["protect"]
        if w not in odu:
            continue
        svcs[n] = ClientService(n, odu[w].hops[0].lightpath, d.get("rate", "100GE"), d.get("src", ""), d.get("dst", ""),
                                w, p if p in odu else None, d.get("protection", "none") if p in odu else "none")
    switches = {n: OTNSwitch(n, g.nodes[n].get("site", n[4:])) for n in nodes("OTNSwitch")}
    srlgs = {}
    for duct, sps in duct_members.items():
        if len({spans[s].oms.split(".", 1)[1].split("-")[0] for s in sps} |
               {spans[s].oms.split(".", 1)[1].split("-")[1] for s in sps}) > 2:
            srlgs[f"srlg.{duct}"] = SRLG(f"srlg.{duct}", "duct", sorted(sps), True)
    return Network("twin", sorted(sites), links, oms, spans, amps, trx, lps, svcs,
                   float(design.get("launch_dbm_per_channel", 1.0)), float(design.get("drop_target_dbm", -6.0)),
                   int(design.get("planning_channel_load", 64)), float(design.get("slot_ghz", 75.0)),
                   sites=sites, hubs=hubs, otn_switches=switches, odu_paths=odu, srlgs=srlgs, link_km=link_km,
                   spectral=_spectral_on())


def twin_deployment(kg) -> dict:
    """Instrumentation tier by site, as recorded."""
    return {n[5:]: d.get("tier", "baseline") for n, d in kg.g.nodes(data=True) if d.get("cls") == "Site"}


# ---------------------------------------------------------------------- features

@dataclass
class Windows:
    before: tuple
    during: tuple
    after: tuple

    @classmethod
    def around(cls, onset: float, now: float) -> "Windows":
        b = (max(0.0, onset - 480.0), max(1.0, onset - 20.0))
        d = (max(0.0, onset - 20.0), min(now, onset + 120.0))
        a = (min(max(onset + 150.0, now - 300.0), now - 60.0), now)
        return cls(b, d, a)


@dataclass
class Features:
    cont: dict = field(default_factory=dict)        # (observable, element) -> {"b","a","d" (extreme), "lost_a", ...}
    pm: dict = field(default_factory=dict)          # (observable, element, bin start) -> count
    alarms: frozenset = frozenset()
    switches: frozenset = frozenset()


def _wmean(pairs, lo, hi):
    """Time-weighted mean of (t0, t1, value) pieces over [lo, hi]; (-inf if mostly lost, None if no overlap)."""
    tot = lost = acc = 0.0
    for a, b, v in pairs:
        w = min(b, hi) - max(a, lo)
        if w <= 0:
            continue
        tot += w
        if v == -math.inf:
            lost += w
        else:
            acc += w * v
    if tot <= 0:
        return None
    if lost > 0.5 * tot:
        return -math.inf
    return acc / (tot - lost) if tot > lost else -math.inf


def _extreme(pairs, lo, hi, worst):
    vals = [v for a, b, v in pairs if min(b, hi) - max(a, lo) > 0]
    return worst(vals) if vals else None


def _log_ber(x):
    return math.log10(max(x, 1e-30))


class GraphTwin:
    name, version = "twin.graph", "0.3"   # 0.2 (amendment 4): noise-free predictions, own calculation error; 0.3 (amendment 8, C4): a cause with no scenario predicts no change
    ladder: frozenset = frozenset()        # the capability ladder's rungs the twin predicts (none unless given)
    _ladder_obs: tuple = ()

    def __init__(self, substrate, sources, onset: float, calc_err_db: float = 0.0, seed: int = 0,
                 equipment: bool = False, ladder=()):
        """calc_err_db: the standard deviation of the twin's own calculation error (dB on margin, OSNR and power;
        the experiment's setting of how good the operator's twin is). It is drawn once per quantity and element for
        the case, as a fixed error of the model, not fresh noise per prediction."""
        t0 = time.time()
        self.sub, self.kg = substrate, substrate.kg
        design = (sources.practice.get("line_design") or {}).get("design", {})
        self.practice = sources.practice.get("twin") or {}
        stated = (self.practice.get("stated_accuracy") or {}).get("changes") or {}
        self.err = {**TWIN_ERR, **{k: tuple(v) for k, v in stated.items()}}
        self.calc_err_db, self.seed = float(calc_err_db), int(seed)
        self.equipment = bool(equipment)
        # the capability ladder's sampled streams the twin predicts: per-span DGD (rung L2; world/ladder.segment_streams
        # on the twin's own network), at its documented accuracy. The per-port WSS readings (L3) are read by their
        # documented meaning instead (tools/ladder.PortMonitor), so that one reading is not counted twice.
        self.ladder = frozenset(ladder or ()) & {"L2", "S", "R", "NOPM"}
        if self.ladder:
            from .ladder import ladder_observables, ladder_twin_err
            self.err.update(ladder_twin_err())
            self._ladder_obs = ladder_observables(self.ladder)
        else:
            self._ladder_obs = ()
        self.at_s = float(substrate.now)            # the case clock: advanced by the loop as checks complete
        self.repair_checks: list = []               # (check, params, t) of completed checks that repair what they find
        self.net = twin_network(self.kg, design)
        if "S" in self.ladder:
            self.net.span_spectra_wanted = True       # span-end spectra only when rung S is read
        if "R" in self.ladder:
            self.net.rx_spectra_wanted = True
        self.dep = twin_deployment(self.kg)
        self.onset, self.now = float(onset), float(substrate.now)
        self.win = Windows.around(self.onset, self.now)
        self.cost = {"setup_s": 0.0, "simulations": 0, "sim_s": 0.0}
        self._sp_slots = None
        self.obs = self._scope(self._observed())
        self._restrict_spectral(self.obs)
        self.base = self.features([])
        # equipment health (pump current, laser bias) is not modelled by the twin; the health term reads it
        self.keys = sorted(k for k in self.obs.cont if k in self.base.cont and k[0] not in HEALTH_OBS)
        self.fits: dict = {}
        self.sequential = False          # peeling: a later cause of a compound is fitted given the earlier ones
        self._base_events: list = []
        self._shift = 0.0                # peeling: a later cause's onset, after the leader's (s)
        self.cost["setup_s"] = round(time.time() - t0, 2)

    # ------------------------------------------------------------------ what the lake shows
    def _observed(self) -> Features:
        sub, W = self.sub, self.win
        f = Features()
        for ob in POWER_OBS + QUALITY_OBS + HEALTH_OBS + ("sop_rate",) + tuple(getattr(self, "_ladder_obs", ())):
            groups: dict = {}
            for o in sub.records(ob):
                if o.period and isinstance(o.value, list):
                    pieces = [(o.t_start + k * o.period, o.t_start + (k + 1) * o.period, v) for k, v in enumerate(o.value)]
                else:
                    pieces = [(o.t_start, o.t_start + 1.0, o.value)]
                for a, b, v in pieces:
                    if not isinstance(v, (int, float)):
                        continue
                    v = float(v)
                    if v <= -900 or v == -math.inf:
                        v = -math.inf
                    elif ob == "pre_fec_ber":
                        v = _log_ber(v)
                    groups.setdefault(o.element, []).append((a, b, v))
            for el, pieces in groups.items():
                f.cont[(ob, el)] = self._summarise(ob, pieces, W, observed=True)
        for ob in ("errored_seconds_pm15", "client_errored_seconds_pm15", "client_unavailable_seconds_pm15"):
            for o in sub.records(ob):
                if isinstance(o.value, (int, float)):
                    f.pm[(ob, o.element, o.t_start)] = float(o.value)
        f.alarms = frozenset((o.observable, o.element) for o in sub.alarms()
                             if o.t_start >= self.onset - 60.0)
        f.switches = frozenset(o.element for o in sub.events("PROT_SWITCH") if o.t_start >= self.onset - 60.0)
        return f

    def _scope(self, f: Features) -> Features:
        """What the twin compares: transmission only, unless it models equipment behaviour."""
        if not self.equipment:
            f.alarms, f.switches = frozenset(), frozenset()
            f.pm = {k: v for k, v in f.pm.items() if k[0] in TRANSMISSION_PM}
        return f

    @staticmethod
    def _summarise(ob, pieces, W, observed):
        out = {"b": _wmean(pieces, *W.before), "a": _wmean(pieces, *W.after)}
        if ob == "sop_rate":
            out["d"] = _extreme(pieces, *W.during, max)
            out["b"] = _extreme(pieces, *W.before, max)
        elif ob == "pre_fec_ber":
            out["d"] = _extreme(pieces, *W.during, max)
        elif ob in POWER_OBS:
            out["d"] = _extreme(pieces, *W.during, min)
        if observed:
            for wname, (lo, hi) in (("b", W.before), ("a", W.after)):
                vals = [v for a, b, v in pieces if lo <= a < hi and v > -math.inf]
                n = len(vals)
                if n >= 2:
                    m = sum(vals) / n
                    sd = math.sqrt(sum((x - m) ** 2 for x in vals) / (n - 1))
                else:
                    sd = NOISE["power_db"]
                out["se_" + wname] = max(0.02, sd / math.sqrt(max(1, n)))
        return out

    # ------------------------------------------------------------------ what the twin predicts
    def world(self, events: list[Event]) -> WorldSim:
        w = WorldSim(self.net, seed=0, deployment=self.dep)
        for e in events:
            w.inject(e)
        return w

    # spectral monitoring (rungs S and R): thousands of streams, nearly all unchanged. The twin compares those of
    # the channels whose shape changed beyond noise (more than SP_CHANGE_SIGMA standard errors), fixed for the case;
    # a hypothesis that would change another channel is still judged on that channel's other streams.
    SP_OBS = ("sp_power", "sp_offset", "sp_width", "rx_offset", "rx_width", "rx_asym", "rx_filter_penalty")
    SP_CHANGE_SIGMA = 5.0        # on the documented noise of a difference of window means (about 0.6 of one reading's)

    def _sp_slot(self, el: str):
        if "|" in el:
            return el.rpartition("|")[2]
        if not hasattr(self, "_trx_slot"):
            self._trx_slot = {}
            for lp in self.kg.nodes_of("OCh"):
                for t, _ in self.kg.out(lp, "endsAt"):
                    self._trx_slot[t] = str(self.kg.g.nodes[lp].get("slot"))
        return self._trx_slot.get(el)

    def _restrict_spectral(self, f: Features) -> None:
        keys = [k for k in f.cont if k[0] in self.SP_OBS]
        if not keys:
            return
        slots = set()
        for k in keys:
            o = f.cont[k]
            if o["a"] is None or o["b"] is None or not math.isfinite(o["a"]) or not math.isfinite(o["b"]):
                continue
            from ..world.ladder import NOISE_L
            se = 0.6 * NOISE_L.get(k[0], 0.5)      # documented noise, not the windows' sample spread (few samples)
            if abs(o["a"] - o["b"]) > self.SP_CHANGE_SIGMA * se:
                slots.add(self._sp_slot(k[1]))
        self._sp_slots = slots
        for k in keys:
            if self._sp_slot(k[1]) not in slots:
                del f.cont[k]

    def features(self, events: list[Event]) -> Features:
        t0 = time.time()
        w = self.world(events)
        segs = w.segments(0.0, self.now)
        f = Features()
        series: dict = {}
        sp_slots = self._sp_slots

        def put(ob, el, a, b, v):
            if sp_slots is not None and ob in self.SP_OBS and self._sp_slot(el) not in sp_slots:
                return
            series.setdefault((ob, el), []).append((a, b, v))
        for s in segs:
            for aid, r in s.snap.amps.items():
                if w.has("amp_power", aid):
                    put("amp_input_power", aid, s.t0, s.t1, r.input_dbm_per_ch)
                    put("amp_output_power", aid, s.t0, s.t1, r.output_dbm_per_ch)
                if w.has("health", aid):
                    put("pump_current_pct", aid, s.t0, s.t1, r.pump_current_pct)
            for r in s.snap.rx.values():
                if w.has("rx_power", r.trx):
                    put("rx_power", r.trx, s.t0, s.t1, r.rx_power_dbm)
                if w.has("coherent", r.trx):
                    lost = not math.isfinite(r.rx_power_dbm)
                    put("est_osnr", r.trx, s.t0, s.t1, -math.inf if lost else r.osnr_db)
                    put("pre_fec_ber", r.trx, s.t0, s.t1, _log_ber(r.pre_fec_ber))
                    put("dgd", r.trx, s.t0, s.t1, r.dgd_ps)
                    put("sop_rate", r.trx, s.t0, s.t1, r.sop_rate_rad_s)
            for tid in self.net.transponders:
                if w.has("health", tid):
                    put("laser_bias_pct", tid, s.t0, s.t1, s.snap.laser_bias_pct.get(tid, 100.0))
            if self.ladder:
                from ..world.ladder import _fx, segment_streams
                for (ob, el), v in segment_streams(self.net, s.snap, _fx(w, s), self.ladder).items():
                    put(ob, el, s.t0, s.t1, v)
            for (oms, slot), (p, o) in s.snap.ocm.items():
                el = f"{self.net.oms[oms].dst}:{oms}"
                if w.has("ocm", el):
                    put("ocm_channel_power", f"{el}|{slot}", s.t0, s.t1, p)
                    put("ocm_osnr", f"{el}|{slot}", s.t0, s.t1, o if p > -math.inf else -math.inf)
        # faults of the monitoring: a biased instrument from its onset; a frozen one repeats its last reading
        for el, bias in w.misreport.items():
            since = w.misreport_since.get(el, -math.inf)
            for (ob, e), pieces in series.items():
                if (e == el or e.split("|")[0] == el) and ob in POWER_OBS + ("est_osnr", "ocm_osnr"):
                    series[(ob, e)] = [(a, b, v + bias if a >= since and v > -math.inf else v) for a, b, v in pieces]
        for el, since in w.stale.items():
            for (ob, e), pieces in series.items():
                if e == el or e.split("|")[0] == el:
                    last = next((v for a, b, v in reversed(pieces) if a < since), None)
                    if last is not None:
                        series[(ob, e)] = [(a, b, last if a >= since else v) for a, b, v in pieces]
        for k, pieces in series.items():
            f.cont[k] = self._summarise(k[0], pieces, self.win, observed=False)
        lake = Lake()
        w._alarms(segs, lake)
        w._pm(segs, lake, 0.0, self.now)
        if self.net.odu_paths:
            w._client_pm(segs, lake, 0.0, self.now)
            w._protection_events(segs, lake)
        for o in lake.query(source="pm_counter"):
            if o.observable != "rx_power_pm15":
                f.pm[(o.observable, o.element, o.t_start)] = float(o.value)
        f.alarms = frozenset((o.observable, o.element) for o in lake.query(source="alarm")
                             if o.value != "not_alarmed" and o.t_start >= self.onset - 60.0)
        f.switches = frozenset(o.element for o in lake.query(source="event") if o.t_start >= self.onset - 60.0)
        f = self._scope(f)
        self.cost["simulations"] = self.cost.get("simulations", 0) + 1
        self.cost["sim_s"] = round(self.cost.get("sim_s", 0.0) + time.time() - t0, 2)
        return f

    # ------------------------------------------------------------------ comparison
    def costs(self, pred: Features) -> dict:
        """Misfit in nats of a prediction, item by item: each compared stream, each PM count, each alarm or
        protection switch present in one and not the other."""
        out: dict = {}
        for k in self.keys:
            o, p = self.obs.cont[k], pred.cont[k]
            ob = k[0]
            fam = "power" if ob in POWER_OBS else ob
            floor, frac = self.err[fam]
            g = self._change_gain(k)
            if o["a"] is None or p["a"] is None or o["b"] is None or p["b"] is None:
                continue
            c = 0.0
            lost_o, lost_p = o["a"] == -math.inf, p["a"] == -math.inf
            if lost_o != lost_p:
                c += LOST_PEN
            elif not lost_o and o["b"] > -math.inf and p["b"] > -math.inf:
                if ob == "sop_rate":
                    if o.get("d") is not None and p.get("d") is not None:
                        x = math.log10(max(o["d"], 1.0)) - math.log10(max(p["d"], 1.0) + 100.0)
                        z = x / floor                  # stated accuracy on the peak rate, in decades
                        c += 0.5 * min(z * z, Z_CAP ** 2) if abs(x) > 0.5 else 0.0
                else:
                    dp, do = g * (p["a"] - p["b"]), o["a"] - o["b"]
                    sig = math.hypot(math.hypot(o.get("se_a", 0.05), o.get("se_b", 0.05)), floor + frac * abs(dp))
                    z = (do - dp) / sig
                    c += 0.5 * min(z * z, Z_CAP ** 2)
                    if o.get("d") is not None and p.get("d") is not None and ob != "dgd":
                        if (o["d"] == -math.inf) != (p["d"] == -math.inf):
                            c += LOST_PEN / 2
                        elif o["d"] > -math.inf:
                            xo, xp = o["d"] - o["b"], g * (p["d"] - p["b"])
                            sig_d = math.hypot(3 * o.get("se_b", 0.05) + (NOISE["power_db"] if fam == "power" else 0.5),
                                               floor + frac * abs(xp))
                            z = (xo - xp) / sig_d
                            c += 0.5 * min(z * z, Z_CAP ** 2)
            out[k] = c
        for k in self.obs.pm.keys() | pred.pm.keys():
            vo, vp = self.obs.pm.get(k), pred.pm.get(k)
            if vo is None or vp is None:
                continue
            z = (vo - vp) / (2.0 + 0.2 * max(vo, vp))
            out[("pm",) + k] = 0.5 * min(z * z, Z_CAP ** 2)
        for a in self.obs.alarms ^ pred.alarms:
            out[("alarm",) + a] = DISCRETE_PEN
        for s in self.obs.switches ^ pred.switches:
            out[("switch", s)] = DISCRETE_PEN
        return out

    # ------------------------------------------------------------------ the twin's own calculation error
    def _draw(self, *key) -> float:
        d = self.__dict__.setdefault("_draws", {})
        if key not in d:
            r = random.Random(zlib.crc32(("twincalc/%d/" % self.seed + "/".join(map(str, key))).encode()))
            d[key] = r.gauss(0.0, 1.0)
        return d[key]

    def _change_gain(self, k) -> float:
        """The twin's predicted change on a stream is off by a fixed fraction, drawn per stream for the case."""
        if self.calc_err_db <= 0:
            return 1.0
        return 1.0 + CHANGE_ERR_PER_DB * self.calc_err_db * self._draw("change", *k)

    def _calc_error(self, value, target: str):
        """Add the twin's calculation error to the fields of a predicted check answer (fixed per field and target)."""
        if self.calc_err_db <= 0 or not isinstance(value, dict):
            return value
        out = {}
        for f, v in value.items():
            kind = CALC_FIELDS.get(f)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and kind is not None and math.isfinite(v):
                e = self.calc_err_db * self._draw("check", f, target)
                if kind == "db":
                    v = round(v + e, 2)
                elif kind == "ber":
                    v = v * 10 ** (BER_DECADES_PER_DB * e)
                else:
                    v = round(v * (1.0 + CHANGE_ERR_PER_DB * e), 2)
            elif isinstance(v, list):
                v = [self._calc_error(x, target) if isinstance(x, dict) else x for x in v]
            out[f] = v
        return out

    def loglik(self, pred: Features, detail: bool = False):
        c = self.costs(pred)
        ll = -sum(c.values())
        if not detail:
            return ll
        return ll, sorted(((round(v, 2),) + tuple(k) for k, v in c.items() if v > 1.0), reverse=True)[:12]

    def changed(self) -> set:
        """Items in which the data show a change at the onset: a level change beyond three standard errors and
        0.2, a loss or return of signal, a dip of more than 1 dB, a bit error rate burst of more than half a
        decade, a polarisation burst five times the rate before and above 2000 rad/s, non-zero PM counts, alarms
        and protection switches."""
        out = set()
        for k in self.keys:
            o = self.obs.cont[k]
            b, a, d = o["b"], o["a"], o.get("d")
            if a is None or b is None:
                continue
            if (a == -math.inf) != (b == -math.inf):
                out.add(k)
                continue
            if a == -math.inf or b == -math.inf:
                continue
            if k[0] == "sop_rate":
                if d is not None and d > 2000.0 and d > 5 * max(b, 1.0):
                    out.add(k)
            elif abs(a - b) > 3 * math.hypot(o.get("se_a", 0.05), o.get("se_b", 0.05)) + 0.2:
                out.add(k)
            elif d is not None and k[0] in POWER_OBS and (d == -math.inf or b - d > 1.0):
                out.add(k)
            elif d is not None and k[0] == "pre_fec_ber" and d - b > 0.5:
                out.add(k)
        out |= {("pm",) + k for k, v in self.obs.pm.items() if v > 0}
        out |= {("alarm",) + a for a in self.obs.alarms}
        out |= {("switch", s) for s in self.obs.switches}
        return out

    def background(self) -> float:
        """The score of a cause nobody proposed: it would leave unchanged what the data show unchanged (scored as
        the no-fault twin scores those items), fit each changed item to about one standard deviation (half a nat),
        and is charged an allowance for not being among the proposals."""
        null = self.costs(self.base)
        ch = self.changed()
        return -sum(v for k, v in null.items() if k not in ch) - 0.5 * len(ch) - BACKGROUND_ALLOWANCE

    # ------------------------------------------------------------------ hypotheses
    def scenario(self, mode: str, loc: str) -> Optional[dict]:
        inst = (self.practice.get("instrument_scenarios") or {}).get(mode) \
            or (self.practice.get("physics_scenarios") or {}).get(mode)
        if inst:
            return dict(inst)
        owner = self._owner(loc)
        for m, d in self.kg.fault_modes_for(owner):
            if m == mode and d.get("twin_scenario"):
                return dict(d["twin_scenario"])
        return None

    def _owner(self, loc: str) -> str:
        if "|" in loc:
            a, _ = loc.split("|", 1)
            if self.kg.cls(a) == "OMS":
                return f"roadm.{self.kg.g.nodes[a]['dst']}"
            return a
        return loc

    def targets(self, mode: str, loc: str, kind: str) -> list[list[str]]:
        """Alternative sets of twin elements the scenario applies to (the hypothesis' location may be coarser)."""
        net, kg = self.net, self.kg
        base = loc.split("|")[0]
        if kind in ("instrument_bias", "instrument_frozen"):
            el = kg.g.nodes[loc].get("lake_element") if kg.has(loc) and kg.cls(loc) == "Instrument" else loc
            return [[el]]
        if kind == "span_cut" and base in net.spans:
            sp = net.spans[base]
            o = net.oms[sp.oms]
            rev = net.oms.get(f"oms.{o.dst}-{o.src}")
            return [[base, rev.spans[len(rev.spans) - 1 - sp.index].id]] if rev else [[base]]
        if kind.startswith("span_"):
            return [[base]] if base in net.spans else []
        if kind == "oms_power_excursion":
            return [[base]] if base in net.oms else []
        if kind.startswith("oms_input"):
            return [[base]] if base in net.oms else []
        if kind.startswith("amp_"):
            return [[base]] if base in net.amps else []
        if kind in ("tx_off", "card_off", "rx_penalty", "tx_laser_offset"):
            return [[base]] if base in net.transponders else []
        if kind == "wss_port_blocked":           # "oms|port": the world's target is the OMS arriving on the port
            return [[base]] if loc.partition("|")[2] == "port" and base in net.oms else []
        if kind.startswith("wss_"):             # per-channel kinds: "oms|slot" only
            if "|" in loc and base in net.oms and loc.partition("|")[2].isdigit():
                return [[loc]]
            return []
        if kind == "switch_failed":
            return [[base]] if base in net.otn_switches else []
        if kind == "xc_wrong":
            if "|" in loc:
                return [[loc]]
            return [[f"{base}|{p}"] for p, _ in kg.inn(base, "crossConnectedAt")][:40] if base in net.otn_switches else []
        if kind == "client_port_failed":
            return [[base]] if base.startswith("cp.") else []
        return []

    def events(self, prim: str, targets: list[str], value: Optional[float], duration: Optional[float]) -> list[Event]:
        params = {}
        if value is not None and prim in PRIMITIVE_PARAM:
            params[PRIMITIVE_PARAM[prim]] = float(value)
        return [Event(prim, t, params, onset=self.onset + self._shift, duration=duration) for t in targets]

    def fit_cause_given(self, mode: str, loc: str, base_events: list, base_key) -> dict:
        """Best fit of one cause with other causes' fitted changes already applied (the ablative step of peeling:
        the later cause is fitted to what the earlier ones leave). Coexisting faults need not start together, so the
        later cause's onset is fitted too, from the leader's onset to PEEL_SHIFTS_S after it ("onset_shift_s").
        Cached per (cause, base)."""
        key = (mode, loc, "given", base_key)
        if key in self.fits:
            return self.fits[key]
        saved, self._base_events = self._base_events, list(base_events)
        cached = self.fits.pop((mode, loc), None)
        out = None
        try:
            for s in PEEL_SHIFTS_S:
                self._shift = s
                r = self.fit_cause(mode, loc)
                self.fits.pop((mode, loc), None)
                if "declined" in r:
                    out = r
                    break
                if out is None or r["ll"] > out["ll"]:
                    out = {**r, "onset_shift_s": s}
        finally:
            self._shift = 0.0
            self.fits.pop((mode, loc), None)
            if cached is not None:
                self.fits[(mode, loc)] = cached
            self._base_events = saved
        self.fits[key] = out
        return out

    def residual(self, h: Hypothesis, min_cost: float = 4.5) -> list:
        """The changed readings a fitted hypothesis leaves unexplained, worst first: [(cost in nats, key)]."""
        r = self.fit(h)
        if "declined" in r:
            return []
        c = self.costs(self.features(r["events"]))
        return sorted(((c[k], k) for k in self.changed() if c.get(k, 0.0) > min_cost), key=lambda x: -x[0])

    def fit_cause(self, mode: str, loc: str) -> dict:
        """Best fit of one cause: {"ll", "params", "events", "scenario"} or {"declined": reason}."""
        key = (mode, loc)
        if key in self.fits:
            return self.fits[key]
        sc = self.scenario(mode, loc)
        if sc is None:
            out = {"declined": NO_SCENARIO}
        else:
            kind = sc["kind"]
            prim = SCENARIO_PRIMITIVE.get(kind)
            alts = self.targets(mode, loc, kind)
            if prim is None or not alts:
                out = {"declined": f"the twin cannot apply scenario {kind} at {loc}"}
            else:
                if kind == "span_added_loss" and sc.get("transient"):
                    prim = "MECHANICAL_DISTURBANCE"
                durs = [None]
                if sc.get("transient") or kind == "span_sop":
                    durs = list((self.practice.get("transient_durations_s") or {}).get(kind, [1.0]))
                rng = [float(x) for x in sc.get("range", [])] if sc.get("parameter") else []
                best = None
                for tg in alts:              # coarse grid over every duration, then refine the best only
                    coarse = [(self._grid_scores(prim, tg, dur, rng), dur) for dur in durs]
                    sc_best, dur = max(coarse, key=lambda x: x[0][0][0])
                    r = self._refine(prim, tg, dur, rng, sc, sc_best)
                    if len(durs) > 1:
                        r = self._refine_duration(prim, tg, durs, rng, sc, r)
                    if best is None or r["ll"] > best["ll"]:
                        best = r
                out = {**best, "scenario": kind}
        self.fits[key] = out
        return out

    def _score(self, prim, tg, v, dur):
        evs = self.events(prim, tg, v, dur)
        return self.loglik(self.features(self._base_events + evs)), evs

    def _grid(self, rng):
        lo, hi = rng
        if lo > 0 and hi / lo > 50:
            return [lo * (hi / lo) ** (i / (GRID - 1)) for i in range(GRID)]
        return [lo + (hi - lo) * i / (GRID - 1) for i in range(GRID)]

    def _grid_scores(self, prim, tg, dur, rng):
        """[(ll, value, events)] best first, over the coarse grid (one entry if the scenario has no parameter)."""
        if not rng:
            ll, evs = self._score(prim, tg, None, dur)
            return [(ll, None, evs)]
        out = []
        for v in self._grid(rng):
            ll, evs = self._score(prim, tg, v, dur)
            out.append((ll, v, evs))
        return sorted(out, key=lambda x: -x[0])

    def _refine(self, prim, tg, dur, rng, sc, scored) -> dict:
        best_ll, best_v, best_ev = scored[0]
        if rng:
            grid = self._grid(rng)
            i = grid.index(best_v)
            a, b = grid[max(0, i - 1)], grid[min(GRID - 1, i + 1)]
            for _ in range(GOLDEN):
                c1, c2 = b - 0.618 * (b - a), a + 0.618 * (b - a)
                (f1, e1), (f2, e2) = self._score(prim, tg, c1, dur), self._score(prim, tg, c2, dur)
                if max(f1, f2) > best_ll:
                    best_ll, best_v, best_ev = (f1, c1, e1) if f1 >= f2 else (f2, c2, e2)
                if f1 > f2:
                    b = c2
                else:
                    a = c1
        params = {sc.get("parameter", "value"): round(best_v, 4)} if best_v is not None else {}
        return {"ll": best_ll, "params": params, "events": best_ev, "duration": dur}

    def _refine_duration(self, prim, tg, durs, rng, sc, r) -> dict:
        """A transient's duration is a fitted parameter like its size: refined on a logarithmic scale between the
        neighbours of the best tried duration, at the best size, and the size then refined again at the new
        duration. (Fitting the duration only on the few documented values leaves a transient lasting between them
        poorly fitted, and its own prediction then misfits what it left.)"""
        ds = sorted(float(x) for x in durs)
        dur, v = r["duration"], next(iter(r["params"].values()), None)
        i = ds.index(dur)
        a, b = math.log(ds[max(0, i - 1)]), math.log(ds[min(len(ds) - 1, i + 1)])
        best_ll, best_d, best_ev = r["ll"], dur, r["events"]
        for _ in range(GOLDEN + 2):
            c1, c2 = b - 0.618 * (b - a), a + 0.618 * (b - a)
            (f1, e1), (f2, e2) = self._score(prim, tg, v, math.exp(c1)), self._score(prim, tg, v, math.exp(c2))
            if max(f1, f2) > best_ll:
                best_ll, best_d, best_ev = (f1, math.exp(c1), e1) if f1 >= f2 else (f2, math.exp(c2), e2)
            if f1 > f2:
                b = c2
            else:
                a = c1
        if best_d == dur:
            return r
        if not rng:
            return {**r, "ll": best_ll, "events": best_ev, "duration": round(best_d, 3)}
        scored = sorted(((self._score(prim, tg, x, best_d)[0], x) for x in self._grid(rng)), key=lambda y: -y[0])
        ll0, v0 = scored[0]
        r2 = self._refine(prim, tg, best_d, rng, sc, [(ll0, v0, self._score(prim, tg, v0, best_d)[1])])
        if r2["ll"] < best_ll:
            params = {sc.get("parameter", "value"): round(v, 4)} if v is not None else {}
            return {**r, "ll": best_ll, "params": params, "events": best_ev, "duration": round(best_d, 3)}
        return {**r2, "duration": round(best_d, 3)}

    def fit(self, h: Hypothesis) -> dict:
        """A hypothesis of one or several causes: the causes' changes applied together. Each cause is fitted alone,
        or, when the twin is sequential (peeling), each later cause is fitted given the earlier causes' changes."""
        if h.kind == HypothesisKind.NONE_OF_THESE:
            return {"declined": "none of these"}
        parts, base, base_key = [], [], ()
        for i, c in enumerate(h.causes):
            loc = _loc_str(c.location)
            if loc is None:
                return {"declined": f"location {c.location.key()} is not one the twin can place"}
            if self.sequential and i > 0:
                r = self.fit_cause_given(c.fault_mode, loc, base, base_key)
            else:
                r = self.fit_cause(c.fault_mode, loc)
            if "declined" in r:
                if not (UNTESTABLE_AS_NO_CHANGE and r["declined"] == NO_SCENARIO):
                    return r
                # a cause the twin cannot simulate: it predicts no change in transmission (C4)
                r = {"ll": None, "params": {}, "events": [], "untestable": True}
            parts.append(r)
            base, base_key = base + list(r["events"]), base_key + ((c.fault_mode, loc),)
        if len(parts) == 1:
            if parts[0].get("untestable"):
                return {**parts[0], "ll": self.loglik(self.features([]))}
            return parts[0]
        evs = [e for p in parts for e in p["events"]]
        return {"ll": self.loglik(self.features(evs)), "params": {k: v for p in parts for k, v in p["params"].items()},
                "events": evs}

    def likelihood(self, hyps: list[Hypothesis]) -> LikelihoodPayload:
        vals = {}
        bg = self.background()
        ll_null = self.loglik(self.base)
        for h in hyps:
            r = self.fit(h)
            key = h.key()
            if "declined" not in r and not self.equipment and r["ll"] - ll_null < NO_CHANGE_NATS:
                # the hypothesis explains the transmission data no better than no fault at all (a fault of equipment
                # behaviour only, or one fitted to almost nothing): the twin cannot tell it from a cause nobody
                # proposed, so it scores no better than the background (as a hypothesis with no twin scenario does),
                # rather than being credited with the no-fault score
                vals[key] = LikelihoodValue(log_likelihood=round(min(r["ll"], bg), 3),
                                            decline_reason="explains no transmission change")
                continue
            if "declined" in r:
                # the twin cannot tell this hypothesis from a cause nobody proposed: it scores as the background
                vals[key] = LikelihoodValue(log_likelihood=round(bg, 3), decline_reason=r["declined"])
            else:
                vals[key] = LikelihoodValue(log_likelihood=round(r["ll"], 3), fitted=r.get("params", {}))
        return LikelihoodPayload(values=vals, background_log_likelihood=round(bg, 3))

    # ------------------------------------------------------------------ predicting a check
    def predict_check(self, events: list[Event], check: str, params: dict, seed: int = 0,
                      t: Optional[float] = None) -> dict:
        """The answer the check would give under the hypothesised events, computed on the twin's own network at
        case time t (default: the case clock): the instrument's true reading in the twin, plus the twin's own
        calculation error. The world's instrument noise is not the twin's to know."""
        from ..world.checks import perform
        if not self.equipment and check not in TRANSMISSION_CHECKS:
            # an equipment check's answer is equipment behaviour: read by its documented meaning, not predicted
            return {"status": "not_modelled", "value": None, "minutes": 0, "dispatch_sites": [], "service_impact": "none"}
        t = self.at_s if t is None else float(t)
        # a fresh copy: checks that repair (cleaning, swapping a unit) shorten the event they find; the repairs of
        # the checks already made are applied first, as they were in the network
        w = self.world([copy.deepcopy(e) for e in events])
        for c, cp, tc in self.repair_checks:
            if tc < t:
                perform(w, self.dep, c, cp, tc, seed, noise=False, t_question=self.now)
        r = perform(w, self.dep, check, params, t, seed, noise=False, t_question=self.now)
        target = check + "/" + "/".join(f"{k}={v}" for k, v in sorted(params.items()))
        return {"status": r.status, "value": self._calc_error(r.value, target), "minutes": r.minutes,
                "dispatch_sites": r.dispatch_sites, "service_impact": r.service_impact}


def _loc_str(loc) -> Optional[str]:
    if loc.kind == "element":
        return loc.element
    if loc.kind == "span":
        return loc.span
    return None
