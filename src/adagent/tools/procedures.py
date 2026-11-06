"""Troubleshooting procedures: a generation line ("Reference Network, Data Sets and Knowledge Graph" §6.2 Table 4
and §8).

Operator alarm-clearing procedures (knowledge/procedures/) are ordered checks whose outcomes select the next check
until a leaf names causes. The same procedures exist in two forms, one per experimental arm:

- tool arm: the procedure tool holds the documents as its own data (ProcedureLibrary.from_sources); the graph has
  no procedure structure;
- graph arm: the lifecycle publishes the documents into the graph (procedures_in_graph=True) and the library is
  read back from the graph's nodes and edges (ProcedureLibrary.from_graph), where other lines may also read them.

Both forms give the same library (tested), and one executor runs either. In generation, the executor answers the
checks it can from the monitoring data (alarms and readings, through the substrate); a field test cannot be
answered, so both of its outcomes stay open and the test is kept as advice. The candidates are the causes at the
leaves reached, resolved by the grounding tool, with equal weight, as an engineer following the procedure would
hold them. The code names no fault mode and no alarm: those come from the procedures.
"""
from __future__ import annotations

import copy
import json
from collections import defaultdict
from typing import Optional

from ..schemas import Candidate, GeneratorPayload, ScenarioFrame
from ..substrate.changes import LOG_UNITS, detect
from .grounding import GroundingTool, graph_element, is_port
from .walks import single

MAX_DEPTH = 40


# ---------------------------------------------------------------------- the library, in either form

class ProcedureLibrary:
    def __init__(self, procedures: dict, selection: list, form: str):
        self.procedures, self.selection, self.form = procedures, selection, form

    @classmethod
    def from_sources(cls, sources) -> "ProcedureLibrary":
        procs, sel = {}, []
        for doc in sources.procedures.values():
            for p in doc["procedures"]:
                procs[p["id"]] = {"start": p["start"], "title": p["title"],
                                  "steps": copy.deepcopy(p["steps"]), "leaves": copy.deepcopy(p["leaves"])}
            sel += [dict(s) for s in doc.get("selection_order", [])]
        return cls(procs, sel, "tool")

    @classmethod
    def from_graph(cls, kg) -> "ProcedureLibrary":
        procs = {}
        for pid in sorted(kg.nodes_of("Procedure")):
            name = pid[len("proc."):]
            start = kg.out(pid, "startsAt")[0][0][len(pid) + 1:]
            steps, leaves = {}, {}
            for n in sorted(x for x in kg.g.nodes if x.startswith(pid + ".")):
                local = n[len(pid) + 1:]
                c = kg.cls(n)
                if c == "ProcedureStep":
                    a = kg.g.nodes[n]
                    st = {"ask": copy.deepcopy(a["ask"]), "text": a.get("text", "")}
                    for v, d in kg.out(n, "onOutcome"):
                        st[d["outcome"]] = v[len(pid) + 1:]
                    steps[local] = st
                elif c == "ProcedureLeaf":
                    a = kg.g.nodes[n]
                    lf = {}
                    causes = sorted(kg.out(n, "concludes"), key=lambda x: x[1]["order"])
                    if causes:
                        lf["causes"] = [{"mode": m, "where": d["where"]} for m, d in causes]
                    nxt = kg.out(n, "continuesWith")
                    if nxt:
                        lf["goto"] = nxt[0][0][len("proc."):]
                    if a.get("goto_by_alarm"):
                        lf["goto_by_alarm"] = True
                    if a.get("advice"):
                        lf["advice"] = a["advice"]
                    leaves[local] = lf
            procs[name] = {"start": start, "title": kg.g.nodes[pid].get("title"), "steps": steps, "leaves": leaves}
        sel = []
        for src in kg.g.nodes:
            for v, d in kg.out(src, "clearedBy"):
                e = {"procedure": v[len("proc."):], "rank": d["rank"]}
                if kg.cls(src) == "Trigger":
                    e["degraded"] = kg.g.nodes[src]["degraded"]
                else:
                    e["alarm"] = src
                sel.append(e)
        sel = [{k: v for k, v in e.items() if k != "rank"} for e in sorted(sel, key=lambda e: e["rank"])]
        return cls(procs, sel, "graph")

    def canonical(self) -> tuple:
        """For comparing the two forms: the content without presentation fields."""
        def leaf(lf):
            return {k: v for k, v in lf.items() if k != "advice"}
        procs = {k: {"start": p["start"], "steps": {s: {o: x for o, x in st.items() if o != "text"}
                                                    for s, st in p["steps"].items()},
                     "leaves": {lid: leaf(lf) for lid, lf in p["leaves"].items()}}
                 for k, p in self.procedures.items()}
        return json.dumps(procs, sort_keys=True), json.dumps(self.selection, sort_keys=True)

    def for_alarm(self, alarm: str) -> Optional[str]:
        return next((s["procedure"] for s in self.selection if s.get("alarm") == alarm), None)


