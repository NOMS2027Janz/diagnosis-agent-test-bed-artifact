"""The agent's use of the capability ladder's observables (world/ladder.py), each by its documented meaning
(knowledge/ladder/telemetry.yaml) or through the twin, never learned. Only the ladder's arms construct these; the
agent as built never does.

L1 stream liveness (StreamLiveness):
  generation   TELEMETRY_STALE at every unit whose sample counter stopped, and only there (one hypothesis per unit,
               named by its first optical instrument, as the monitoring line names it);
  evidence     a term of its own (evidence "stream_counters", tau 1): a stopped counter not accounted for by stale
               telemetry at that unit costs stopped_unexplained_nats (so every cause that needs the stream live, or
               says nothing of it, is marked down, and so is a cause nobody proposed); stale telemetry hypothesised
               at a unit whose counter advances costs live_contradicts_stale_nats.
  Manual troubleshooting reads the management system's TELEMETRY_STREAM_LOST alarm with the ladder's procedure
  (knowledge/ladder/procedures.yaml): ladder_library, LadderGrounding and the alarm source "nms_alarm".
L2 per-span DGD: through the twin (GraphTwin(ladder=...) predicts each span's DGD).
L3 per-port WSS readings (PortMonitor), L4 located events (LocatedEvents), L5 event logs (EventLogs): a generation
  line at the places the readings name and an evidence term of their own, each by the documented meaning.
L6 cross-check: a check offered at the contenders' units (xcheck_for) and read by its meaning (xcheck_direct).
"""
from __future__ import annotations

import copy
import math
from functools import lru_cache
from pathlib import Path

import yaml

from ..schemas import Candidate, GeneratorPayload, LikelihoodPayload, LikelihoodValue
from .grounding import GroundingTool
from .walks import single

ROOT = Path(__file__).resolve().parents[3] / "knowledge" / "ladder"
RUNGS = ("L1", "L2", "L3", "L4", "L5", "L6")
COUNTED = ("amp_input_power", "amp_output_power", "rx_power", "ocm_channel_power", "ocm_osnr", "pre_fec_ber",
           "est_osnr", "sop_rate", "dgd", "pump_current_pct", "laser_bias_pct")
OPTICAL = ("amp_input_power", "amp_output_power", "rx_power")
MONITORING = ("INSTRUMENT_MISREPORT", "TELEMETRY_STALE")


@lru_cache(maxsize=1)
def doc() -> dict:
    return yaml.safe_load((ROOT / "telemetry.yaml").read_text())


def rung(name: str) -> dict:
    return doc()["rungs"][name]


def unit_instrument(kg, el: str):
    """The instrument a monitoring hypothesis names for a unit: its first optical instrument (as loop.monitoring_payload
    names it, so the two lines' proposals are one hypothesis)."""
    for i in kg.instruments_for_lake_element(el):
        if any(o in OPTICAL for o in kg.g.nodes[i].get("observes", [])):
            return i
    return None


def _unit(kg, el: str) -> str:
    """The lake unit a hypothesis location names (an instrument names the unit it is on)."""
    if kg.has(el) and kg.cls(el) == "Instrument":
        return kg.g.nodes[el].get("lake_element", el)
    return el


# ---------------------------------------------------------------------- L1

