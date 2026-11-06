"""Case frame builders ("Reference Network, Data Sets and Knowledge Graph" §6.1).

The frame states what is known about a case before anyone hypothesises: the question and its anchors,
the onset, the symptoms with their evidence, the services affected and the unaffected neighbours, the
elements the affected services share, the instruments that saw nothing, silent or unknown streams, and
recent maintenance. Every generation line starts from it.

`DeterministicFramer` builds it with fixed queries against the graph and the lake and with generic change
detection (src/adagent/substrate/changes.py). It contains no knowledge of optics or of any fault mode:
which elements matter comes from the graph's structure, and what changed comes from the data's own
statistics. `describe()` and `role_text()` give the role form used by analogy search and the LLM.
"""
from __future__ import annotations

import math
from typing import Optional

from ..schemas import FrameService, FrameSymptom, ScenarioFrame
from ..substrate.access import Substrate
from ..substrate.changes import detect, detect_onset

STATE_ORDER = {"down": 0, "hit": 1, "errored": 2, "degraded": 3, "ok": 4}


def _graph_element(kg, el: str) -> Optional[str]:
    if kg.has(el):
        return el
    if ":" in el:                                    # channel monitor "site:oms|slot"
        oms = el.split(":", 1)[1].split("|")[0]
        return oms if kg.has(oms) else None
    return None


NETWORK_CLASSES = {"ClientPort", "ODUPath", "LineInterface", "Amplifier", "ClientService", "FibreSpan", "OCh",
                   "ProtectionGroup", "OMS", "ROADM", "OTNSwitch"}     # elements of the network (a wider frame's scope)


