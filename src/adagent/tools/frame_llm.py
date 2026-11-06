"""The LLM framer: the framing role of "Diagnosis Agent Framework Specification" v0.1 §8, carried out by
an external LLM that queries the substrate through tools ("Reference Network, Data Sets and Knowledge
Graph" §6.1).

The model receives the question and a short overview, decides what to look at, calls query tools over the
graph and the lake, and returns the frame in the same schema as the deterministic framer, so the two can be
compared field by field. The tools expose the substrate's general queries and the generic change detector;
they carry no knowledge of faults. The model is told not to hypothesise causes: framing comes before
generation.
"""
from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel

from ..schemas import FrameService, FrameSymptom, ScenarioFrame
from ..substrate.access import Substrate
from ..substrate.changes import LOG_UNITS, detect
from .frame import describe, describe_service

PROMPT_VERSION = "framer-0.3"
MAX_TURNS = 24
MAX_LIST = 60

SYSTEM = """You are the framing role of a diagnosis agent for an optical transport network carrying OTN services \
(ROADMs, amplified fibre lines, coherent line interfaces, OTN switches, client services with optional SNCP protection).
Your job is to build the case frame for a question, before anyone proposes causes. Establish, from the data:
- the onset time of the problem;
- the symptoms: alarms, protection switches, changes in readings, new errored seconds; with their evidence ids;
- which services are affected (down, hit by a protection switch, errored, degraded) and which neighbouring services are not;
- the elements the affected services' working paths have in common, when several services are affected;
- instruments in scope that saw no change, recorded instruments that reported nothing, and data from unknown sources;
- recent maintenance at sites in scope.
Use the tools to look; do not guess values you have not seen. Do not propose or rank causes. When you have enough, \
stop calling tools and answer with the frame.
Field definitions: onset_estimate is the earliest time at which the data (alarms, events, reading changes, errored \
seconds) show the problem; the reported time is only a customer's or operator's approximation. scope lists every \
element you examined. services has one entry per service."""

