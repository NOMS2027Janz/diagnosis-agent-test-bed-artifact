"""The capability ladder: telemetry capabilities that today's instruments lack, each an opt-in option of the world
(spec/PROTOCOL_CAMPAIGN.md, amendment "the capability ladder"). WorldSim.ladder holds the rungs switched on; with
none the world is exactly as before. Each rung adds what an instrument would observe, nothing else: it is computed
from the network's physical state in each segment (the snapshot and the effects acting on it), never from the
injected faults' labels. The agent reads it by its documented meaning (knowledge/ladder/telemetry.yaml) or through
the twin, and the harness's equivalence classes see the same observable (signature_items, cont_items), so a rung can
only separate what its observable separates.

  L1 stream liveness    every sampled telemetry stream carries a sample counter, recorded with each value (field
                        seq of the observation; a list for a chunk of a fast stream). A stream gone stale keeps
                        repeating its last value and its counter stops; a misreporting instrument keeps counting.
                        The management system raises TELEMETRY_STREAM_LOST (source "nms_alarm") on a unit whose
                        counter has not advanced for HOLD_SAMPLES sample periods and at least HOLD_S: the alarm
                        manual troubleshooting starts from. It is the counter read by its documented threshold, so it
                        carries nothing the counter does not.
  L2 per-span DGD       a DGD estimate for every span (as a per-section estimate gives), every 60 s ("span_dgd").
  L3 per-port WSS       high-resolution channel monitoring at each WSS output port: the channel's power leaving the
                        port ("wss_port_power") and the measured offset of the channel filter's centre
                        ("wss_filter_offset"), every 60 s, element "oms|slot" (the WSS channel after that OMS).
  L4 located events     per-span polarisation-rate sensing and coordinated transient capture: every brief excursion
                        on a span (a polarisation change faster than SOP_SENSE_RAD_S, or a loss excursion above
                        LOSS_SENSE_DB, over within CAPTURE_MAX_S) is captured with its time, duration and size
                        ("span_transient_capture"); about CAPTURE_MISS of them are missed.
  L5 event logs         the equipment logs its line protection switches ("LINE_PROTECTION_SWITCH", element the
                        lightpath) and the channel add/drop actions that change an OMS's loading ("CHANNEL_ADD_DROP",
                        element the OMS), timestamped to the second; an entry reaches the management system after a
                        delay (log-normal, median LOG_DELAY_MEDIAN_S) and LOG_LOSS of entries are lost (source "log").
  L6 instrument cross-check  a remote check (xcheck_reading, 5 minutes) that reads the quantity an amplifier's or a
                        receiver's power instrument reports from an independent instrument at the same point, now and
                        15 minutes before the question, and reports whether the two instruments' changes agree.
"""
from __future__ import annotations

import math
import random
import zlib

COUNTED_SOURCES = ("telemetry", "instrument")
NOT_COUNTED = ("transient_capture", "span_transient_capture")   # triggered captures are events, not sampled streams
HOLD_S, HOLD_SAMPLES = 120.0, 3               # a counter is stopped when it has not advanced for this long
STREAM_LOST = "TELEMETRY_STREAM_LOST"
LADDER_PERIOD_S = 60.0
NOISE_L = {"span_dgd": 1.0, "wss_port_power": 0.2, "wss_filter_offset": 0.5,
           # spectral monitoring (spectral mode; docs/campaign/spectral_design.md): S at every span end, R at receivers
           "sp_power": 0.3, "sp_offset": 0.5, "sp_width": 1.0,
           "rx_offset": 0.1, "rx_width": 0.2, "rx_asym": 0.01, "rx_filter_penalty": 0.05}
SPECTRAL_OBS = {"S": ("sp_power", "sp_offset", "sp_width"),
                "R": ("rx_offset", "rx_width", "rx_asym", "rx_filter_penalty")}
S_PERIOD_EVERY = 1               # S reports every ladder period (60 s)
PENALTY_MODEL_ERR_DB = 0.2       # the learned shape-to-penalty model's error: a fixed bias per receiver and case
SOP_SENSE_RAD_S, LOSS_SENSE_DB, CAPTURE_MAX_S, CAPTURE_MISS = 5.0e3, 0.3, 60.0, 0.02
LOG_LOSS, LOG_DELAY_MEDIAN_S = 0.10, 20.0
XCHECK_REF_S, XCHECK_AGREE_DB = 900.0, 1.0
# rung L6's check, beside the catalogue (world/checks.CHECKS; the same form): unavailable unless the world has L6
LADDER_CHECKS = {
    "xcheck_reading": ("remote", 5, "none", "a unit's power reading compared with an independent instrument's"),
}
FILTER_PENALTY_PER_GHZ2 = 0.02               # the world's filter penalty (faults.effects_at): 0.02 dB per GHz^2