class StreamLiveness:
    name, version = "ladder.liveness", "0.1"

    def __init__(self, substrate):
        self.sub, self.kg = substrate, substrate.kg
        d = rung("L1")
        self.hold_s, self.hold_n = float(d["hold_s"]), int(d["hold_samples"])
        self.nats_unexplained = float(d["evidence"]["stopped_unexplained_nats"])
        self.nats_live = float(d["evidence"]["live_contradicts_stale_nats"])
        self.state = self._read()            # lake unit -> {"stopped": since or None, "streams": n}
        self.stopped = {u: s["stopped"] for u, s in self.state.items() if s["stopped"] is not None}

    def _read(self) -> dict:
        now = self.sub.now
        out: dict = {}
        for ob in COUNTED:
            per: dict = {}
            for o in self.sub.records(ob):
                if o.seq is None:
                    continue
                if isinstance(o.seq, list) and o.period:
                    per.setdefault(o.element, []).extend(
                        (o.t_start + k * o.period, s) for k, s in enumerate(o.seq))
                elif not isinstance(o.seq, list):
                    per.setdefault(o.element, []).append((o.t_start, o.seq))
            for el, pts in per.items():
                pts.sort()
                if len(pts) < 2:
                    continue
                period = min((b[0] - a[0]) for a, b in zip(pts, pts[1:]) if b[0] > a[0]) if len(pts) > 1 else 60.0
                last_adv = pts[0][0]
                for (ta, sa), (tb, sb) in zip(pts, pts[1:]):
                    if sb != sa:
                        last_adv = tb
                hold = max(self.hold_s, self.hold_n * period)
                unit = el.split("|")[0]
                st = out.setdefault(unit, {"stopped": None, "streams": 0, "live": 0})
                st["streams"] += 1
                if now - last_adv >= hold:
                    st["stopped"] = min(st["stopped"], last_adv) if st["stopped"] is not None else last_adv
                else:
                    st["live"] += 1
        return out

    def payload(self) -> GeneratorPayload:
        insts = [i for u in sorted(self.stopped) for i in [unit_instrument(self.kg, u)] if i]
        if not insts:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        m = 0.9 / len(insts)
        return GeneratorPayload(candidates=[Candidate(hypothesis=single("TELEMETRY_STALE", i), prior_mass=m,
                                                      rationale="sample counter stopped") for i in insts],
                                residual_mass=0.1)

    def likelihood(self, hyps) -> LikelihoodPayload:
        vals = {}
        for h in hyps:
            stale_units = set()
            ll = 0.0
            for c in h.causes:
                if c.location.kind != "element":
                    continue
                if c.fault_mode == "TELEMETRY_STALE":
                    u = _unit(self.kg, c.location.element)
                    stale_units.add(u)
                    st = self.state.get(u)
                    if st is not None and st["stopped"] is None and st["live"] > 0:
                        ll -= self.nats_live
            ll -= self.nats_unexplained * len(set(self.stopped) - stale_units)
            vals[h.key()] = LikelihoodValue(log_likelihood=ll)
        return LikelihoodPayload(values=vals, background_log_likelihood=-self.nats_unexplained * len(self.stopped))


# ---------------------------------------------------------------------- L2, L3 (through the twin)

LADDER_OBS = {"L2": ("span_dgd",),           # the ladder's streams the twin compares
              "S": ("sp_power", "sp_offset", "sp_width"),
              "R": ("rx_offset", "rx_width", "rx_asym", "rx_filter_penalty")}
NO_PENALTY_MODEL = "NOPM"     # an agent-side flag: R without the learned penalty model (its stream not read)


def ladder_observables(ladder) -> tuple:
    obs = tuple(ob for r in sorted(ladder) for ob in LADDER_OBS.get(r, ()))
    return tuple(o for o in obs if not (o == "rx_filter_penalty" and NO_PENALTY_MODEL in ladder))


def ladder_twin_err() -> dict:
    """The twin's stated accuracy on the ladder's streams it compares (floor, fraction of the predicted change)."""
    out = {}
    for r in ("L2", "S", "R"):
        for ob, e in (rung(r).get("twin_error_by_stream") or {"span_dgd": rung(r).get("twin_error")}).items():
            if e:
                out[ob] = (float(e["floor"]), float(e["fraction"]))
    return out


# ---------------------------------------------------------------------- L4, L5 (documented meaning)

