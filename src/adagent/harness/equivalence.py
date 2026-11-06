"""Equivalence classes at reference scale (harness side; specification §9.2, design note §4.3).

For a case, the equivalence class of the truth is every single cause that some value of its free parameter makes
indistinguishable from the truth on the data the agent could have had: the same alarms raised, the same protection
switches, the same services down, the same receivers errored in each interval, and every passive reading the
deployed instruments take within two combined standard deviations in every interval. A diagnosis naming any
member has done as well as the data allow.

The harness computes it with the world simulator, never the agent's tools. Candidate alternatives are the
locations the generation lines proposed for the case (any line, so the class covers whatever a line could name)
with every fault mode the world can apply there. Incipient faults (slow ramps) are compared only with incipient
alternatives; transient alternatives take the truth's duration when the truth is transient, and their longest
documented duration otherwise.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Optional

from ..lake import Lake
from ..world.faults import CATALOGUE, Event
from ..world.physics import FEC_BER
from ..world.simulator import NOISE, WorldSim

K_SIGMA = 2.0
GRID = 12
# the capability ladder's rungs in the case's world (world/ladder.py); set by the harness while it scores a case
# (exp2.score), so that every signature sees the observables the rungs add. Empty: as before.
LADDER: frozenset = frozenset()
CONT_RUNGS = frozenset({"L2", "L3"})       # rungs that add sampled streams (compared as readings)


def _world_class(net, el: str) -> Optional[str]:
    base, _, rest = el.partition("|")
    if base in net.spans:
        return "FibreSpan"
    if base in net.amps:
        return "Amplifier"
    if base in net.oms:
        return "OMS" if rest in ("", "port") else "WSS"
    if base in net.transponders:
        return "CoherentTransponder"
    if base.startswith("cp."):
        return "ClientPort"
    if base.startswith("otn."):
        return "OTNSwitch"
    if base in net.lightpaths:
        return "OCh"
    return None


def _modes_for(net, el: str) -> list[str]:
    c = _world_class(net, el)
    if c is None:
        return []
    alias = {"OMS": {"OMS", "Port", "PatchPanel"}, "CoherentTransponder": {"CoherentTransponder", "Card"},
             "OCh": {"OCh", "ProtectionGroup"}}.get(c, {c})
    if c == "OMS":
        alias = alias | {"WSS_PORT"}
    out = []
    for m, fm in CATALOGUE.items():
        if m == "WSS_PORT_FAIL":                       # world target: the OMS whose arriving WSS port failed
            if c == "OMS":
                out.append(m)
            continue
        if fm.nature.value == "substrate":             # the instrument on an amplifier or a receiver
            if c in ("Amplifier", "CoherentTransponder") and m in ("INSTRUMENT_MISREPORT", "TELEMETRY_STALE"):
                out.append(m)
            continue
        if fm.nature.value == "context" or not fm.simulable:
            continue
        if alias & set(fm.applies_to):
            if c == "OTNSwitch" and ("|" in el) != (m == "XC_MISCONNECT"):
                continue
            out.append(m)
    return out


class Signature:
    """What the data could show for a set of causes, interval by interval."""

    def __init__(self, net, deployment, events, t_end: float):
        w = WorldSim(net, seed=0, deployment=deployment, ladder=LADDER)
        for ev in events:
            w.inject(ev)
        segs = w.segments(0.0, t_end)
        lake = Lake()
        w._alarms(segs, lake)
        w._protection_events(segs, lake)
        self.discrete = (
            frozenset((o.observable, o.element) for o in lake.query(source="alarm") if o.value != "not_alarmed"),
            frozenset(o.element for o in lake.query(source="event")),
        )
        cont: dict = {}
        per_seg = []
        for i, s in enumerate(segs):
            down = frozenset(k for k, st in s.svc.items() if not getattr(st, "up", True))
            errored = frozenset(r.trx for r in s.snap.rx.values()
                                if r.pre_fec_ber >= FEC_BER or r.rx_power_dbm == -math.inf)
            per_seg.append((down, errored))
            for aid, a in s.snap.amps.items():
                if w.has("amp_power", aid):
                    cont[(i, "amp_in", aid)] = (a.input_dbm_per_ch, NOISE["power_db"])
                    cont[(i, "amp_out", aid)] = (a.output_dbm_per_ch, NOISE["power_db"])
                if w.has("health", aid):
                    cont[(i, "pump", aid)] = (a.pump_current_pct, NOISE["pump_pct"])
            for tid in net.transponders:
                if w.has("health", tid):
                    cont[(i, "laser_bias", tid)] = (s.snap.laser_bias_pct.get(tid, 100.0), NOISE["pump_pct"])
            for r in s.snap.rx.values():
                if w.has("rx_power", r.trx):
                    cont[(i, "rx", r.trx)] = (r.rx_power_dbm, NOISE["power_db"])
                if w.has("coherent", r.trx):
                    cont[(i, "osnr", r.trx)] = (r.osnr_db, NOISE["osnr_db"])
                    cont[(i, "ber", r.trx)] = (math.log10(max(r.pre_fec_ber, 1e-30)), 0.05)
                    cont[(i, "dgd", r.trx)] = (r.dgd_ps, 0.3)
                    cont[(i, "sop", r.trx)] = (r.sop_rate_rad_s, max(50.0, NOISE["sop_frac"] * r.sop_rate_rad_s))
            if LADDER:
                from ..world.ladder import _fx, cont_items
                cont.update(cont_items(w, i, s.snap, _fx(w, s)))
            for (oms, slot), (p, o) in s.snap.ocm.items():
                el = f"{net.oms[oms].dst}:{oms}"
                if w.has("ocm", el):
                    cont[(i, "ocm_p", f"{el}|{slot}")] = (p, NOISE["power_db"])
                    cont[(i, "ocm_osnr", f"{el}|{slot}")] = (o if p > -math.inf else -math.inf, NOISE["osnr_db"])
        self.discrete += (tuple(per_seg),)
        # the event-like observables of the ladder's rungs (stopped counters, span captures, log entries): discrete
        self.ladder_items = ()
        if LADDER:
            from ..world.ladder import signature_items
            self.ladder_items = signature_items(w, segs, t_end)
        # faults of the monitoring itself: a misreporting instrument's readings carry its bias from the onset; a
        # stale one keeps reporting what it read before it froze
        t0s = [s.t0 for s in segs]
        for el, bias in w.misreport.items():
            since = w.misreport_since.get(el, -math.inf)
            for k in list(cont):
                if k[2] == el and t0s[k[0]] >= since and k[1] in ("amp_in", "amp_out", "rx", "osnr"):
                    v, sg = cont[k]
                    cont[k] = (v + bias if v > -math.inf else v, sg)
        for el, since in w.stale.items():
            first_after = next((i for i, t in enumerate(t0s) if t >= since), None)
            if first_after is None or first_after == 0:
                continue
            for k in list(cont):
                if k[2] == el and k[0] >= first_after and (first_after - 1, k[1], k[2]) in cont:
                    cont[k] = cont[(first_after - 1, k[1], k[2])]
        self.cont = cont

    def mismatch(self, other: "Signature") -> float:
        """Largest normalised difference; infinite if anything discrete differs."""
        if self.discrete != other.discrete or self.ladder_items != other.ladder_items:
            return math.inf
        worst = 0.0
        for k in self.cont.keys() | other.cont.keys():
            va, sa = self.cont.get(k, (0.0, 1e-6))
            vb, sb = other.cont.get(k, (0.0, 1e-6))
            if va == vb:
                continue
            if va == -math.inf or vb == -math.inf:
                return math.inf
            worst = max(worst, abs(va - vb) / math.hypot(sa, sb))
        return worst


def _events(mode: str, targets: list, params: dict, onset: float, duration: Optional[float]) -> list[Event]:
    prof = "ramp" if CATALOGUE[mode].nature.value == "incipient" else "step"
    return [Event(mode, t, params, onset=onset, duration=duration, profile=prof) for t in targets]


def _targets(net, mode: str, el: str) -> list:
    """A cut takes both fibres of the cable, as the world's history and cases do."""
    if mode == "FIBRE_CUT" and el in net.spans:
        sp = net.spans[el]
        o = net.oms[sp.oms]
        rev = net.oms.get(f"oms.{o.dst}-{o.src}")
        if rev is not None:
            return [el, rev.spans[len(rev.spans) - 1 - sp.index].id]
    return [el]