def _rng(w, *key) -> random.Random:
    return random.Random(zlib.crc32(("ladder/%d/" % w.seed + "/".join(map(str, key))).encode()))


def want_spectra(net, ladder) -> None:
    """Span-end spectra are computed only when rung S reads them (they cost time on every evaluation)."""
    if "S" in set(ladder or ()):
        net.span_spectra_wanted = True
    if "R" in set(ladder or ()):
        net.rx_spectra_wanted = True


def apply_world(w, segs, lake, t0: float, t1: float) -> None:
    if {"L2", "L3", "S", "R"} & set(w.ladder):
        _sampled(w, segs, lake, t0, t1)
    if "L4" in w.ladder:
        _captures(w, segs, lake, t1)
    if "L5" in w.ladder:
        _logs(w, segs, lake, t1)
    if "L1" in w.ladder:                       # last: every sampled stream, the ladder's own included, is counted
        _counters(w, lake, t1)


# ---------------------------------------------------------------------- what each segment's state shows (L2, L3)

def segment_streams(net, snap, fx, ladder) -> dict:
    """{(observable, element): true value} of the ladder's sampled streams in one state of the network (the twin
    computes the same on its own network)."""
    from .physics import PMD_PS_SQRT_KM, WSS_NOMINAL_ATT_DB
    out = {}
    if "L2" in ladder:
        for sid, sp in net.spans.items():
            pmd = sp.pmd_ps_sqrt_km if sp.pmd_ps_sqrt_km is not None else PMD_PS_SQRT_KM
            out[("span_dgd", sid)] = math.sqrt((pmd * math.sqrt(sp.length_km)) ** 2
                                               + fx.span_dgd_extra_ps.get(sid, 0.0) ** 2)
    if "L3" in ladder:
        for (oid, slot), att in snap.wss_att_db.items():
            el = f"{oid}|{slot}"
            p_in = snap.ocm.get((oid, slot), (-math.inf, 0.0))[0]
            out[("wss_port_power", el)] = (-math.inf if p_in == -math.inf or att == math.inf
                                           else p_in + WSS_NOMINAL_ATT_DB - att)
            pen = fx.wss_filter_penalty_db.get((oid, slot), 0.0)
            out[("wss_filter_offset", el)] = math.sqrt(max(pen, 0.0) / FILTER_PENALTY_PER_GHZ2)
    if "S" in ladder:
        for (sp, slot), r in snap.spectra_span.items():
            el = f"{sp}|{slot}"
            out[("sp_power", el)] = r["power"]
            out[("sp_offset", el)] = float(r["centroid"])
            out[("sp_width", el)] = float(r["width"])
    if "R" in ladder:
        for key, r in snap.spectra_rx.items():
            rx = snap.rx.get(key)
            if rx is None or not math.isfinite(rx.rx_power_dbm):
                continue
            out[("rx_offset", rx.trx)] = float(r["centroid"])
            out[("rx_width", rx.trx)] = float(r["width"])
            out[("rx_asym", rx.trx)] = float(r["asym"])
            out[("rx_filter_penalty", rx.trx)] = float(r["penalty_over_nominal"])
    return out


def _model_bias(w, trx: str) -> float:
    """The learned penalty model's error at one receiver in one case: fixed, as a model's error is."""
    return _rng(w, "pm", trx).gauss(0, PENALTY_MODEL_ERR_DB)


def _fx(w, s):
    from .faults import effects_at
    return effects_at(w.events, (s.t0 + s.t1) / 2)


def _sampled(w, segs, lake, t0, t1) -> None:
    rng = _rng(w, "sampled")
    t, i = t0, 0
    while t < t1:
        seg = next(s for s in segs if s.t0 <= t < s.t1)
        for (ob, el), v in sorted(segment_streams(w.net, seg.snap, _fx(w, seg), w.ladder).items()):
            if ob in SPECTRAL_OBS["S"] and i % S_PERIOD_EVERY:
                continue
            sig = NOISE_L[ob]
            val = v if v == -math.inf else v + rng.gauss(0, sig)
            if ob == "rx_filter_penalty" and math.isfinite(val):
                val += _model_bias(w, el)
                sig = math.hypot(sig, PENALTY_MODEL_ERR_DB)
            lake.add(source="instrument", observable=ob, element=el, instrument=el, t_start=t, t_end=None,
                     value=val, sigma=sig)
        i += 1
        t = t0 + i * LADDER_PERIOD_S


