"""World simulator (specification §11.1): build, inject, run, acquire.

The state of the network is piecewise constant between breakpoints (event onsets and ends,
recurring pulses, and a 15-minute grid for ramps). For each segment the physics is evaluated
once. Alarms, performance-monitoring bins and instrument streams are then derived from the
segments, so sub-second transients are represented exactly even though most instruments
sample far more slowly. Only the harness may hold a WorldSim; the agent sees the lake.
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field
from typing import Optional

from .alarms import SOAK_CLEAR_S, SOAK_RAISE_S, Defect, defects
from .faults import Event, effects_at
from ..lake import Lake, Observation  # noqa: F401  (the lake is shared infrastructure)
from .network import Network
from .otn import client_port, otn_defects, service_states
from .physics import FEC_BER, Snapshot, evaluate

PM_BIN_S = 900.0

TIERS = {
    "baseline": {"alarms", "pm15"},
    "power": {"alarms", "pm15", "amp_power", "rx_power"},
    "ocm": {"alarms", "pm15", "amp_power", "rx_power", "ocm"},
    "coherent": {"alarms", "pm15", "amp_power", "rx_power", "ocm", "coherent", "health"},
    "full": {"alarms", "pm15", "amp_power", "rx_power", "ocm", "coherent", "health",
             "triggered", "distribution", "otdr", "context"},
}

NOISE = {"power_db": 0.2, "osnr_db": 0.5, "q_db": 0.2, "sop_frac": 0.1, "pump_pct": 0.5}


@dataclass
class Segment:
    t0: float
    t1: float
    snap: Snapshot
    defects: set[Defect]
    svc: dict = field(default_factory=dict)      # (service, egress site) -> otn.DirState


@dataclass
class WorldSim:
    net: Network
    seed: int = 0
    tier: str = "power"
    events: list[Event] = field(default_factory=list)
    context: list[dict] = field(default_factory=list)       # maintenance, provisioning, weather
    misreport: dict[str, float] = field(default_factory=dict)   # instrument element -> bias
    stale: dict[str, float] = field(default_factory=dict)       # instrument element -> since t
    misreport_since: dict[str, float] = field(default_factory=dict)   # instrument element -> from t
    deployment: Optional[dict[str, str]] = None   # site -> tier; None means `tier` everywhere
    chunk_fast_streams: Optional[bool] = None     # store 1 s streams as 60 s chunks (default: OTN networks)
    # the capability ladder (world/ladder.py): telemetry capabilities beyond the top tier, each opt-in ("L1".."L6");
    # empty, the world is exactly as before
    ladder: frozenset = frozenset()

    def __post_init__(self):
        if self.ladder and {"S", "R"} & set(self.ladder):
            from .ladder import want_spectra
            want_spectra(self.net, self.ladder)
        if self.chunk_fast_streams is None:
            self.chunk_fast_streams = bool(self.net.odu_paths)

    def site_of(self, element: str) -> str:
        n = self.net
        if element in n.transponders:
            return n.transponders[element].node
        if element in n.amps:
            a = n.amps[element]
            o = n.oms[a.oms]
            return o.dst if a.role == "preamp" else o.src
        if element.startswith("cp."):
            return element.rsplit(".", 1)[1]
        if ":" in element:                       # OCM "<site>:<oms>"
            return element.split(":", 1)[0]
        return ""

    def has(self, stream: str, element: str) -> bool:
        if self.deployment is None:
            return stream in TIERS[self.tier]
        return stream in TIERS[self.deployment.get(self.site_of(element), "baseline")]

    def inject(self, ev: Event) -> None:
        if ev.fault_mode in ("MAINTENANCE_ACTIVITY", "PROVISIONING_CHANGE", "WEATHER_EXPOSURE"):
            self.context.append({"type": ev.fault_mode, "element": ev.target, "t_start": ev.onset,
                                 "t_end": ev.onset + (ev.duration or 0.0)})
        elif ev.fault_mode == "INSTRUMENT_MISREPORT":
            self.misreport[ev.target] = ev.params.get("bias", 3.0)
            self.misreport_since[ev.target] = ev.onset
        elif ev.fault_mode == "TELEMETRY_STALE":
            self.stale[ev.target] = ev.onset
        self.events.append(ev)

    # ------------------------------------------------------------------ segments

    def _breakpoints(self, t0: float, t1: float) -> list[float]:
        bps = {t0, t1}
        for ev in self.events:
            for t in (ev.onset, ev.onset + ev.duration if ev.duration else None):
                if t is not None and t0 < t < t1:
                    bps.add(t)
            if ev.profile == "recurring" and ev.period and ev.pulse_len:
                k = 0
                while ev.onset + k * ev.period < t1:
                    for t in (ev.onset + k * ev.period, ev.onset + k * ev.period + ev.pulse_len):
                        if t0 < t < t1:
                            bps.add(t)
                    k += 1
            if ev.profile == "ramp":
                t = max(t0, ev.onset)
                while t < t1:
                    bps.add(t)
                    t += PM_BIN_S
        return sorted(bps)

    def segments(self, t0: float, t1: float) -> list[Segment]:
        bps = self._breakpoints(t0, t1)
        segs = []
        for a, b in zip(bps[:-1], bps[1:]):
            mid = (a + b) / 2
            fx = effects_at(self.events, mid)
            snap = evaluate(self.net, fx)
            dfx = defects(self.net, snap, laser_bias=fx.trx_laser_bias_pct, cards=fx.card_failed)
            svc = {}
            if self.net.odu_paths:
                svc = service_states(self.net, snap, fx)
                dfx |= otn_defects(self.net, svc, fx)
            segs.append(Segment(a, b, snap, dfx, svc))
        return segs

    # ------------------------------------------------------------------ run

    def run(self, t0: float, t1: float) -> Lake:
        rng = random.Random(self.seed)
        segs = self.segments(t0, t1)
        lake = Lake()
        streams = TIERS[self.tier] if self.deployment is None else set().union(
            *(TIERS[t] for t in self.deployment.values()))
        self._alarms(segs, lake)
        if "pm15" in streams:
            self._pm(segs, lake, t0, t1)
            if self.net.odu_paths:
                self._client_pm(segs, lake, t0, t1)
        if self.net.odu_paths:
            self._protection_events(segs, lake)
        if "amp_power" in streams:
            self._sample(segs, lake, rng, t0, t1, 10.0, self._amp_obs)
        if "rx_power" in streams:
            self._sample(segs, lake, rng, t0, t1, 10.0, self._rx_power_obs)
        if "ocm" in streams:
            self._sample(segs, lake, rng, t0, t1, 60.0, self._ocm_obs)
        if "coherent" in streams:
            if self.chunk_fast_streams:
                self._coherent_chunks(segs, lake, t0, t1)
            else:
                self._sample(segs, lake, rng, t0, t1, 1.0, self._coherent_obs)
        if "health" in streams:
            self._sample(segs, lake, rng, t0, t1, 60.0, self._health_obs)
        if "triggered" in streams:
            self._triggered(segs, lake)
        if "context" in streams:
            for c in self.context:
                lake.add(source="context", observable=c["type"].lower(), element=c["element"],
                         instrument=None, t_start=c["t_start"], t_end=c["t_end"], value=True)
        if self.stale:
            self._freeze_stale(lake)
        if self.ladder:
            from .ladder import apply_world
            apply_world(self, segs, lake, t0, t1)
        self._segs = segs
        return lake

    def _freeze_stale(self, lake: Lake) -> None:
        """Telemetry gone stale keeps reporting the last value it had before it froze."""
        groups: dict = {}
        for o in lake.observations:
            if o.element in self.stale and o.source in ("telemetry", "instrument"):
                groups.setdefault((o.observable, o.element), []).append(o)
        for (obs, el), os_ in groups.items():
            since = self.stale[el]
            os_.sort(key=lambda o: o.t_start)
            before = [o for o in os_ if o.t_start < since]
            if not before:
                continue
            last = before[-1].value
            frozen = last[-1] if isinstance(last, list) else last
            for o in os_:
                if o.t_start >= since:
                    o.value = [frozen] * len(o.value) if isinstance(o.value, list) else frozen

    # ------------------------------------------------------------------ alarms with soak

    def _alarms(self, segs: list[Segment], lake: Lake) -> None:
        intervals: dict[Defect, list[list[float]]] = {}
        for s in segs:
            for d in s.defects:
                iv = intervals.setdefault(d, [])
                if iv and abs(iv[-1][1] - s.t0) < 1e-9:
                    iv[-1][1] = s.t1
                else:
                    iv.append([s.t0, s.t1])
        for d, ivs in sorted(intervals.items(), key=lambda x: (x[1][0][0], x[0].type, x[0].element)):
            merged: list[list[float]] = []
            for a, b in ivs:                 # gaps shorter than the clear soak keep the alarm up
                if merged and a - merged[-1][1] < SOAK_CLEAR_S:
                    merged[-1][1] = b
                else:
                    merged.append([a, b])
            for a, b in merged:
                if b - a >= SOAK_RAISE_S:
                    lake.add(source="alarm", observable=d.type, element=d.element, instrument=d.element,
                             t_start=a + SOAK_RAISE_S, t_end=b + SOAK_CLEAR_S, value=d.severity)

    # ------------------------------------------------------------------ 15-minute PM

    def _pm(self, segs: list[Segment], lake: Lake, t0: float, t1: float) -> None:
        b = t0
        while b < t1:
            e = min(b + PM_BIN_S, t1)
            inbin = [(max(s.t0, b), min(s.t1, e), s) for s in segs if s.t1 > b and s.t0 < e]
            keys = inbin[0][2].snap.rx.keys() if inbin else []
            for key in keys:
                if not self.has("pm15", inbin[0][2].snap.rx[key].trx):
                    continue
                vals = [(x1 - x0, s.snap.rx[key]) for x0, x1, s in inbin]
                pw = [r.rx_power_dbm for _, r in vals]
                finite = [(w, r.rx_power_dbm) for w, r in vals if r.rx_power_dbm > -math.inf]
                avg = (sum(w * p for w, p in finite) / sum(w for w, _ in finite)) if finite else -math.inf
                es = sum(math.ceil(x1 - x0) for x0, x1, s in inbin
                         if s.snap.rx[key].pre_fec_ber >= FEC_BER or s.snap.rx[key].rx_power_dbm == -math.inf)
                trx = vals[0][1].trx
                cal = self.net.calibration_db.get(trx, 0.0)
                if b >= self.misreport_since.get(trx, math.inf):
                    cal += self.misreport.get(trx, 0.0)
                pw = [x + cal if x > -math.inf else x for x in pw]
                avg = avg + cal if avg > -math.inf else avg
                lake.add(source="pm_counter", observable="rx_power_pm15", element=trx, instrument=trx,
                         t_start=b, t_end=e, value={"min": min(pw), "avg": avg, "max": max(pw)}, sigma=0.2)
                lake.add(source="pm_counter", observable="errored_seconds_pm15", element=trx,
                         instrument=trx, t_start=b, t_end=e, value=es)
            b = e

    # ------------------------------------------------------------------ sampled streams

    def _sample(self, segs, lake, rng, t0, t1, period, fn) -> None:
        t = t0
        i = 0
        while t < t1:
            seg = next(s for s in segs if s.t0 <= t < s.t1)
            window = [s for s in segs if s.t1 > t and s.t0 < t + period]
            fn(lake, rng, t, seg, window)
            i += 1
            t = t0 + i * period

    def _noisy(self, rng, element: str, v: float, sigma: float, t: float, calibrated: bool = True,
               optical: bool = True) -> float:
        """A reading as the instrument reports it: noise, any misreport fault, and (world version 2) the
        instrument's hidden calibration offset, which applies to optical readings in dB."""
        if v == -math.inf:
            return v
        cal = self.net.calibration_db.get(element, 0.0) if calibrated else 0.0
        # a misreporting instrument is an optical one (power, OSNR); equipment health readings are not affected
        bias = self.misreport.get(element, 0.0) if optical and t >= self.misreport_since.get(element, -math.inf) else 0.0
        return v + rng.gauss(0, sigma) + bias + cal

    def _amp_obs(self, lake, rng, t, seg, window) -> None:
        for aid, r in seg.snap.amps.items():
            if not self.has("amp_power", aid):
                continue
            for obs, v in (("amp_input_power", r.input_dbm_per_ch), ("amp_output_power", r.output_dbm_per_ch)):
                lake.add(source="telemetry", observable=obs, element=aid, instrument=aid, t_start=t,
                         t_end=None, value=self._noisy(rng, aid, v, NOISE["power_db"], t), sigma=NOISE["power_db"])

    def _rx_power_obs(self, lake, rng, t, seg, window) -> None:
        for (lp, d), r in seg.snap.rx.items():
            if not self.has("rx_power", r.trx):
                continue
            lake.add(source="telemetry", observable="rx_power", element=r.trx, instrument=r.trx, t_start=t,
                     t_end=None, value=self._noisy(rng, r.trx, r.rx_power_dbm, NOISE["power_db"], t),
                     sigma=NOISE["power_db"])

    def _ocm_obs(self, lake, rng, t, seg, window) -> None:
        for (oms, slot), (p, osnr) in seg.snap.ocm.items():
            el = f"{self.net.oms[oms].dst}:{oms}"
            if not self.has("ocm", el):
                continue
            lake.add(source="instrument", observable="ocm_channel_power", element=f"{el}|{slot}",
                     instrument=el, t_start=t, t_end=None,
                     value=self._noisy(rng, el, p, NOISE["power_db"], t), sigma=NOISE["power_db"])
            lake.add(source="instrument", observable="ocm_osnr", element=f"{el}|{slot}", instrument=el,
                     t_start=t, t_end=None,
                     value=-math.inf if p == -math.inf else self._noisy(rng, el, osnr, NOISE["osnr_db"], t),
                     sigma=NOISE["osnr_db"])

    def _coherent_obs(self, lake, rng, t, seg, window) -> None:
        for key, r in seg.snap.rx.items():
            worst_ber = max(s.snap.rx[key].pre_fec_ber for s in window)
            peak_sop = max(s.snap.rx[key].sop_rate_rad_s for s in window)
            lake.add(source="telemetry", observable="pre_fec_ber", element=r.trx, instrument=r.trx,
                     t_start=t, t_end=t + 1.0, value=worst_ber)
            lake.add(source="telemetry", observable="est_osnr", element=r.trx, instrument=r.trx, t_start=t,
                     t_end=None, value=self._noisy(rng, r.trx, r.osnr_db, NOISE["osnr_db"], t), sigma=NOISE["osnr_db"])
            lake.add(source="telemetry", observable="sop_rate", element=r.trx, instrument=r.trx, t_start=t,
                     t_end=t + 1.0, value=peak_sop * (1 + rng.gauss(0, NOISE["sop_frac"])) + 50 * abs(rng.gauss(0, 1)))
            lake.add(source="telemetry", observable="dgd", element=r.trx, instrument=r.trx, t_start=t,
                     t_end=None, value=r.dgd_ps + rng.gauss(0, 0.3), sigma=0.3)

    def _coherent_chunks(self, segs, lake, t0, t1, chunk: float = 60.0) -> None:
        """Per-second coherent telemetry stored as 60 s chunks (value: list of per-second values)."""
        import numpy as np
        rng = np.random.default_rng(self.seed + 7)
        n = int(round(t1 - t0))
        # per second: worst BER and peak SOP over segments overlapping that second
        sec_segs = []
        j = 0
        for k in range(n):
            a, b = t0 + k, t0 + k + 1
            while segs[j].t1 <= a:
                j += 1
            w, jj = [], j
            while jj < len(segs) and segs[jj].t0 < b:
                w.append(segs[jj])
                jj += 1
            sec_segs.append(w)
        for key, r0 in segs[0].snap.rx.items():
            trx = r0.trx
            if not self.has("coherent", trx):
                continue
            ber = np.array([max(sg.snap.rx[key].pre_fec_ber for sg in w) for w in sec_segs])
            sop = np.array([max(sg.snap.rx[key].sop_rate_rad_s for sg in w) for w in sec_segs])
            osnr = np.array([w[0].snap.rx[key].osnr_db for w in sec_segs])
            dgd = np.array([w[0].snap.rx[key].dgd_ps for w in sec_segs])
            secs = t0 + np.arange(n)
            bias = np.where(secs >= self.misreport_since.get(trx, -math.inf), self.misreport.get(trx, 0.0), 0.0) \
                + self.net.calibration_db.get(trx, 0.0)
            osnr_n = np.where(np.isfinite(osnr), osnr + rng.normal(0, NOISE["osnr_db"], n) + bias, -999.0)
            sop_n = sop * (1 + rng.normal(0, NOISE["sop_frac"], n)) + 50 * np.abs(rng.normal(0, 1, n))
            dgd_n = dgd + rng.normal(0, 0.3, n)
            for c0 in range(0, n, int(chunk)):
                c1 = min(n, c0 + int(chunk))
                ta, tb = t0 + c0, t0 + c1
                for obs, arr, sig in (("pre_fec_ber", ber, None), ("est_osnr", osnr_n, NOISE["osnr_db"]),
                                      ("sop_rate", sop_n, None), ("dgd", dgd_n, 0.3)):
                    lake.add(source="telemetry", observable=obs, element=trx, instrument=trx, t_start=ta,
                             t_end=tb, value=[round(float(x), 6) if obs != "pre_fec_ber" else float(x)
                                              for x in arr[c0:c1]], sigma=sig, period=1.0)

    # ------------------------------------------------------------------ OTN service layer

    def _client_pm(self, segs, lake, t0, t1) -> None:
        """15-minute client PM at each egress client port: errored and unavailable seconds."""
        switches = self._switch_times(segs)
        b = t0
        while b < t1:
            e = min(b + PM_BIN_S, t1)
            inbin = [(max(s.t0, b), min(s.t1, e), s) for s in segs if s.t1 > b and s.t0 < e]
            for key in inbin[0][2].svc:
                port = client_port(*key)
                if not self.has("pm15", port):
                    continue
                down = sum(x1 - x0 for x0, x1, s in inbin if not s.svc[key].up)
                hits = sum(1 for (k, t) in switches if k == key and b <= t < e)
                lake.add(source="pm_counter", observable="client_errored_seconds_pm15", element=port,
                         instrument=port, t_start=b, t_end=e, value=int(math.ceil(down)) + hits)
                lake.add(source="pm_counter", observable="client_unavailable_seconds_pm15", element=port,
                         instrument=port, t_start=b, t_end=e, value=int(down) if down >= 10 else 0)
            b = e

    def _switch_times(self, segs):
        out = []
        for prev, cur in zip(segs, segs[1:]):
            for key, st in cur.svc.items():
                a0, a1 = prev.svc[key].active, st.active
                if a0 != a1 and a0 is not None and a1 is not None:
                    out.append((key, cur.t0, a0, a1))
        return [(k, t) for k, t, _, _ in out]

    def _protection_events(self, segs, lake) -> None:
        for prev, cur in zip(segs, segs[1:]):
            for key, st in cur.svc.items():
                a0, a1 = prev.svc[key].active, st.active
                if a0 != a1 and a0 is not None and a1 is not None:
                    port = client_port(*key)
                    lake.add(source="event", observable="PROT_SWITCH", element=port, instrument=port,
                             t_start=cur.t0, t_end=None, value=f"{a0}->{a1}")

    def _health_obs(self, lake, rng, t, seg, window) -> None:
        for aid, r in seg.snap.amps.items():
            if not self.has("health", aid):
                continue
            lake.add(source="telemetry", observable="pump_current_pct", element=aid, instrument=aid, t_start=t,
                     t_end=None, value=self._noisy(rng, aid, r.pump_current_pct, NOISE["pump_pct"], t, calibrated=False,
                                                                 optical=False),
                     sigma=NOISE["pump_pct"])
        for tid in sorted(self.net.transponders):          # transmit laser bias current, % of beginning of life
            if not self.has("health", tid):
                continue
            r2 = random.Random(zlib.crc32(f"laser/{self.seed}/{tid}/{t:.3f}".encode()))   # own stream
            v = seg.snap.laser_bias_pct.get(tid, 100.0) + r2.gauss(0, NOISE["pump_pct"])
            lake.add(source="telemetry", observable="laser_bias_pct", element=tid, instrument=tid, t_start=t,
                     t_end=None, value=v, sigma=NOISE["pump_pct"])

    def _triggered(self, segs: list[Segment], lake: Lake) -> None:
        """Flight-recorder capture: any segment under one second with a BER or SOP excursion."""
        for s in segs:
            dur = s.t1 - s.t0
            if dur >= 1.0:
                continue
            for key, r in s.snap.rx.items():
                if r.pre_fec_ber > 1e-3 or r.sop_rate_rad_s > 1e4:
                    lake.add(source="instrument", observable="transient_capture", element=r.trx,
                             instrument=r.trx, t_start=s.t0, t_end=s.t1,
                             value={"duration_s": dur, "peak_ber": r.pre_fec_ber,
                                    "peak_sop_rate": r.sop_rate_rad_s, "rx_power": r.rx_power_dbm})

    # ------------------------------------------------------------------ acquire (on demand)

    def acquire_otdr(self, span_id: str, t: float, rng_seed: int = 0, noise: bool = True) -> dict:
        """Embedded OTDR trace on a span: events by position, with 0.05 km resolution noise (none if noise False)."""
        if noise:
            rng = random.Random(rng_seed)
        else:
            from .checks import Exact
            rng = Exact()
        sp = self.net.spans[span_id]
        events = []
        for ev in self.events:
            if ev.target != span_id or ev.level(t) <= 0:
                continue
            pos = ev.params.get("position_km", sp.length_km / 2)
            if ev.fault_mode == "FIBRE_CUT":
                events.append({"position_km": round(pos + rng.gauss(0, 0.05), 2), "type": "end_of_fibre"})
            elif ev.fault_mode in ("SPAN_LOSS_INCREASE", "MECHANICAL_DISTURBANCE"):
                loss = ev.params.get("added_loss_db", ev.params.get("loss_excursion_db", 0))
                events.append({"position_km": round(pos + rng.gauss(0, 0.05), 2), "type": "loss",
                               "loss_db": round(loss + rng.gauss(0, 0.1), 2)})
        return {"span": span_id, "length_km": sp.length_km, "events": events}