class DeterministicFramer:
    name = "deterministic"

    def __init__(self, sub: Substrate, max_scope: int = 800, max_neighbours: int = 30, widen: int = 1):
        """widen > 1 builds a wider frame for a return to generation: the scope, the signal paths followed from each
        element and the elements whose streams set the onset grow by that factor, and the graph neighbours of every
        affected element join the scope."""
        self.sub, self.kg = sub, sub.kg
        self.widen = max(1, int(widen))
        self.max_scope, self.max_neighbours = max_scope * self.widen, max_neighbours * self.widen

    def _log(self, observable: str) -> bool:
        from ..substrate.changes import LOG_UNITS
        return self.kg.g.nodes.get(f"obs.{observable}", {}).get("unit") in LOG_UNITS

    # ------------------------------------------------------------------ scope
    def _anchor_scope(self, anchor: str) -> list[str]:
        kg = self.kg
        if kg.cls(anchor) == "ClientService":
            return kg.service_closure(anchor, "both")
        out = [anchor]
        for lp, d in kg.lightpaths_through(anchor)[:20 * self.widen]:
            out += kg.signal_path(lp, d)
        for svc in kg.services_through(anchor)[:10 * self.widen]:
            out += kg.service_closure(svc, "working")
        return list(dict.fromkeys(out))

    # ------------------------------------------------------------------ build
    def build(self, question) -> ScenarioFrame:
        sub, kg = self.sub, self.kg
        sub.reader = "frame:deterministic"
        rep = float(question.reported_at_s)
        t0 = max(0.0, rep - 900.0)
        alarms = sub.alarms(t0=rep - 600.0)
        events = sub.events("PROT_SWITCH", t0=rep - 600.0)
        scope = self._anchor_scope(question.anchor)

        # onset: the earliest of the alarms near the report and the first departure of the anchor's streams
        t_alarm = min((o.t_start for o in alarms), default=None)
        streams = [(ob, le, self._log(ob)) for el in scope[:200 * self.widen] for inst, ob, le in sub.expected_streams(el)
                   if not ob.endswith("_pm15") and not kg.g.nodes[inst].get("per_channel")]
        t_change = detect_onset(sub, streams[:400 * self.widen], rep)
        cands = [t for t in (t_alarm, t_change) if t is not None]
        onset = min(cands) if cands else rep

        symptoms: list[FrameSymptom] = []
        affected: dict[str, None] = {}
        for o in alarms:
            symptoms.append(FrameSymptom(kind="alarm", observable=o.observable, element=o.element, t=o.t_start,
                                         detail=str(o.value), evidence=[o.id]))
            g = _graph_element(kg, o.element)
            if g:
                affected[g] = None
        for o in events:
            symptoms.append(FrameSymptom(kind="event", observable=o.observable, element=o.element, t=o.t_start,
                                         detail=str(o.value), evidence=[o.id]))
            affected[o.element] = None

        # widen the scope to what the alarms touch: their elements and the signal paths through them
        for el in list(affected):
            scope.append(el)
            for lp, d in kg.lightpaths_through(el)[:6 * self.widen]:
                scope += kg.signal_path(lp, d)
            if self.widen > 1 and kg.has(el):
                nb = [n for n in list(kg.g.successors(el)) + list(kg.g.predecessors(el)) if kg.cls(n) in NETWORK_CLASSES]
                scope += nb[:self.max_neighbours]
        scope = list(dict.fromkeys(e for e in scope if kg.has(e)))[: self.max_scope]
        slots = {kg.g.nodes[lp].get("slot") for lp in scope if kg.cls(lp) == "OCh"}
        for el in scope:
            if kg.cls(el) == "LineInterface":
                for lp, _ in kg.inn(el, "endsAt"):
                    slots.add(kg.g.nodes[lp].get("slot"))

        # changes in readings on every recorded instrument in scope
        quiet = []
        quiet_streams = []                # "observable@lake element" examined and unchanged (per channel for monitors)
        examined: set = set()             # an instrument can be recorded on two elements (a ROADM and its line)
        for el in scope:
            for inst, ob, le in sub.expected_streams(el):
                if ob.endswith("_pm15") or (inst, ob) in examined:
                    continue
                examined.add((inst, ob))
                lake_els = [le]
                if kg.g.nodes[inst].get("per_channel"):
                    lake_els = [f"{le}|{s}" for s in sorted(slots) if s is not None]
                changed = False
                for lel in lake_els:
                    s = [(x.t, x.value, x.evidence_id) for x in sub.samples(ob, lel, 0.0, sub.now)]
                    ch = detect(s, ob, lel, onset, log_scale=self._log(ob))
                    if not ch:
                        quiet_streams.append(f"{ob}@{lel}")
                    if ch:
                        changed = True
                        detail = ch.kind if ch.delta is None else f"{ch.kind} {ch.delta:+.3g}"
                        symptoms.append(FrameSymptom(kind="change", observable=ob, element=lel, t=ch.t, detail=detail,
                                                     magnitude=ch.delta, evidence=ch.evidence))
                        affected[el] = None
                if not changed and inst not in quiet:
                    quiet.append(inst)

        # performance monitoring: errored seconds appearing after the onset
        for ob in ("errored_seconds_pm15", "client_errored_seconds_pm15"):
            for el in sorted(sub.elements_reporting(ob)):
                recs = sub.records(ob, el)
                prior = [r for r in recs if r.t_end is not None and r.t_end <= onset]
                after = [r for r in recs if r.t_end is not None and r.t_end > onset and (r.value or 0) > 0]
                if after and not any((r.value or 0) > 0 for r in prior):
                    r = after[0]
                    symptoms.append(FrameSymptom(kind="pm", observable=ob, element=el, t=r.t_start,
                                                 detail=f"ES {r.value}", magnitude=float(r.value), evidence=[r.id]))
                    if kg.has(el):
                        affected[el] = None

        services = self._services(question.anchor, symptoms, scope)
        hurt = [s.service for s in services if s.state in ("down", "hit", "errored")]
        shared = kg.common_elements(hurt, "working") if len(hurt) >= 2 else []
        silent = list(dict.fromkeys(f"{i}:{o}" for el in scope for i, o in sub.silent_streams(el, onset - 300, sub.now)))
        unknown = [f"{o}@{e}" for o, e in sub.unknown_sources(["amp_input_power", "rx_power", "est_osnr"])][:50]
        sites = {kg.g.nodes[e].get("site") or kg.g.nodes[e].get("src") for e in scope} - {None}
        maint = [m for m in sub.maintenance(since_day=(sub.day or 0) - 7) if m["site"] in sites]
        return ScenarioFrame(builder=self.name, question=question.text, question_kind=question.kind,
                             anchors=[question.anchor], reported_at=rep, onset_estimate=round(onset, 3),
                             window=[t0, sub.now], symptoms=sorted(symptoms, key=lambda s: (s.t, s.kind, s.element)),
                             services=services, affected_elements=list(affected), shared_elements=shared,
                             quiet_instruments=quiet, silent_streams=silent, unknown_sources=unknown,
                             maintenance=maint, scope=scope, quiet_streams=quiet_streams)

    # ------------------------------------------------------------------ services
    def _services(self, anchor: str, symptoms, scope) -> list[FrameService]:
        kg = self.kg
        state: dict[str, str] = {}
        prot: dict[str, bool] = {}
        ev: dict[str, list] = {}

        def mark(svc, st, e):
            if svc is None:
                return
            if svc not in state or STATE_ORDER[st] < STATE_ORDER[state[svc]]:
                state[svc] = st
            ev.setdefault(svc, []).extend(e)

        for s in symptoms:
            if s.element.startswith("cp."):
                svc = s.element.split(".")[1]
                if s.observable == "CLIENT_SF":
                    mark(svc, "down", s.evidence)
                elif s.observable in ("PROT_ACTIVE", "PROT_SWITCH"):
                    prot[svc] = True
                    mark(svc, "hit", s.evidence)
                elif s.observable == "client_errored_seconds_pm15":
                    mark(svc, "errored", s.evidence)
                elif s.observable == "PROT_UNAVAIL":
                    mark(svc, "ok", s.evidence)
            elif s.kind in ("change", "alarm", "pm") and kg.cls(s.element) == "LineInterface":
                for lp, _ in kg.inn(s.element, "endsAt"):
                    for p in kg.paths_using_lightpath(lp):
                        mark(kg.service_of_path(p), "degraded", s.evidence)
        neighbours = []
        if kg.cls(anchor) == "ClientService":
            mark(anchor, "ok", [])
            w = kg.service_paths(anchor)["working"]
            for h in (kg.odu_hops(w) if w else []):
                neighbours += [kg.service_of_path(p) for p in kg.paths_using_lightpath(h["lightpath"])]
        n_added = 0
        for svc in dict.fromkeys(neighbours):
            if svc and svc not in state and n_added < self.max_neighbours:
                state[svc] = "ok"
                n_added += 1
        return [FrameService(service=s, state=st, on_protection=prot.get(s), evidence=sorted(set(ev.get(s, []))))
                for s, st in sorted(state.items(), key=lambda x: (STATE_ORDER[x[1]], x[0]))]