def cont_items(w, i, snap, fx) -> dict:
    """The ladder's sampled streams for a segment, as the harness's signatures compare them: {(i, ob, el): (value,
    sigma)}."""
    if not ({"L2", "L3", "S", "R"} & set(w.ladder)):
        return {}
    return {(i, ob, el): (v, math.hypot(NOISE_L[ob], PENALTY_MODEL_ERR_DB) if ob == "rx_filter_penalty" else NOISE_L[ob])
            for (ob, el), v in segment_streams(w.net, snap, fx, w.ladder).items()}


# ---------------------------------------------------------------------- L4 located events

def excursions(w, segs, t1: float) -> list:
    """[(span, kind, t_start, duration_s, peak)] of brief excursions on spans: a run of segments with the span's
    polarisation changing faster than SOP_SENSE_RAD_S ("sop") or its loss raised by more than LOSS_SENSE_DB
    ("loss"), over within CAPTURE_MAX_S and before t1."""
    runs: dict = {}
    out = []
    for s in segs:
        fx = _fx(w, s)
        now = {}
        for sp, r in fx.span_sop_rate.items():
            if r >= SOP_SENSE_RAD_S:
                now[(sp, "sop")] = r
        for sp, x in fx.span_extra_loss_db.items():
            if LOSS_SENSE_DB <= x < math.inf:
                now[(sp, "loss")] = x
        for k in list(runs):
            if k not in now:
                a, peak = runs.pop(k)
                if s.t0 - a <= CAPTURE_MAX_S:
                    out.append((k[0], k[1], a, s.t0 - a, peak))
        for k, v in now.items():
            if k in runs:
                runs[k] = (runs[k][0], max(runs[k][1], v))
            else:
                runs[k] = (s.t0, v)
    return sorted(out, key=lambda x: (x[2], x[0], x[1]))


def _captures(w, segs, lake, t1) -> None:
    for sp, kind, a, dur, peak in excursions(w, segs, t1):
        if _rng(w, "capture", sp, kind, round(a, 3)).random() < CAPTURE_MISS:
            continue
        lake.add(source="instrument", observable="span_transient_capture", element=sp, instrument=sp, t_start=a,
                 t_end=a + dur, value={"kind": kind, "duration_s": round(dur, 4),
                                       ("peak_sop_rate" if kind == "sop" else "loss_excursion_db"): round(peak, 3)})


# ---------------------------------------------------------------------- L5 equipment event logs

def logged_actions(w, segs) -> list:
    """[(type, element, t)] of the equipment actions the logs record: the start of each line protection switch and
    of each change in an OMS's channel loading."""
    out, prev = [], (set(), set())
    for s in segs:
        fx = _fx(w, s)
        hits, loads = set(fx.protection_hit), set(fx.oms_power_excursion_db)
        out += [("LINE_PROTECTION_SWITCH", lp, s.t0) for lp in sorted(hits - prev[0])]
        out += [("CHANNEL_ADD_DROP", o, s.t0) for o in sorted(loads - prev[1])]
        prev = (hits, loads)
    return out


def _logs(w, segs, lake, t1) -> None:
    for typ, el, t in logged_actions(w, segs):
        r = _rng(w, "log", typ, el, round(t, 3))
        if r.random() < LOG_LOSS:
            continue
        arrived = t + min(600.0, r.lognormvariate(math.log(LOG_DELAY_MEDIAN_S), 0.8))
        if arrived > t1:
            continue
        lake.add(source="log", observable=typ, element=el, instrument=el, t_start=float(math.floor(t)), t_end=None,
                 value={"t_arrived": round(arrived, 1)})


# ---------------------------------------------------------------------- L6 instrument cross-check