def best_fit(net, deployment, target: Signature, mode: str, el: str, onset: float, duration: Optional[float],
             t_end: float) -> float:
    fm = CATALOGUE[mode]
    numeric = [p for p in fm.parameters if p.name not in ("position_km", "duration_s", "staleness_s")]
    dur = None
    if fm.nature.value == "transient":
        # a transient alternative lasts as long as the truth when the truth is transient; against a lasting truth
        # it is given its longest documented duration, the most lasting it can look
        dmax = next((p.high for p in fm.parameters if p.name == "duration_s"), 1.0)
        dur = duration if duration is not None else dmax
    tg = _targets(net, mode, el)
    if not numeric:
        return target.mismatch(Signature(net, deployment, _events(mode, tg, {}, onset, dur), t_end))
    p = numeric[0]
    lo, hi = p.low, p.high
    if p.prior == "log_uniform" and lo > 0:
        grid = [lo * (hi / lo) ** (i / (GRID - 1)) for i in range(GRID)]
    else:
        grid = [lo + (hi - lo) * i / (GRID - 1) for i in range(GRID)]
    f = lambda v: target.mismatch(Signature(net, deployment, _events(mode, tg, {p.name: float(v)}, onset, dur), t_end))
    vals = [f(v) for v in grid]
    i = min(range(GRID), key=lambda j: vals[j])
    if vals[i] == math.inf:
        return math.inf
    a, b = grid[max(0, i - 1)], grid[min(GRID - 1, i + 1)]
    best = vals[i]
    for _ in range(12):                          # golden-section refine on the bracket
        c1, c2 = b - 0.618 * (b - a), a + 0.618 * (b - a)
        f1, f2 = f(c1), f(c2)
        best = min(best, f1, f2)
        if f1 < f2:
            b = c2
        else:
            a = c1
    return best