TOOLS = [
    {"type": "function", "function": {"name": "list_alarms", "description": "Alarms raised in a time window (alarmed severities only), oldest first. Optional filters: alarm type names (for example CLIENT_SF) and severities (critical, major, minor).",
        "parameters": {"type": "object", "properties": {"t_from": {"type": "number"}, "t_to": {"type": "number"},
                       "types": {"type": "array", "items": {"type": "string"}, "description": "alarm type names"},
                       "severities": {"type": "array", "items": {"type": "string", "enum": ["critical", "major", "minor"]}}},
                       "required": ["t_from", "t_to"]}}},
    {"type": "function", "function": {"name": "list_protection_switches", "description": "Protection-switch events in a time window.",
        "parameters": {"type": "object", "properties": {"t_from": {"type": "number"}, "t_to": {"type": "number"}}, "required": ["t_from", "t_to"]}}},
    {"type": "function", "function": {"name": "new_errored_seconds", "description": "Line receivers and client ports whose 15-minute errored-second counts became non-zero after a time.",
        "parameters": {"type": "object", "properties": {"after": {"type": "number"}}, "required": ["after"]}}},
    {"type": "function", "function": {"name": "service_info", "description": "A service's endpoints, rate, protection and the lightpaths of its working and protection paths.",
        "parameters": {"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]}}},
    {"type": "function", "function": {"name": "describe", "description": "The role of one or more elements in words (class, position, sites).",
        "parameters": {"type": "object", "properties": {"elements": {"type": "array", "items": {"type": "string"}}}, "required": ["elements"]}}},
    {"type": "function", "function": {"name": "signal_path", "description": "Elements a lightpath's signal passes in one direction, in order.",
        "parameters": {"type": "object", "properties": {"lightpath": {"type": "string"}, "direction": {"type": "string", "enum": ["fwd", "rev"]}}, "required": ["lightpath", "direction"]}}},
    {"type": "function", "function": {"name": "services_through", "description": "Services whose working or protection path passes an element.",
        "parameters": {"type": "object", "properties": {"element": {"type": "string"}}, "required": ["element"]}}},
    {"type": "function", "function": {"name": "common_elements", "description": "Elements every listed service's working path depends on.",
        "parameters": {"type": "object", "properties": {"services": {"type": "array", "items": {"type": "string"}}}, "required": ["services"]}}},
    {"type": "function", "function": {"name": "reading_changes", "description": "For the listed elements, which of their recorded instruments' readings changed at a given time (level shift, excursion, lost) and which saw no change. Uses a generic statistical test.",
        "parameters": {"type": "object", "properties": {"elements": {"type": "array", "items": {"type": "string"}}, "at": {"type": "number"}}, "required": ["elements", "at"]}}},
    {"type": "function", "function": {"name": "observability", "description": "For the listed elements: recorded instruments that reported nothing; and, network-wide, how many streams come from sources not in the records.",
        "parameters": {"type": "object", "properties": {"elements": {"type": "array", "items": {"type": "string"}}}, "required": ["elements"]}}},
    {"type": "function", "function": {"name": "maintenance", "description": "Maintenance windows in the last 7 days at the listed sites.",
        "parameters": {"type": "object", "properties": {"sites": {"type": "array", "items": {"type": "string"}}}, "required": ["sites"]}}},
]


class LLMSymptom(BaseModel):          # same fields as FrameSymptom, with no defaults (strict schemas)
    kind: Literal["alarm", "event", "change", "pm"]
    observable: str
    element: str
    t: float
    detail: str
    magnitude: Optional[float]
    evidence: list[str]


class LLMService(BaseModel):
    service: str
    state: Literal["down", "hit", "errored", "degraded", "ok"]
    on_protection: Optional[bool]
    evidence: list[str]


class FrameMaintenance(BaseModel):
    site: str
    t_day: float


class LLMFrameOut(BaseModel):
    onset_estimate: float
    symptoms: list[LLMSymptom]
    services: list[LLMService]
    affected_elements: list[str]
    shared_elements: list[str]
    quiet_instruments: list[str]
    silent_streams: list[str]
    unknown_sources_count: int
    maintenance: list[FrameMaintenance]
    scope: list[str]
    notes: str


class LLMFramer:
    def __init__(self, client, sub: Substrate):
        self.client, self.sub, self.kg = client, sub, sub.kg
        self.trace: list[dict] = []
        self.examined: set[str] = set()          # every element named in a tool call (recorded, not asked)

    # ------------------------------------------------------------------ tool implementations
    def _call(self, name: str, a: dict):
        sub, kg = self.sub, self.kg
        if name == "list_alarms":
            allres = sub.alarms(t0=a["t_from"], t1=a["t_to"])
            res = allres
            if a.get("types"):
                res = [o for o in res if o.observable in a["types"]]
            if a.get("severities"):
                res = [o for o in res if o.value in a["severities"]]
            out = [{"t": round(o.t_start, 1), "alarm": o.observable, "element": o.element, "severity": o.value,
                    "evidence": o.id} for o in res]
            reply = {"count": len(out), "alarms": out[:MAX_LIST], "truncated": len(out) > MAX_LIST}
            if allres and not res:     # never let a filter silently hide alarms
                reply["note"] = (f"{len(allres)} alarms in the window, none matching the filters; types present: "
                                 f"{sorted({o.observable for o in allres})}")
            return reply
        if name == "list_protection_switches":
            res = sub.events("PROT_SWITCH", t0=a["t_from"], t1=a["t_to"])
            out = [{"t": round(o.t_start, 3), "port": o.element, "switch": o.value, "evidence": o.id} for o in res]
            return {"count": len(out), "events": out[:MAX_LIST], "truncated": len(out) > MAX_LIST}
        if name == "new_errored_seconds":
            out = []
            for ob in ("errored_seconds_pm15", "client_errored_seconds_pm15"):
                for el in sorted(sub.elements_reporting(ob)):
                    recs = sub.records(ob, el)
                    prior = [r for r in recs if r.t_end is not None and r.t_end <= a["after"]]
                    later = [r for r in recs if r.t_end is not None and r.t_end > a["after"] and (r.value or 0) > 0]
                    if later and not any((r.value or 0) > 0 for r in prior):
                        out.append({"element": el, "counter": ob, "errored_seconds": later[0].value,
                                    "bin_start": later[0].t_start, "evidence": later[0].id})
            return {"count": len(out), "items": out[:MAX_LIST], "truncated": len(out) > MAX_LIST}
        if name == "service_info":
            s = a["service"]
            if not kg.has(s):
                return {"error": f"unknown service {s}"}
            paths = kg.service_paths(s)
            return {"description": describe_service(kg, s), "ports": kg.ports_of(s),
                    "working_hops": kg.odu_hops(paths["working"]) if paths["working"] else [],
                    "protection_hops": kg.odu_hops(paths["protect"]) if paths["protect"] else []}
        if name == "describe":
            return {e: describe(kg, e) for e in a["elements"][:MAX_LIST]}
        if name == "signal_path":
            return {"path": kg.signal_path(a["lightpath"], a["direction"])}
        if name == "services_through":
            s = kg.services_through(a["element"])
            return {"count": len(s), "services": s[:MAX_LIST]}
        if name == "common_elements":
            return {"elements": kg.common_elements(a["services"][:40], "working")}
        if name == "reading_changes":
            changes, quiet = [], []
            for el in a["elements"][:120]:
                for inst, ob, le in sub.expected_streams(el):
                    if ob.endswith("_pm15") or kg.g.nodes[inst].get("per_channel"):
                        continue
                    logs = kg.g.nodes.get(f"obs.{ob}", {}).get("unit") in LOG_UNITS
                    smp = [(x.t, x.value, x.evidence_id) for x in sub.samples(ob, le, 0.0, sub.now)]
                    ch = detect(smp, ob, le, a["at"], log_scale=logs)
                    if ch:
                        changes.append({"element": le, "observable": ob, "kind": ch.kind, "t": round(ch.t, 1),
                                        "delta": None if ch.delta is None else round(ch.delta, 3),
                                        "evidence": ch.evidence})
                    else:
                        quiet.append(inst)
            return {"changes": changes[:MAX_LIST * 2], "quiet_instruments": quiet[:MAX_LIST * 2],
                    "n_changes": len(changes), "n_quiet": len(quiet)}
        if name == "observability":
            silent = [f"{i}:{o}" for el in a["elements"][:120] for i, o in sub.silent_streams(el, 0.0, sub.now)]
            unknown = sub.unknown_sources(["amp_input_power", "rx_power", "est_osnr"])
            return {"silent_streams": silent[:MAX_LIST], "unknown_source_count": len(unknown)}
        if name == "maintenance":
            ms = [m for m in sub.maintenance(since_day=(sub.day or 0) - 7) if m["site"] in a["sites"]]
            return {"windows": ms}
        return {"error": f"unknown tool {name}"}

    # ------------------------------------------------------------------ the loop
    def build(self, question) -> ScenarioFrame:
        self.sub.reader = "frame:llm"
        overview = {"question": question.text, "question_kind": question.kind, "anchor": question.anchor,
                    "reported_at_s": question.reported_at_s, "now_s": self.sub.now,
                    "alarms_since_report_minus_10min": len(self.sub.alarms(t0=question.reported_at_s - 600))}
        session = self.client.tool_session("framer", PROMPT_VERSION, SYSTEM, TOOLS)
        calls = session.start("Build the case frame.\n" + json.dumps(overview))
        for turn in range(MAX_TURNS):
            if not calls:
                break
            results = {}
            for c in calls:
                try:
                    args = json.loads(c.arguments or "{}")
                    res = self._call(c.name, args)
                except Exception as e:  # noqa: BLE001
                    args, res = {}, {"error": repr(e)[:200]}
                for key in ("elements", "element", "lightpath", "service", "services"):
                    v = args.get(key)
                    self.examined.update(v if isinstance(v, list) else ([v] if v else []))
                self.trace.append({"turn": turn, "tool": c.name, "args": args,
                                   "result_size": len(json.dumps(res, default=str))})
                results[c.id] = json.dumps(res, default=str)[:20000]
            calls = session.submit(results)
        out = session.finish("Now give the case frame.", LLMFrameOut)
        return ScenarioFrame(builder=f"llm:{getattr(self.client, 'model', 'unknown')}", question=question.text,
                             question_kind=question.kind, anchors=[question.anchor],
                             reported_at=question.reported_at_s, onset_estimate=out.onset_estimate,
                             window=[max(0.0, question.reported_at_s - 900), self.sub.now],
                             symptoms=[FrameSymptom(**x.model_dump()) for x in out.symptoms],
                             services=[FrameService(**x.model_dump()) for x in out.services], affected_elements=out.affected_elements,
                             shared_elements=out.shared_elements, quiet_instruments=out.quiet_instruments,
                             silent_streams=out.silent_streams,
                             unknown_sources=[f"{out.unknown_sources_count} streams"] if out.unknown_sources_count else [],
                             maintenance=[m.model_dump() for m in out.maintenance],
                             scope=list(dict.fromkeys(out.scope + sorted(self.examined))), notes=out.notes)