class _Located:
    """Shared reading of event-like observables that place a brief event: entries near the onset, each of which
    names (kind, element); a hypothesis accounts for an entry when one of its causes is the mode the entry's kind
    documents, at that element."""
    rung_id = ""
    rationale = ""

    def __init__(self, substrate, onset: float):
        self.sub, self.kg = substrate, substrate.kg
        self.doc = rung(self.rung_id)
        w = float(self.doc["window_s"])
        rows = self._read()        # lake observations near the onset, or (mode, element) readings already made
        if rows and isinstance(rows[0], tuple):
            self.entries = sorted(set(rows))
        else:
            self.entries = sorted({(self.mode_of(o), o.element) for o in rows
                                   if onset - w <= o.t_start <= substrate.now and self.mode_of(o)})

    def _read(self):
        raise NotImplementedError

    def mode_of(self, o):
        raise NotImplementedError

    def modes(self) -> set:
        raise NotImplementedError

    def payload(self) -> GeneratorPayload:
        if not self.entries:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        m = 0.9 / len(self.entries)
        return GeneratorPayload(candidates=[Candidate(hypothesis=single(mode, el), prior_mass=m,
                                                      rationale=self.rationale) for mode, el in self.entries],
                                residual_mass=0.1)

    def likelihood(self, hyps) -> LikelihoodPayload:
        ev = self.doc["evidence"]
        unexplained = float(ev.get("capture_unexplained_nats", ev.get("entry_unexplained_nats", 6.0)))
        absent = float(ev.get("no_capture_nats", ev.get("no_entry_nats", 2.3)))
        have = set(self.entries)
        vals = {}
        for h in hyps:
            mine = {(c.fault_mode, c.location.element) for c in h.causes
                    if c.location.kind == "element" and c.fault_mode in self.modes() and self.placeable(c.location.element)}
            ll = -absent * len(mine - have) - unexplained * len(have - mine)
            vals[h.key()] = LikelihoodValue(log_likelihood=ll)
        return LikelihoodPayload(values=vals, background_log_likelihood=-unexplained * len(have))

    def placeable(self, el: str) -> bool:
        return True


class LocatedEvents(_Located):
    """L4: per-span captures of brief polarisation and loss excursions."""
    name, version, rung_id, rationale = "ladder.located", "0.1", "L4", "captured on this span"
    KIND = {"sop": "SOP_TRANSIENT", "loss": "MECHANICAL_DISTURBANCE"}

    def _read(self):
        return self.sub.records("span_transient_capture")

    def mode_of(self, o):
        return self.KIND.get((o.value or {}).get("kind"))

    def modes(self):
        return set(self.KIND.values())

    def placeable(self, el):
        return self.kg.has(el) and self.kg.cls(el) == "FibreSpan"     # every span is sensed; nothing else is


class EventLogs(_Located):
    """L5: the equipment's logs of line protection switches and channel add/drop actions."""
    name, version, rung_id, rationale = "ladder.logs", "0.1", "L5", "logged by the equipment"

    def _read(self):
        return self.sub._q(source="log")

    def mode_of(self, o):
        return self.doc["explains"].get(o.observable)

    def modes(self):
        return set(self.doc["explains"].values())


class PortMonitor(_Located):
    """L3, by the readings' documented meaning: a filter centre offset that appeared since the onset is a filter
    shift at that port; a port's output power that fell while the channel arriving at that degree held steady is a
    loss in that port (an attenuation drift). The windows are the twin's (before the onset, and the last minutes)."""
    name, version, rung_id, rationale = "ladder.ports", "0.1", "L3", "read at the WSS port"

    def __init__(self, substrate, onset: float):
        from .twin import Windows
        self.win = Windows.around(onset, substrate.now)
        self.onset = onset
        super().__init__(substrate, onset)

    def _mean(self, ob, el, lo, hi):
        xs = [x.value for x in self.sub.samples(ob, el, lo, hi) if math.isfinite(x.value)]
        return sum(xs) / len(xs) if xs else None

    def _read(self):
        th = self.doc["thresholds"]
        b, a = self.win.before, self.win.after
        # "since the onset" (knowledge/ladder/telemetry.yaml, L3): only readings taken after the onset count. With no
        # onset in the record (a slow drift, whose frame puts the onset at the question time) there are none, and
        # no port can show a change since it (amendment 12: a one-sample window let noise across ~1,000 ports read
        # as a filter shift).
        a = (max(a[0], self.onset), a[1])
        if a[0] >= a[1]:
            return []
        out = []
        for el in sorted(self.sub.elements_reporting("wss_filter_offset")):
            x0, x1 = self._mean("wss_filter_offset", el, *b), self._mean("wss_filter_offset", el, *a)
            if x0 is not None and x1 is not None and abs(x1) > th["offset_ghz"] and abs(x1 - x0) > th["offset_ghz"]:
                out.append(("WSS_FILTER_SHIFT", el))
        for el in sorted(self.sub.elements_reporting("wss_port_power")):
            p0, p1 = self._mean("wss_port_power", el, *b), self._mean("wss_port_power", el, *a)
            if p0 is None or p1 is None or p1 - p0 > -th["drop_db"]:
                continue
            oms, _, slot = el.partition("|")
            dst = self.kg.g.nodes[oms].get("dst") if self.kg.has(oms) else None
            ocm = f"{dst}:{oms}|{slot}"
            i0, i1 = self._mean("ocm_channel_power", ocm, *b), self._mean("ocm_channel_power", ocm, *a)
            if i0 is not None and i1 is not None and abs(i1 - i0) <= th["steady_db"]:
                out.append(("WSS_ATTEN_DRIFT", el))
        return out

    def mode_of(self, o):
        return o[0]

    def modes(self):
        return {"WSS_FILTER_SHIFT", "WSS_ATTEN_DRIFT"}

    def placeable(self, el):
        return "|" in el and el.partition("|")[2].isdigit()       # a WSS channel port, "oms|slot"