def equivalence_class(case, candidate_locations: list[str]) -> dict:
    """{"members": [cause keys], "tested": n, "fits": {key: mismatch}} for the case's truth."""
    w = case.world
    net, dep = w.net, w.deployment
    f = case.truth.fault
    t_end = case.substrate.now
    target = Signature(net, dep, _events(f.mode, f.targets, f.params, f.onset_s, f.duration_s), t_end)
    members = {f"{f.mode}@{t}" for t in f.targets}
    fits = {}
    truth_incipient = CATALOGUE[f.mode].nature.value == "incipient"
    for el in dict.fromkeys(candidate_locations):
        for mode in _modes_for(net, el):
            key = f"{mode}@{el}"
            if key in members or (CATALOGUE[mode].nature.value == "incipient") != truth_incipient:
                continue
            m = best_fit(net, dep, target, mode, el, f.onset_s, f.duration_s, t_end)
            fits[key] = None if m == math.inf else round(m, 3)
            if m <= K_SIGMA:
                members.add(key)
    return {"members": sorted(members), "tested": len(fits), "fits": fits}


def equivalence_classes_v2(case, candidate_locations: list[str]) -> dict:
    """Experiment 2 cases may have several causes (coincident faults) and faults of the monitoring itself.

    Returns {"whole": members, "components": [members per cause], "fits": {...}}: "whole" lists the single
    causes indistinguishable from the entire truth (for one cause, its class as in Experiment 1); "components"
    lists, for each cause, the alternatives indistinguishable from the truth when that cause alone is replaced and
    the others are kept. A diagnosis is right if it names a member of "whole", or, for every cause, a member of
    that cause's component class.
    """
    w = case.world
    net, dep = w.net, w.deployment
    faults = getattr(case.truth, "faults", None) or [case.truth.fault]
    t_end = case.substrate.now
    evs = [_events(f.mode, f.targets, f.params, f.onset_s, f.duration_s) for f in faults]
    target = Signature(net, dep, [e for es in evs for e in es], t_end)
    locs = list(dict.fromkeys(candidate_locations))
    fits: dict = {}

    def search(fixed_events, own_keys, ref):
        return _class_search(net, dep, target, locs, fixed_events, own_keys, ref, t_end, fits)

    whole_keys = {f"{f.mode}@{t}" for f in faults for t in f.targets}
    whole = search([], whole_keys, faults[0]) if len(faults) == 1 else sorted(whole_keys)
    comps = []
    if len(faults) > 1:
        for j, f in enumerate(faults):
            others = [e for i, es in enumerate(evs) if i != j for e in es]
            comps.append(search(others, {f"{f.mode}@{t}" for t in f.targets}, f))
    else:
        comps = [whole]
    return {"whole": whole, "components": comps,
            "fits": {f"{a}|{b}": v for (a, b), v in fits.items()}, "tested": len(fits)}


