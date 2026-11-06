"""The LLM proposer: a generation line ("Reference Network, Data Sets and Knowledge Graph" §6.2, Table 4;
§9, Table 5).

The model receives the case frame in role form (each element described by its role, with its identifier kept
as a reference) and, from the knowledge graph, the equipment present in the case with the fault modes its
documentation lists, the effects those modes have, and the shared vocabulary of locations ("upstream on the
signal's path", "the far-end transmitter", ...). It proposes kinds of fault, each with a probability, a
rationale and where it lies: a location term anchored on a symptom or a service, an element every affected
service depends on, or an element named in the brief. The grounding tool turns each proposal into concrete
locations and keeps only those whose equipment documents the mode; mass on proposals that cannot be grounded
goes to the residual.

One structured call per case, no tools: the line is the model's own reasoning on what it is shown. The model
is used as supplied. The code names no fault mode: the modes offered are read from the graph for the equipment
in the case.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Literal, Optional

from pydantic import BaseModel, create_model

from ..schemas import Candidate, GeneratorPayload, ScenarioFrame
from .frame import describe, describe_service
from .grounding import GroundingTool, graph_element
from .walks import single

PROMPT_VERSION = "proposer-0.5"     # 0.2: "shared" names which element; 0.3: the question's service marked;
#                                     0.4: a site may be named for its ROADM or switch; 0.5: symptoms labelled O1,
#                                     O2, ... (S1, S2, ... collided with site names S01 to S24 when written S15)
MAX_SYMPTOMS = 150
MAX_QUIET = 150

SYSTEM = """You are the hypothesis proposer of a diagnosis agent for an optical transport network carrying OTN \
services (ROADMs, amplified fibre lines, coherent line interfaces, OTN switches, client services with optional \
SNCP protection). You are given a case: the question, the symptoms found, the services affected and their \
neighbours, the instruments that saw no change, and the equipment involved with the fault modes its documentation \
lists.

Propose the faults that could explain the case. For each, give:
- fault_mode: one of the documented fault modes listed for the equipment in the case;
- where it lies, in one of three ways:
  - location_kind "term": a location term from the list, relative to a symptom (anchor_symptom, e.g. "O4") or to a \
service (anchor_service, e.g. "V2");
  - location_kind "shared": one of the elements every affected service's working path depends on, given in element by its reference (e.g. "E3");
  - location_kind "element": an element named in the brief, given in element by its identifier exactly as written; \
for a node's own equipment (its ROADM or OTN switch), give the site name (e.g. "S19");
  set the fields that do not apply to null;
- probability: your probability that this is the cause, as a number between 0 and 1;
- rationale: one or two sentences on why, including what argues against it.
Also give probability_other: your probability that the cause is none of your proposals. Your probabilities and \
probability_other should sum to at most 1.