# ---------------------------------------------------------------------- L6 (a check read by its documented meaning)

XCHECK = "xcheck_reading"


def xcheck_catalogue() -> dict:
    c = rung("L6")["check"]
    return {"id": c["id"], "class": c["class"], "minutes": c["minutes"]}


def xcheck_for(kg, loc: str) -> list:
    """The cross-check at the unit a location names (an instrument names the unit it is on): amplifiers and line
    receivers."""
    u = _unit(kg, loc.split("|")[0])
    if kg.has(u) and kg.cls(u) in ("Amplifier", "LineInterface"):
        return [(XCHECK, {"unit": u})]
    return []


def xcheck_direct(params: dict, value: dict, loc: str, mode: str, kg=None):
    """Misfit (nats) of a cross-check answer for a contender at loc, by the check's documented meaning."""
    from .interpret import DIRECT_NATS
    agree = value.get("agree")
    if agree is None:
        return None
    u = params.get("unit")
    at = loc.split("|")[0] == u or (kg is not None and kg.has(loc) and kg.cls(loc) == "Instrument"
                                    and kg.g.nodes[loc].get("lake_element") == u)
    if at and mode == "INSTRUMENT_MISREPORT":
        return DIRECT_NATS if agree else 0.0
    if at and mode == "TELEMETRY_STALE":
        return None if agree else 0.0
    return 0.0 if agree else DIRECT_NATS


# ---------------------------------------------------------------------- manual troubleshooting

def ladder_library(lib, ladder):
    """The operator's procedure library with the ladder's procedures for the rungs switched on, ahead of the
    operator's selection order."""
    d = yaml.safe_load((ROOT / "procedures.yaml").read_text())
    out = copy.deepcopy(lib)
    sel = [{k: v for k, v in s.items() if k != "rung"} for s in d["selection_order"] if s.get("rung") in ladder]
    for p in d["procedures"]:
        if p.get("rung") in ladder:
            out.procedures[p["id"]] = {"start": p["start"], "title": p["title"], "steps": copy.deepcopy(p["steps"]),
                                       "leaves": copy.deepcopy(p["leaves"])}
    out.selection = sel + out.selection
    return out


class LadderGrounding(GroundingTool):
    """The grounding tool with the ladder procedures' one extra location term: the instrument of the unit an alarm
    names (faults of the monitoring are faults of an instrument, which the equipment documentation does not list)."""

    def resolve(self, where, unit, *a, **kw):
        if where == "instrument_of_this_unit":
            i = unit_instrument(self.kg, unit.split("|")[0])
            return [i] if i else []
        return super().resolve(where, unit, *a, **kw)

    def admits(self, loc, mode):
        if mode in MONITORING and self.kg.has(loc) and self.kg.cls(loc) == "Instrument":
            return True
        return super().admits(loc, mode)


# ---------------------------------------------------------------------- spectral monitoring, S and R (generation)