def _class_search(net, dep, target, locs, fixed_events, own_keys, ref, t_end, fits: dict) -> list[str]:
    """The members of one class: own_keys plus every single cause at locs that, with fixed_events held, is
    within K_SIGMA of the target signature. Fits found are recorded in fits."""
    members = set(own_keys)
    incipient = CATALOGUE[ref.mode].nature.value == "incipient"
    for el in locs:
        for mode in _modes_for(net, el):
            key = f"{mode}@{el}"
            if key in members or (CATALOGUE[mode].nature.value == "incipient") != incipient:
                continue
            m = _fit_with(net, dep, target, fixed_events, mode, el, ref, t_end)
            fits[(tuple(sorted(own_keys)), key)] = None if m == math.inf else round(m, 3)
            if m <= K_SIGMA:
                members.add(key)
    return sorted(members)


def cable_sections(net, targets: list[str]) -> list[list[str]]:
    """Group the spans of a cut by cable section: a span and its mirror-index span on the reverse OMS are the two
    fibres of one section (the same pairing _targets and the agent's canonical() use). The span attribute
    .cable is not used, because a long shared cable (srlg.cable.*) runs over several links and so over several
    sections. Each group lists both fibres of the section, sorted, whether or not both were targets."""
    groups: dict = {}
    for t in targets:
        sec = tuple(sorted(_targets(net, "FIBRE_CUT", t)))
        groups.setdefault(sec, None)
    return [list(s) for s in groups]


def part_classes(case, candidate_locations: list[str], fault_index: int, sections: list[list[str]]) -> list[list[str]]:
    """For a fault with several cable sections (a duct cut), one class per section: the single causes that are
    indistinguishable from the truth when that section alone is replaced and every other section of the cut, and
    any other fault, is kept. Each class holds the section's own keys (both fibres)."""
    w = case.world
    net, dep = w.net, w.deployment
    faults = getattr(case.truth, "faults", None) or [case.truth.fault]
    t_end = case.substrate.now
    ref = faults[fault_index]
    allev = [e for f in faults for e in _events(f.mode, f.targets, f.params, f.onset_s, f.duration_s)]
    target = Signature(net, dep, allev, t_end)
    locs = list(dict.fromkeys(candidate_locations))
    out = []
    for sec in sections:
        fixed = [e for i, f in enumerate(faults)
                 for e in _events(f.mode, [t for t in f.targets if i != fault_index or t not in sec], f.params,
                                  f.onset_s, f.duration_s)]
        out.append(_class_search(net, dep, target, locs, fixed, {f"{ref.mode}@{s}" for s in sec}, ref, t_end, {}))
    return out


# ---------------------------------------------------------------------- a memo of fits, across arms
# Every arm scores the same cases, and a fit depends only on the case's world and the alternative; the harness
# names the world (fit_context) and the memo is kept per case on disk (directory EXP2_FIT_CACHE, if set).
def _fit_version() -> str:
    """The memo's version: the code's own label and a digest of what a fit depends on beyond the case."""
    import hashlib
    from ..world import alarms, faults, ladder, network, otn, physics, simulator
    parts = [Path(ladder.__file__).read_text(), repr(sorted((m, repr(fm)) for m, fm in CATALOGUE.items())), repr(sorted(NOISE.items())),
             repr(K_SIGMA), repr(GRID), Path(physics.__file__).read_text(), Path(simulator.__file__).read_text(), Path(faults.__file__).read_text(),
             Path(alarms.__file__).read_text(), Path(otn.__file__).read_text(), Path(network.__file__).read_text(),
             Path(__file__).read_text()]
    return "a4." + hashlib.sha256("".join(parts).encode()).hexdigest()[:12]


FIT_VERSION = None          # set on first use (_fit_version)
_CTX: Optional[str] = None
_MEMO: dict = {}
_DIRTY = False


def fit_context(ctx: Optional[str]) -> None:
    """Start (ctx = a name of the case's world) or end (None: save) the memo of fits for one case."""
    global _CTX, _MEMO, _DIRTY
    d = os.environ.get("EXP2_FIT_CACHE")
    if _CTX is not None and _DIRTY and d:
        f = Path(d) / f"{_CTX}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            old = json.loads(f.read_text()) if f.exists() else {}
        except ValueError:
            old = {}
        tmp = f.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({**old, **_MEMO}))
        os.replace(tmp, f)
    _CTX, _MEMO, _DIRTY = ctx, {}, False
    if ctx is not None and d and (Path(d) / f"{ctx}.json").exists():
        try:
            _MEMO = json.loads((Path(d) / f"{ctx}.json").read_text())
        except ValueError:
            _MEMO = {}


def _ev_sig(e) -> tuple:
    return (e.fault_mode, e.target, sorted(e.params.items()), e.onset, e.duration, e.profile)