def xcheck(world, params: dict, t: float, rng, t_q: float):
    """The answer of xcheck_reading on a unit (an amplifier's input power or a receiver's received power): whether
    the change its own instrument reports since XCHECK_REF_S before the question agrees, within XCHECK_AGREE_DB, with
    the change an independent instrument at the same point measures. None (unavailable) without rung L6."""
    if "L6" not in getattr(world, "ladder", ()):
        return None
    from .checks import _snap
    u = params["unit"]
    net = world.net
    if u in net.amps:
        true = lambda tt: _snap(world, tt).amps[u].input_dbm_per_ch  # noqa: E731
    elif u in net.transponders:
        def true(tt):
            r = next((r for r in _snap(world, tt).rx.values() if r.trx == u), None)
            if r is None:
                raise KeyError(u)
            return r.rx_power_dbm
    else:
        raise KeyError(u)
    t_ref = max(0.0, t_q - XCHECK_REF_S)

    def reported(tt):
        since = world.stale.get(u)
        if since is not None and tt >= since and since > 0:
            tt = since - 1.0                         # a stale stream repeats what it read before it froze
        v = true(tt)
        if v == -math.inf:
            return v
        return v + (world.misreport.get(u, 0.0) if tt >= world.misreport_since.get(u, math.inf) else 0.0)

    ind = [true(t_ref), true(t)]
    rep = [reported(t_ref), reported(t)]
    if any(x == -math.inf for x in ind + rep):
        agree = [x == -math.inf for x in ind] == [x == -math.inf for x in rep]
    else:
        d_ind = (ind[1] + rng.gauss(0, 0.1)) - (ind[0] + rng.gauss(0, 0.1))
        d_rep = (rep[1] + rng.gauss(0, 0.2)) - (rep[0] + rng.gauss(0, 0.2))
        agree = abs(d_rep - d_ind) <= XCHECK_AGREE_DB
    return {"unit": u, "agree": bool(agree)}


# ---------------------------------------------------------------------- L1 stream liveness

def _period(os_) -> float:
    o = os_[0]
    if o.period:
        return float(o.period)
    ts = sorted({x.t_start for x in os_})
    return (ts[1] - ts[0]) if len(ts) > 1 else 60.0


def _counters(w, lake, t1: float) -> None:
    """Number every sample of every sampled stream; a stale unit's counter stops where its values froze (the same
    rule as WorldSim._freeze_stale: an observation starting at or after the freeze, on a stream that reported
    before it)."""
    groups: dict = {}
    for o in lake.observations:
        if o.source in COUNTED_SOURCES and o.observable not in NOT_COUNTED and not isinstance(o.value, dict):
            groups.setdefault((o.observable, o.element), []).append(o)
    raise_at: dict = {}
    for (ob, el), os_ in groups.items():
        os_.sort(key=lambda o: o.t_start)
        since = w.stale.get(el)
        freezes = since is not None and any(o.t_start < since for o in os_)
        n = 0
        for o in os_:
            k = len(o.value) if isinstance(o.value, list) else 1
            if freezes and o.t_start >= since:
                o.seq = [n] * k if isinstance(o.value, list) else n
            else:
                o.seq = list(range(n + 1, n + k + 1)) if isinstance(o.value, list) else n + 1
                n += k
        if freezes:
            t = since + max(HOLD_S, HOLD_SAMPLES * _period(os_))
            raise_at[el] = min(raise_at.get(el, t), t)
    for el, t in sorted(raise_at.items()):
        if t <= t1:
            lake.add(source="nms_alarm", observable=STREAM_LOST, element=el, instrument=el, t_start=t, t_end=None,
                     value="minor")


def stopped_units(w, t_end: float) -> frozenset:
    """The units whose counters a reader would see stopped by t_end (for the harness's signatures)."""
    if "L1" not in w.ladder:
        return frozenset()
    streams = ("amp_power", "rx_power", "coherent", "health")
    return frozenset(el for el, since in w.stale.items()
                     if since > 0 and since + HOLD_S <= t_end and any(w.has(s, el) for s in streams))


# ---------------------------------------------------------------------- the harness's signatures

def signature_items(w, segs, t_end: float) -> tuple:
    """What the event-like rungs add to the data a diagnosis could have had (harness/equivalence.Signature): a tuple
    of hashable items, empty when no rung is on. Noise-free: captures and log entries as the instruments would
    record them were none missed or lost."""
    if not w.ladder:
        return ()
    out = []
    if "L1" in w.ladder:
        out.append(("L1", stopped_units(w, t_end)))
    if "L4" in w.ladder:
        out.append(("L4", frozenset((sp, kind) for sp, kind, *_ in excursions(w, segs, t_end))))
    if "L5" in w.ladder:
        out.append(("L5", frozenset((typ, el) for typ, el, _ in logged_actions(w, segs))))
    return tuple(out)