Reason from what was seen and from what was not: an instrument that should have seen an effect and saw no change \
is evidence against a fault that would have caused it. Prefer the location that explains the most symptoms. \
Include less likely alternatives when they remain possible, with lower probabilities. Do not invent elements, \
fault modes or location terms."""


def _strip(el: str) -> str:
    return graph_element(el).split("|")[0]


class LLMProposer:
    name, version = "llm_proposer", PROMPT_VERSION

    def __init__(self, client, kg, grounding: GroundingTool, procedures_from_graph: bool = False):
        """procedures_from_graph: in the graph arm of the procedures comparison, the brief also gives the
        operator's procedures for the alarms in the case, read from the graph with its general queries."""
        self.client, self.kg, self.grounding = client, kg, grounding
        self.procedures_from_graph = procedures_from_graph
        self.trace: dict = {}

    def procedures_text(self, frame: ScenarioFrame) -> str:
        """The procedures the graph links to the alarms in the case (and, with no alarm, to degraded signals)."""
        kg = self.kg
        alarms = {s.observable for s in frame.symptoms if s.kind == "alarm"}
        procs = []
        for a in sorted(alarms):
            if kg.has(a):
                procs += [v for v, _ in kg.out(a, "clearedBy")]
        if not procs:
            for t in sorted(kg.nodes_of("Trigger")):
                procs += [v for v, _ in kg.out(t, "clearedBy")]
        seen, queue, lines = set(), list(dict.fromkeys(procs)), []
        while queue:
            pid = queue.pop(0)
            if pid in seen or not kg.has(pid):
                continue
            seen.add(pid)
            lines.append(f"{pid[5:]}: {kg.g.nodes[pid].get('title', '')}")
            nodes = sorted(x for x in kg.g.nodes if x.startswith(pid + "."))
            for n in nodes:
                local = n[len(pid) + 1:]
                if kg.cls(n) == "ProcedureStep":
                    outs = ", ".join(f"{d['outcome']} -> {v[len(pid) + 1:]}" for v, d in kg.out(n, "onOutcome"))
                    lines.append(f"  {local}: {kg.g.nodes[n].get('text', '')} [{outs}]")
                elif kg.cls(n) == "ProcedureLeaf":
                    causes = sorted(kg.out(n, "concludes"), key=lambda x: x[1]["order"])
                    nxt = [v for v, _ in kg.out(n, "continuesWith")]
                    if causes:
                        lines.append(f"  {local}: cause " + "; ".join(f"{m} at {d['where']}" for m, d in causes))
                    elif nxt:
                        lines.append(f"  {local}: continue with {nxt[0][5:]}")
                        queue.append(nxt[0])
                    elif kg.g.nodes[n].get("goto_by_alarm"):
                        lines.append(f"  {local}: continue with the procedure for the alarm found")
        return "\n".join(lines)

    # ------------------------------------------------------------------ what the model is shown
    def case_equipment(self, frame: ScenarioFrame) -> list[str]:
        """Elements whose equipment is in the case: the elements examined, affected and shared, those that saw
        symptoms, and everything the affected services' paths depend on."""
        kg = self.kg
        els = set()
        for e in frame.scope + frame.affected_elements + frame.shared_elements + [s.element for s in frame.symptoms]:
            b = _strip(e)
            if kg.has(b):
                els.add(b)
        for sv in frame.services:
            if sv.state != "ok" and kg.has(sv.service):
                els |= {_strip(x) for x in kg.service_closure(sv.service)}
        return sorted(e for e in els if kg.has(e))

    def documentation(self, frame: ScenarioFrame) -> tuple[str, list[str]]:
        """The equipment types in the case, with their documented fault modes, as text; and the modes."""
        kg = self.kg
        by_type: dict = {}
        for el in self.case_equipment(frame):
            ty = kg.type_of(el)
            if ty:
                by_type.setdefault(ty, set()).add(kg.cls(el))
        lines, modes = [], set()
        for ty in sorted(by_type):
            a = kg.g.nodes[ty]
            lines.append(f"{ty} ({', '.join(sorted(by_type[ty]))}{'; ' + a['role'] if a.get('role') else ''}):")
            fms = kg.out(ty, "hasFaultMode")
            if not fms:
                lines.append("  no fault modes documented")
            for m, d in sorted(fms, key=lambda x: x[0]):
                eff = ", ".join(f"{e['effect']} ({e.get('scope', 'all_channels')})" for e in d.get("effects", []))
                lines.append(f"  - {m} [{d.get('nature') or 'unspecified'}]: {d.get('description') or ''}"
                             + (f" Effects: {eff}." if eff else ""))
                modes.add(m)
        effects = sorted({e["effect"] for ty in by_type for _, d in kg.out(ty, "hasFaultMode") for e in d.get("effects", [])})
        if effects:
            lines += ["", "Effects named above:"]
            lines += [f"  - {e}: {kg.g.nodes.get(f'effect.{e}', {}).get('meaning', '')}" for e in effects]
        return "\n".join(lines), sorted(modes)

    def brief(self, frame: ScenarioFrame) -> tuple[str, dict]:
        """The case in role form, with symptoms, services and shared elements numbered for reference."""
        kg = self.kg
        refs = {"S": {}, "V": {}, "E": {}}
        L = [f"Question: {frame.question}",
             f"Estimated onset: t={frame.onset_estimate:.0f} s; question asked at t={frame.window[1]:.0f} s.", ""]
        L.append("Services (V):")
        for i, s in enumerate(frame.services, 1):
            refs["V"][f"V{i}"] = s.service
            prot = ", traffic on protection" if s.on_protection else ""
            if s.service in frame.anchors:
                state = "the service in the question" + ("; no symptom found in the data" if s.state == "ok"
                                                         else f"; {s.state}")
            else:
                state = s.state
            L.append(f"  V{i} [{state}{prot}] {describe_service(kg, s.service)}")
        if not frame.services:
            L.append("  none identified")
        L += ["", "Symptoms (O, for observations): time, kind, what, detail, where (role):"]
        order = {"alarm": 0, "event": 1, "change": 2, "pm": 3}
        syms = sorted(frame.symptoms, key=lambda x: (order[x.kind], x.t, x.observable, x.element))
        for i, s in enumerate(syms[:MAX_SYMPTOMS], 1):
            refs["S"][f"O{i}"] = s.element
            L.append(f"  O{i} t={s.t:.1f} {s.kind} {s.observable} [{s.detail}] at {s.element}: {describe(kg, s.element)}")
        if len(syms) > MAX_SYMPTOMS:
            L.append(f"  ... and {len(syms) - MAX_SYMPTOMS} more of the same kinds")
        if not syms:
            L.append("  none found")
        if frame.shared_elements:
            L += ["", "Elements every affected service's working path depends on (E):"]
            for i, e in enumerate(frame.shared_elements, 1):
                refs["E"][f"E{i}"] = e
                L.append(f"  E{i} {e}: {describe(kg, e)}")
        quiet: dict = {}                                   # element -> observables that saw no change
        for inst in frame.quiet_instruments:
            a = kg.g.nodes.get(inst, {})
            le = a.get("lake_element")
            if le:
                quiet.setdefault(le, []).extend(o for o in a.get("observes", []) if o not in quiet.get(le, []))
        L += ["", f"Instruments in scope that saw no change ({len(frame.quiet_instruments)} on {len(quiet)} elements), "
                  "by element:"]
        for le, obs in list(quiet.items())[:MAX_QUIET]:
            L.append(f"  - {le} ({describe(kg, le)}): {', '.join(obs)}")
        if len(quiet) > MAX_QUIET:
            L.append(f"  ... and {len(quiet) - MAX_QUIET} more elements")
        if frame.silent_streams:
            L.append("Recorded instruments that reported nothing: " + ", ".join(frame.silent_streams[:20]))
        if frame.unknown_sources:
            L.append(f"Streams from sources not in the inventory: {len(frame.unknown_sources)}")
        if frame.maintenance:
            L.append("Maintenance in the last 7 days at sites in scope: " +
                     "; ".join(f"{m['site']} (day {m['t_day']:.1f}, {m.get('duration_h')} h)" for m in frame.maintenance))
        return "\n".join(L), refs

    def schema(self, modes: list[str], terms: list[str]):
        Mode = Literal[tuple(modes)] if modes else str
        Term = Literal[tuple(terms)]
        P = create_model("Proposal", fault_mode=(Mode, ...),
                         location_kind=(Literal["term", "shared", "element"], ...),
                         term=(Optional[Term], ...), anchor_symptom=(Optional[str], ...),
                         anchor_service=(Optional[str], ...), element=(Optional[str], ...),
                         probability=(float, ...), rationale=(str, ...))
        return create_model("Proposals", assessment=(str, ...), proposals=(list[P], ...),
                            probability_other=(float, ...))

    def prompt(self, frame: ScenarioFrame):
        doc, modes = self.documentation(frame)
        terms = self.grounding.terms()
        text, refs = self.brief(frame)
        if self.procedures_from_graph:
            pt = self.procedures_text(frame)
            if pt:
                text += ("\n\nThe operator's troubleshooting procedures for these alarms (a guide, not a verdict; "
                         "checks marked as tests need a field visit):\n" + pt)
        user = (text + "\n\nEquipment in the case and its documented fault modes:\n" + doc +
                "\n\nLocation terms (relative to the anchor; 'upstream' is against the direction of the signal "
                "arriving at it):\n" + "\n".join(f"  - {k}: {v}" for k, v in terms.items()))
        return user, refs, modes, list(terms)

    # ------------------------------------------------------------------ run
    def run(self, frame: ScenarioFrame) -> GeneratorPayload:
        user, refs, modes, terms = self.prompt(frame)
        if not modes:
            self.trace = {"error": "no documented fault modes in the case"}
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        out = self.client.parse("proposer", PROMPT_VERSION, SYSTEM, user, self.schema(modes, terms))
        return self.ground(frame, out, refs)

    def ground(self, frame: ScenarioFrame, out, refs: dict) -> GeneratorPayload:
        props = [p.model_dump() if hasattr(p, "model_dump") else dict(p) for p in out.proposals]
        total = sum(max(0.0, min(1.0, p["probability"])) for p in props) + max(0.0, min(1.0, out.probability_other))
        scale = 1.0 / total if total > 1.0 else 1.0
        grounded: dict = defaultdict(float)
        why: dict = {}
        residual = max(0.0, min(1.0, out.probability_other)) * scale
        log = []
        for p in props:
            pr = max(0.0, min(1.0, p["probability"])) * scale
            wlocs, note = self._locate(frame, p, refs)
            locs = [loc for loc, _ in wlocs]
            entry = {**p, "grounded": locs[:40], "n_grounded": len(locs), "note": note}
            if any(w < 1.0 for _, w in wlocs):
                entry["undocumented"] = [loc for loc, w in wlocs if w < 1.0][:40]
            log.append(entry)
            if not wlocs:
                residual += pr
                continue
            for loc, w in wlocs:                  # a location kept only by its class carries reduced weight
                grounded[(p["fault_mode"], loc)] += pr * w / len(wlocs)
                residual += pr * (1.0 - w) / len(wlocs)
                why.setdefault((p["fault_mode"], loc), p["rationale"])
        if total < 1.0:
            residual += 1.0 - total
        self.trace = {"assessment": out.assessment, "proposals": log, "probability_other": out.probability_other,
                      "sum_stated": round(total, 4)}
        rows = sorted(grounded.items(), key=lambda x: (-x[1], x[0]))
        residual = min(1.0, residual)
        cands = [Candidate(hypothesis=single(m, loc), prior_mass=w, rationale=f"llm: {why[(m, loc)][:300]}")
                 for (m, loc), w in rows]
        tot = sum(c.prior_mass for c in cands) + residual
        if tot > 1.0:                                  # rounding
            for c in cands:
                c.prior_mass /= tot
            residual /= tot
        return GeneratorPayload(candidates=cands, residual_mass=residual)

    def _locate(self, frame: ScenarioFrame, p: dict, refs: dict) -> tuple[list, str]:
        g, mode = self.grounding, p["fault_mode"]
        k = p["location_kind"]
        el = (p.get("element") or "").strip()
        # a site named where an element is asked for is the site, even where a symptom label could read the same
        # (labels were S1, S2, ... before prompt 0.5 and collided with site names); otherwise a reference (E3, O4)
        # stands for the element it refers to
        if not (k == "element" and self.kg.has(f"site.{el}") and not self.kg.has(_strip(el))):
            el = refs["E"].get(el) or refs["S"].get(el) or el
        if k == "shared" and not el:
            locs = g.ground_weighted(frame, mode, relation=("shared",))
            return locs, ("no element reference; spread over every shared element" if locs
                          else "no shared element documents the mode")
        if k == "element" and not self.kg.has(_strip(el)) and self.kg.has(f"site.{el}"):
            # a site named where an element is asked for: the site's own equipment (ROADM, OTN switch) that
            # documents the mode; the brief names sites but not always their ROADMs and switches
            locs = [x for unit in (f"roadm.{el}", f"otn.{el}") if self.kg.has(unit)
                    for x in g.ground_weighted(frame, mode, element=unit)]
            return locs, ("site named; resolved to its node equipment" if locs
                          else "site named; its node equipment does not document the mode")
        if k in ("element", "shared"):
            if not self.kg.has(_strip(el)):
                return [], f"unknown element {p.get('element')!r}"
            locs = g.ground_weighted(frame, mode, element=el)
            return locs, "" if locs else "the element's equipment does not document the mode"
        term = p.get("term")
        anchor = refs["S"].get((p.get("anchor_symptom") or "").strip())
        svc = refs["V"].get((p.get("anchor_service") or "").strip())
        if not term:
            return [], "no location term"
        if not anchor and not svc:
            return [], f"unknown anchor {p.get('anchor_symptom') or p.get('anchor_service')!r}"
        locs = g.ground_weighted(frame, mode, relation=("where", term, anchor, svc))
        return locs, "" if locs else "nothing at that location documents the mode"