# ---------------------------------------------------------------------- the executor

class ProcedureTool:
    name, version = "procedures", "0.1"

    def __init__(self, substrate, library: ProcedureLibrary, grounding: Optional[GroundingTool] = None,
                 residual: float = 0.1):
        self.sub, self.kg, self.lib = substrate, substrate.kg, library
        self.g = grounding or GroundingTool(substrate.kg)
        self.residual = residual
        self.trace: dict = {}

    # ------------------------------------------------------------------ what the data say
    extra_alarm_sources: tuple = ()     # the capability ladder: management-system alarms ("nms_alarm") also read

    def _alarms(self, frame) -> list:
        out = [o for o in self.sub.alarms(t0=frame.window[0]) if o.value != "not_alarmed"]
        for src in self.extra_alarm_sources:
            out += sorted(self.sub._q(source=src, t0=frame.window[0]), key=lambda o: (o.t_start, o.observable, o.element))
        return out

    def _alarm_at(self, frame, types, elements) -> list:
        els = set(elements) | {e.split("|")[0] for e in elements}
        return [o for o in self._alarms(frame) if o.observable in types and o.element in els]

    def _reading(self, frame, observables, elements, changes) -> tuple[Optional[bool], list]:
        """True if any of the elements' instruments shows one of the changes in one of the observables at the
        onset; False if instruments exist and none does; None if no instrument records it there."""
        kg, sub = self.kg, self.sub
        observables = [observables] if isinstance(observables, str) else observables
        seen_any, hits = False, []
        for el in elements:
            base, _, slot = el.partition("|")
            if is_port(el):                     # a WSS input port has no reading of its own; its channels do
                continue
            for inst, ob, le in sub.expected_streams(base):
                if ob not in observables:
                    continue
                if kg.g.nodes[inst].get("per_channel"):
                    if not slot:
                        continue
                    le = f"{le}|{slot}"
                elif slot:
                    continue
                smp = [(x.t, x.value, x.evidence_id) for x in sub.samples(ob, le, 0.0, sub.now)]
                if not smp:
                    continue
                seen_any = True
                logs = kg.g.nodes.get(f"obs.{ob}", {}).get("unit") in LOG_UNITS
                ch = detect(smp, ob, le, frame.onset_estimate, log_scale=logs)
                if ch is None:
                    continue
                kind = ch.kind
                if kind == "level":
                    kind = "level_down" if (ch.delta or 0) < 0 else "level_up"
                if kind in changes:
                    hits.append(el)
        if hits:
            return True, list(dict.fromkeys(hits))
        return (False if seen_any else None), []

    def _resolve(self, where: str, unit: str) -> list[str]:
        return self.g.resolve(where, unit)

    # ------------------------------------------------------------------ the triggering alarm
    def select(self, frame) -> Optional[tuple]:
        alarms = self._alarms(frame)
        for sel in self.lib.selection:
            if "alarm" in sel:
                inst = sorted((o for o in alarms if o.observable == sel["alarm"]),
                              key=lambda o: (o.t_start, o.element))
                if inst:
                    o = inst[0]
                    return sel["procedure"], graph_element(o.element), f"{o.observable} at {o.element}"
            elif sel.get("degraded") == "receiver":
                rx = [s for s in frame.symptoms if s.kind in ("change", "pm") and self.kg.cls(s.element) == "LineInterface"
                      and s.observable in ("pre_fec_ber", "errored_seconds_pm15", "est_osnr")]
                if rx:
                    s = sorted(rx, key=lambda s: (s.t, s.element))[0]
                    return sel["procedure"], s.element, f"degraded receiver {s.element}"
            elif sel.get("degraded") == "client":
                cp = [s for s in frame.symptoms if s.kind == "pm" and s.element.startswith("cp.")]
                if cp:
                    s = sorted(cp, key=lambda s: (s.t, s.element))[0]
                    return sel["procedure"], s.element, f"errored client port {s.element}"
            elif sel.get("degraded") == "question_service":
                svc = next((a for a in frame.anchors if self.kg.cls(a) == "ClientService"), None)
                w = self.kg.service_paths(svc)["working"] if svc else None
                if w:
                    rx = [e for h in self.kg.odu_hops(w) for e in self.kg.lightpath_ends(h["lightpath"]).values()]
                    rx = list(dict.fromkeys(rx))
                    if rx:
                        return sel["procedure"], rx, f"every receiver on the working path of {svc}"
        return None

    # ------------------------------------------------------------------ running a procedure
    def _run(self, frame, pid: str, unit: str, depth: int, path: list, out: list, tests: list) -> None:
        if depth > MAX_DEPTH:
            out.append({"causes": [], "unit": unit, "path": path + ["(depth limit)"]})
            return
        p = self.lib.procedures[pid]
        self._step(frame, pid, p["start"], unit, None, depth, path + [pid], out, tests)

    def _step(self, frame, pid, node, unit, found, depth, path, out, tests) -> None:
        p = self.lib.procedures[pid]
        if node in p["leaves"]:
            lf = p["leaves"][node]
            if lf.get("goto"):
                self._run(frame, lf["goto"], unit, depth + 1, path + [node], out, tests)
            elif lf.get("goto_by_alarm"):
                if found is None:
                    out.append({"causes": [], "unit": unit, "path": path + [node, "(no alarm to follow)"]})
                    return
                el, alarm = found
                nxt = self.lib.for_alarm(alarm)
                if nxt is None:
                    out.append({"causes": [], "unit": unit, "path": path + [node, f"(no procedure for {alarm})"]})
                else:
                    self._run(frame, nxt, el, depth + 1, path + [node], out, tests)
            else:
                out.append({"causes": lf.get("causes", []), "unit": unit, "path": path + [node],
                            "advice": lf.get("advice", "")})
            return
        st = p["steps"][node]
        ask = st["ask"]
        answer, unit2, found2 = None, unit, found
        if "test" in ask:
            tests.append({"procedure": pid, "step": node, "test": ask["test"], "unit": unit})
        elif "alarm" in ask:
            els = self._resolve(ask["where"], unit)
            answer = bool(self._alarm_at(frame, ask["alarm"], els))
        elif "find" in ask:
            els = self._resolve(ask["where"], unit)
            hits = self._alarm_at(frame, ask["find"], els)
            if hits:
                order = {e: i for i, e in enumerate(els)}
                hits.sort(key=lambda o: (order.get(o.element, order.get(o.element.split("|")[0], 1e9)), o.t_start))
                o = hits[0] if ask.get("pick", "most_upstream") == "most_upstream" else hits[-1]
                answer, unit2, found2 = True, graph_element(o.element), (graph_element(o.element), o.observable)
            else:
                answer = False
        elif "reading" in ask:
            els = self._resolve(ask["where"], unit)
            answer, hits = self._reading(frame, ask["reading"], els, ask["change"])
            if answer and ask.get("move"):
                unit2 = hits[0]
        elif "role" in ask:
            a = self.kg.g.nodes.get(unit.split("|")[0], {})
            answer = (a.get("role") or a.get("kind")) == ask["role"]
        step_path = path + [f"{node}={answer}"]
        if answer is None:
            if "unknown" in st:
                self._step(frame, pid, st["unknown"], unit2, found2, depth + 1, step_path, out, tests)
            else:
                for o in ("yes", "no"):
                    self._step(frame, pid, st[o], unit2, found2, depth + 1, step_path, out, tests)
        else:
            nxt = st["yes" if answer else "no"]
            if nxt == node and unit2 == unit:          # a trace that did not move: stop looping
                nxt = st["no"]
            self._step(frame, pid, nxt, unit2, found2, depth + 1, step_path, out, tests)

    # ------------------------------------------------------------------ generator contract
    def run(self, frame: ScenarioFrame) -> GeneratorPayload:
        self.sub.reader = f"procedures:{self.lib.form}"
        trig = self.select(frame)
        if trig is None:
            self.trace = {"trigger": None}
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        pid, units, what = trig
        leaves, tests = [], []
        for unit in ([units] if isinstance(units, str) else units):
            self._run(frame, pid, unit, 0, [], leaves, tests)
        mass = defaultdict(float)
        lost = 0.0
        share = (1.0 - self.residual) / max(1, len(leaves))
        for lf in leaves:
            causes = lf["causes"]
            if not causes:
                lost += share
                continue
            for c in causes:
                locs = [x for loc in self._resolve(c["where"], lf["unit"]) for x in self.g._expand(loc, frame)]
                locs = [x for x in dict.fromkeys(locs) if self.g.admits(x, c["mode"])]
                lf.setdefault("grounded", []).append((c["mode"], c["where"], len(locs)))
                if not locs:
                    lost += share / len(causes)
                    continue
                for x in locs:
                    mass[(c["mode"], x)] += share / len(causes) / len(locs)
        self.trace = {"trigger": what, "procedure": pid, "form": self.lib.form,
                      "leaves": [{k: v for k, v in lf.items()} for lf in leaves], "tests_pending": tests}
        rows = sorted(mass.items(), key=lambda x: (-x[1], x[0]))
        return GeneratorPayload(candidates=[Candidate(hypothesis=single(m, loc), prior_mass=w,
                                                      rationale=f"procedure {pid}")
                                            for (m, loc), w in rows],
                                residual_mass=min(1.0, self.residual + lost))