def _inst_of(kg, el, lake_el):
    for i in kg.instruments_for_lake_element(lake_el):
        return i
    return ""


# ---------------------------------------------------------------------- role form

def describe(kg, el: str) -> str:
    """An element's role in words, from the graph."""
    base = el.split("|")[0]
    if ":" in base:
        site, oms = base.split(":", 1)
        a = kg.g.nodes.get(oms, {})
        return f"channel monitor at {site} on the line from {a.get('src')}"
    a = kg.g.nodes.get(base, {})
    c = a.get("cls")
    if c == "FibreSpan":
        o = kg.g.nodes.get(a["oms"], {})
        n = sum(1 for x in kg.oms_elements(a["oms"]) if kg.g.nodes[x].get("cls") == "FibreSpan")
        return (f"fibre span {a['index'] + 1} of {n} on the line {o.get('src')}->{o.get('dst')} "
                f"({a.get('length_km')} km{', aerial' if a.get('aerial') else ''})")
    if c == "Amplifier":
        o = kg.g.nodes.get(a["oms"], {})
        n = sum(1 for x in kg.oms_elements(a["oms"]) if kg.g.nodes[x].get("cls") == "Amplifier")
        return f"{a.get('role')} amplifier ({a['index'] + 1} of {n}) on the line {o.get('src')}->{o.get('dst')}"
    if c == "OMS":
        return f"OMS {a.get('src')}->{a.get('dst')} (its input patch at {a.get('src')})"
    if c == "LineInterface":
        lps = [lp for lp, _ in kg.inn(base, "endsAt")]
        return f"{a.get('kind')} line interface, {a.get('rate_g')}G, at {a.get('site')} (lightpath {', '.join(lps)})"
    if c == "ClientPort":
        return f"client port of service {a.get('service')} at {a.get('site')}"
    if c == "OTNSwitch":
        return f"OTN switch at {a.get('site')}"
    if c == "ROADM":
        return f"ROADM at {a.get('site')}"
    if c == "OCh":
        return f"lightpath {base} ({a.get('rate_g')}G, {'-'.join(a.get('nodes', []))})"
    return c or base


def describe_service(kg, svc: str) -> str:
    a = kg.g.nodes.get(svc, {})
    paths = kg.service_paths(svc)
    w = kg.odu_hops(paths["working"]) if paths["working"] else []
    via = [w[0]["a"]] + [h["z"] for h in w] if w else []
    return (f"{svc}: {a.get('rate')} {a.get('src')}-{a.get('dst')}, protection {a.get('protection')}, "
            f"working path via {'-'.join(via)} on {len(w)} lightpath(s)")


def role_text(frame: ScenarioFrame, kg, max_symptoms: int = 120) -> str:
    """The role form: the frame in words, with each element's role, for the LLM and analogy search."""
    lines = [f"Question: {frame.question}", f"Estimated onset: t={frame.onset_estimate:.0f} s; question asked at "
             f"t={frame.window[1]:.0f} s.", "", "Services:"]
    for s in frame.services[:40]:
        prot = " (traffic on protection)" if s.on_protection else ""
        lines.append(f"- {s.state}{prot}: {describe_service(kg, s.service)}")
    lines += ["", "Symptoms (time, what, where):"]
    by_kind = {"alarm": 0, "event": 1, "change": 2, "pm": 3}
    for s in sorted(frame.symptoms, key=lambda x: (by_kind[x.kind], x.t))[:max_symptoms]:
        lines.append(f"- t={s.t:.1f} {s.kind} {s.observable} [{s.detail}] at {s.element}: {describe(kg, s.element)}")
    if len(frame.symptoms) > max_symptoms:
        lines.append(f"- ... and {len(frame.symptoms) - max_symptoms} more")
    if frame.shared_elements:
        lines += ["", "Elements every affected service's working path depends on:"]
        lines += [f"- {e}: {describe(kg, e)}" for e in frame.shared_elements[:40]]
    lines += ["", f"Instruments in scope that saw no change: {len(frame.quiet_instruments)}"]
    if frame.silent_streams:
        lines.append(f"Recorded instruments that reported nothing: {', '.join(frame.silent_streams[:20])}")
    if frame.unknown_sources:
        lines.append(f"Streams from sources not in the records: {len(frame.unknown_sources)}")
    if frame.maintenance:
        lines.append("Maintenance in the last 7 days at sites in scope: " +
                     "; ".join(f"{m['site']} (day {m['t_day']:.1f}, {m['duration_h']} h)" for m in frame.maintenance))
    return "\n".join(lines)