def _fit_with(net, dep, target, fixed_events, mode, el, ref, t_end, return_params: bool = False):
    """best_fit, with other causes held fixed and the alternative timed like the cause it replaces. With
    return_params, returns (mismatch, the best parameters, duration). Memoised per case (fit_context).

    The ladder's event-like rungs (stopped counters, span captures, log entries) do not depend on the alternative's
    fitted parameter (a stale stream, a protection switch or a load change is there whatever its size; every
    transient's size lies above the capture thresholds and its duration is the truth's), so the fit is made on the
    rest of the data, memoised as without them, and the alternative is then a member only if its event-like items at
    the best fit are the truth's. This is the same as fitting with them, at a fraction of the cost."""
    global LADDER
    if getattr(target, "ladder_items", ()):
        import copy as _copy
        base_t = _copy.copy(target)
        base_t.ladder_items = ()
        saved, LADDER = LADDER, LADDER & CONT_RUNGS
        try:
            r = _fit_with(net, dep, base_t, fixed_events, mode, el, ref, t_end, return_params=True)
        finally:
            LADDER = saved
        if r[0] != math.inf and _ladder_items_at(net, dep, fixed_events, mode, el, ref, t_end, r[1], r[2]) \
                != target.ladder_items:
            r = (math.inf, {}, r[2])
        return r if return_params else r[0]
    return _fit_with_memo(net, dep, target, fixed_events, mode, el, ref, t_end, return_params)


def _ladder_items_at(net, dep, fixed_events, mode, el, ref, t_end, params, dur) -> tuple:
    from ..world.ladder import signature_items
    w = WorldSim(net, seed=0, deployment=dep, ladder=LADDER)
    for e in list(fixed_events) + _events(mode, _targets(net, mode, el), dict(params), ref.onset_s, dur):
        w.inject(e)
    return signature_items(w, w.segments(0.0, t_end), t_end)


def _fit_with_memo(net, dep, target, fixed_events, mode, el, ref, t_end, return_params: bool = False):
    global _DIRTY, FIT_VERSION
    key = None
    if _CTX is not None:
        FIT_VERSION = FIT_VERSION or _fit_version()
        key = repr((FIT_VERSION, mode, el, sorted(_ev_sig(e) for e in fixed_events),
                    (ref.mode, list(ref.targets), ref.onset_s, ref.duration_s), t_end))
        if key in _MEMO:
            m, params, dur = _MEMO[key]
            m = math.inf if m is None else m
            return (m, params, dur) if return_params else m
    r = _fit_with_raw(net, dep, target, fixed_events, mode, el, ref, t_end)
    if key is not None:
        _MEMO[key] = [None if r[0] == math.inf else r[0], r[1], r[2]]
        _DIRTY = True
    return r if return_params else r[0]


def _fit_with_raw(net, dep, target, fixed_events, mode, el, ref, t_end):
    fm = CATALOGUE[mode]
    numeric = [p for p in fm.parameters if p.name not in ("position_km", "duration_s", "staleness_s")]
    dur = None
    if fm.nature.value == "transient":
        dmax = next((p.high for p in fm.parameters if p.name == "duration_s"), 1.0)
        dur = ref.duration_s if ref.duration_s is not None else dmax
    tg = _targets(net, mode, el)

    def sig(params):
        return Signature(net, dep, list(fixed_events) + _events(mode, tg, params, ref.onset_s, dur), t_end)
    if not numeric:
        m = target.mismatch(sig({}))
        return (m, {}, dur)
    p = numeric[0]
    lo, hi = p.low, p.high
    grid = ([lo * (hi / lo) ** (i / (GRID - 1)) for i in range(GRID)] if p.prior == "log_uniform" and lo > 0
            else [lo + (hi - lo) * i / (GRID - 1) for i in range(GRID)])
    f = lambda v: target.mismatch(sig({p.name: float(v)}))  # noqa: E731
    vals = [f(v) for v in grid]
    i = min(range(GRID), key=lambda j: vals[j])
    if vals[i] == math.inf:
        return (math.inf, {}, dur)
    a, b = grid[max(0, i - 1)], grid[min(GRID - 1, i + 1)]
    best, best_v = vals[i], grid[i]
    for _ in range(12):
        c1, c2 = b - 0.618 * (b - a), a + 0.618 * (b - a)
        f1, f2 = f(c1), f(c2)
        if min(f1, f2) < best:
            best, best_v = (f1, c1) if f1 <= f2 else (f2, c2)
        if f1 < f2:
            b = c2
        else:
            a = c1
    return (best, {p.name: float(best_v)}, dur)