class SpectralLocator:
    """S and R, by the readings' documented meaning (knowledge/ladder/telemetry.yaml): where a channel's shape first
    changed along its path (S) or that a received shape changed (R). Generation only; the evidence is the twin's,
    which predicts every reading under every hypothesis in spectral mode."""
    name, version = "ladder.spectral", "0.1"

    def __init__(self, substrate, onset: float, ladder):
        from .twin import Windows
        self.sub, self.kg = substrate, substrate.kg
        self.win = Windows.around(onset, substrate.now)
        self.entries: list = []
        found: set = set()
        if "S" in ladder:
            found |= self._span_ends()
        if "R" in ladder:
            found |= self._receivers()
        self.entries = sorted(found)

    def _mean(self, ob, el, lo, hi):
        xs = [x.value for x in self.sub.samples(ob, el, lo, hi) if isinstance(x.value, (int, float)) and math.isfinite(x.value)]
        return sum(xs) / len(xs) if xs else None

    def _changed(self, obs_th: dict) -> set:
        b, a = self.win.before, self.win.after
        out = set()
        for ob, th in obs_th.items():
            for el in self.sub.elements_reporting(ob):
                x0, x1 = self._mean(ob, el, *b), self._mean(ob, el, *a)
                if x0 is not None and x1 is not None and abs(x1 - x0) > th:
                    out.add((el, ob))
        return out

    def _ends(self, lp: str, d: str):
        e = self.kg.lightpath_ends(lp)
        return (e.get("a"), e.get("z")) if d == "fwd" else (e.get("z"), e.get("a"))

    def _span_ends(self) -> set:
        """Along each channel's path, the first OMS whose span-end shape changed, and changed again at the next
        OMS too (a filter's or a laser's change persists downstream; a single noisy reading does not)."""
        th = rung("S")["thresholds"]
        changed = self._changed({"sp_offset": float(th["offset_ghz"]), "sp_width": float(th["width_ghz"])})
        if not changed:
            return set()
        by_el: dict = {}
        for el, ob in changed:
            by_el.setdefault(el, set()).add(ob)
        slots = {el.rpartition("|")[2] for el in by_el}
        out = set()
        for lp in self.kg.nodes_of("OCh"):
            slot = str(self.kg.g.nodes[lp].get("slot"))
            if slot not in slots:
                continue
            for d in ("fwd", "rev"):
                path = self.kg.lightpath_oms(lp, d)
                hits = []
                for oms in path:
                    spans = [e for e in self.kg.oms_elements(oms) if self.kg.cls(e) == "FibreSpan"]
                    hits.append(by_el.get(f"{spans[0]}|{slot}", set()) if spans else set())
                for j, hit in enumerate(hits):
                    if not hit:
                        continue
                    if j + 1 < len(hits) and not (hits[j + 1] & hit):
                        continue                      # not seen again downstream: noise
                    if j == 0:
                        tx, _ = self._ends(lp, d)
                        if tx:
                            out.add(("LASER_FREQ_DRIFT", tx))
                    else:
                        port = f"{path[j - 1]}|{slot}"
                        if "sp_offset" in hit:
                            out.add(("WSS_FILTER_SHIFT", port))
                        if "sp_width" in hit:
                            out.add(("WSS_FILTER_NARROWING", port))
                    break
        return out

    def _receivers(self) -> set:
        th = rung("R")["thresholds"]
        changed = {el for el, _ in self._changed({"rx_offset": float(th["offset_ghz"]),
                                                  "rx_width": float(th["width_ghz"])})}
        out = set()
        for rx in sorted(changed):
            for lp, _ in self.kg.inn(rx, "endsAt"):
                e = self.kg.lightpath_ends(lp)
                d = "fwd" if e.get("z") == rx else "rev"
                tx, _ = self._ends(lp, d)
                if tx:
                    out.add(("LASER_FREQ_DRIFT", tx))
                slot = self.kg.g.nodes[lp].get("slot")
                for oms in self.kg.lightpath_oms(lp, d):
                    out.add(("WSS_FILTER_SHIFT", f"{oms}|{slot}"))
                    out.add(("WSS_FILTER_NARROWING", f"{oms}|{slot}"))
        return out

    def payload(self) -> GeneratorPayload:
        if not self.entries:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        m = 0.9 / len(self.entries)
        return GeneratorPayload(candidates=[Candidate(hypothesis=single(mode, el), prior_mass=m,
                                                      rationale="channel shape changed")
                                            for mode, el in self.entries], residual_mass=0.1)
