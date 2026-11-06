"""Lifecycle processes that write the knowledge graph ("Reference Network, Data Sets and Knowledge
Graph" §5.1, Table 3).

The graph's content arrives only through these processes, each triggered by a lifecycle event and
applied at the event's *recorded* time. Events never recorded (an unregistered instrument deployment)
never reach the graph. The processes read the event records (what management systems hold) and the
knowledge sources under knowledge/ (what documentation says); they never see the world simulator or
the truth of any incident.

Every write is logged with the event that caused it (KnowledgeGraph.log), which is the evolution check
of the design note's §2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .kg import KnowledgeGraph

TIER_ORDER = ["baseline", "power", "ocm", "coherent", "full"]
KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"
VERIFICATION_WEIGHT = {"repair_confirmed": 1.0, "engineer_concluded": 0.7, "agent_concluded": 0.3, "refuted": 0.0}


def _tier_ok(point_tier: str, site_tier: str) -> bool:
    return TIER_ORDER.index(point_tier) <= TIER_ORDER.index(site_tier)


def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


# ---------------------------------------------------------------------- knowledge sources

def _outcome_keys(doc: dict) -> dict:
    """YAML reads the outcome keys yes and no as booleans; give them back their names."""
    for pr in doc.get("procedures", []):
        for st in pr.get("steps", {}).values():
            for k, name in ((True, "yes"), (False, "no")):
                if k in st:
                    st[name] = st.pop(k)
    return doc


@dataclass
class Sources:
    vocabulary: dict
    standards: dict                      # name -> document
    types: dict                          # type id -> {"doc": document, "entry": type entry, "path": str}
    procedures: dict = field(default_factory=dict)   # operator procedure documents, by file stem
    records: dict = field(default_factory=dict)      # operator practice on the completeness of records
    checks: dict = field(default_factory=dict)       # operator practice on diagnostic checks and their effort
    practice: dict = field(default_factory=dict)     # every operator practice note, by file stem

    @classmethod
    def load(cls, root: Path = KNOWLEDGE_ROOT) -> "Sources":
        vocab = yaml.safe_load((root / "vocabulary.yaml").read_text())
        standards = {p.stem: yaml.safe_load(p.read_text()) for p in sorted((root / "standards").glob("*.yaml"))}
        types = {}
        import os                       # the run's spectral mode, read from the environment, not from the world
        addenda = {}
        if os.environ.get("ADAGENT_SPECTRAL") == "1" and (root / "spectral" / "addenda.yaml").exists():
            for e in yaml.safe_load((root / "spectral" / "addenda.yaml").read_text())["extends"]:
                addenda[e["package"]] = e
        for p in sorted(list((root / "practice").glob("*.yaml")) + list((root / "packages").glob("*/*.yaml"))):
            doc = yaml.safe_load(p.read_text())
            add = addenda.get(str(p.relative_to(root)))
            if add:          # spectral mode: the addenda's fault modes and probable causes join the package
                doc["fault_modes"] = list(doc.get("fault_modes") or []) + list(add.get("fault_modes") or [])
                for a in doc.get("alarms") or []:
                    a["probable_causes"] = list(a.get("probable_causes") or []) + list(
                        (add.get("alarm_causes") or {}).get(a["id"], []))
            for entry in doc.get("types", []):
                types[entry["id"]] = {"doc": doc, "entry": entry, "path": str(p.relative_to(root))}
        procedures = {p.stem: _outcome_keys(yaml.safe_load(p.read_text()))
                      for p in sorted((root / "procedures").glob("*.yaml"))}
        rec = root / "practice" / "records.yaml"
        chk = root / "practice" / "checks.yaml"
        practice = {p.stem: yaml.safe_load(p.read_text()) for p in sorted((root / "practice").glob("*.yaml"))}
        return cls(vocab, standards, types, procedures,
                   yaml.safe_load(rec.read_text()) if rec.exists() else {},
                   yaml.safe_load(chk.read_text()) if chk.exists() else {}, practice)

    def package_view(self, type_id: str) -> dict:
        """The parts of a type's document that apply to that type."""
        src = self.types[type_id]
        doc, entry = src["doc"], src["entry"]
        cls_, role = entry["class"], entry.get("role")

        def applies(item):
            a = item.get("applies_to")
            if a is not None and a not in (type_id, cls_):
                return False
            roles = item.get("roles")
            return not roles or role is None or role in roles

        return {
            "document": doc["document"], "path": src["path"], "vendor": doc.get("vendor", "operator"),
            "class": cls_, "entry": entry, "datasheet": doc.get("datasheet", {}),
            "signal_behaviour": [b for b in (doc.get("signal_behaviour") or [])
                                 if not isinstance(b, dict) or b.get("at") in (None, cls_)]
            if isinstance(doc.get("signal_behaviour"), list) else doc.get("signal_behaviour"),
            "monitoring_points": [m for m in doc.get("monitoring_points", []) if applies(m)],
            "fault_modes": [f for f in doc.get("fault_modes", []) if applies(f)],
            "alarms": [a for a in (doc.get("alarms") or []) if applies(a)],
            "standards": doc.get("standards", []), "notes": doc.get("notes", []), "routing": doc.get("routing"),
        }


# ---------------------------------------------------------------------- processes

@dataclass
class LifecycleState:
    kg: KnowledgeGraph
    sources: Sources
    closed_tickets: list = field(default_factory=list)
    ticket_state: dict = field(default_factory=dict)     # id -> (equipment type at closure, mode, verification)
    procedures_in_graph: bool = False                     # the graph arm of the procedures comparison


def initial_knowledge(st: LifecycleState) -> None:
    """Standing knowledge before the network exists: vocabulary, standards and plant practice."""
    kg, src, tag = st.kg, st.sources, "initial"
    for quality, rels in (src.records or {}).get("relations", {}).items():
        for r in rels:
            kg.record_quality[r] = quality
    for e, d in src.vocabulary["effects"].items():
        kg.put_node(f"effect.{e}", "Effect", tag, **d)
    for o, d in src.vocabulary["observables"].items():
        kg.put_node(f"obs.{o}", "Observable", tag, unit=d.get("unit"), scope=d.get("scope"))
        for e in d.get("sees", []):
            kg.put_edge(f"obs.{o}", f"effect.{e}", "sees", tag)
    for term, d in src.vocabulary.get("locations", {}).items():
        kg.put_node(f"loc.{term}", "LocationTerm", tag, meaning=d["meaning"])
    for name, doc in src.standards.items():
        kg.put_node(f"std.{name}", "Standard", tag, document=doc["document"], source=doc.get("source"),
                    consequent_actions=doc.get("consequent_actions"), correlations=doc.get("correlations"),
                    protection=doc.get("protection"))
        for alarm, c in (doc.get("correlations") or {}).items():
            kg.put_node(alarm, "AlarmType", tag)
            kg.put_edge(f"std.{name}", alarm, "defines", tag, **c)
    for tid, s in src.types.items():
        if s["doc"].get("vendor") is None:            # operator practice documents
            onboard_type(st, tid, tag)
    if st.procedures_in_graph:
        for name, doc in src.procedures.items():
            publish_procedures(st, name, doc, tag)


def publish_procedures(st: LifecycleState, name: str, doc: dict, tag: str) -> None:
    """Write an operator procedure document into the graph (the graph arm of the procedures comparison): each
    procedure, its steps and outcomes as nodes, the alarms that select it, the observables its checks read, and the
    fault modes its leaves name."""
    kg = st.kg
    kg.put_node(f"procdoc.{name}", "ProcedureDocument", tag, document=doc["document"], source=doc.get("source"))
    for rank, sel in enumerate(doc.get("selection_order", [])):
        pid = f"proc.{sel['procedure']}"
        if "alarm" in sel:
            if not kg.has(sel["alarm"]):
                kg.put_node(sel["alarm"], "AlarmType", tag)
            kg.put_edge(sel["alarm"], pid, "clearedBy", tag, rank=rank)
        else:
            kg.put_node(f"trigger.degraded.{sel['degraded']}", "Trigger", tag, degraded=sel["degraded"])
            kg.put_edge(f"trigger.degraded.{sel['degraded']}", pid, "clearedBy", tag, rank=rank)
    for pr in doc["procedures"]:
        pid = f"proc.{pr['id']}"
        kg.put_node(pid, "Procedure", tag, title=pr["title"], document=f"procdoc.{name}")
        kg.put_edge(f"procdoc.{name}", pid, "contains", tag)
        kg.put_edge(pid, f"{pid}.{pr['start']}", "startsAt", tag)
        for sid, step in pr["steps"].items():
            nid = f"{pid}.{sid}"
            kg.put_node(nid, "ProcedureStep", tag, ask=step["ask"], text=step.get("text", ""))
            obs = step["ask"].get("reading")
            for o in ([obs] if isinstance(obs, str) else obs or []):
                kg.put_edge(nid, f"obs.{o}", "reads", tag)
            for outcome in ("yes", "no", "unknown"):
                if outcome in step:
                    kg.put_edge(nid, f"{pid}.{step[outcome]}", "onOutcome", tag, outcome=outcome)
        for lid, leaf in pr["leaves"].items():
            nid = f"{pid}.{lid}"
            kg.put_node(nid, "ProcedureLeaf", tag, advice=leaf.get("advice", ""),
                        goto_by_alarm=bool(leaf.get("goto_by_alarm")))
            if leaf.get("goto"):
                kg.put_edge(nid, f"proc.{leaf['goto']}", "continuesWith", tag)
            for i, c in enumerate(leaf.get("causes", [])):
                if not kg.has(c["mode"]):
                    kg.put_node(c["mode"], "FaultMode", tag)
                kg.put_edge(nid, c["mode"], "concludes", tag, where=c["where"], order=i)


def onboard_type(st: LifecycleState, type_id: str, tag: str) -> None:
    kg = st.kg
    if type_id not in st.sources.types:
        kg.put_node(type_id, "EquipmentType", tag, documented=False)
        return
    v = st.sources.package_view(type_id)
    kg.put_node(type_id, "EquipmentType", tag, documented=True, vendor=v["vendor"], element_class=v["class"],
                document=v["document"], path=v["path"], datasheet=v["datasheet"],
                signal_behaviour=v["signal_behaviour"], monitoring_points=v["monitoring_points"],
                notes=v["notes"], routing=v["routing"], role=v["entry"].get("role"), kind=v["entry"].get("kind"),
                cause_counts={})
    for fm in v["fault_modes"]:
        kg.put_node(fm["id"], "FaultMode", tag)
        kg.put_edge(type_id, fm["id"], "hasFaultMode", tag, description=fm.get("description"),
                    extends_to=fm.get("extends_to"),
                    effects=fm.get("effects", []), nature=fm.get("nature"),
                    documented_rate=fm.get("documented_rate"), twin_scenario=fm.get("twin_scenario"))
        for eff in fm.get("effects", []):
            kg.put_edge(fm["id"], f"effect.{eff['effect']}", "produces", tag, scope=eff.get("scope"))
    for al in v["alarms"]:
        kg.put_node(al["id"], "AlarmType", tag)
        kg.put_edge(type_id, al["id"], "canRaise", tag, severity=al.get("severity"), roles=al.get("roles"),
                    raised_when=al.get("raised_when"), probable_causes=al.get("probable_causes", []),
                    suppression=al.get("suppression"), reported_against=al.get("reported_against"),
                    first_on_oms=bool(al.get("first_on_oms")))
    for std in v["standards"]:
        kg.put_edge(type_id, f"std.{std}", "implements", tag)


def _add_instruments(st: LifecycleState, el: str, tag: str) -> None:
    """Create the instruments an element's type documents, up to its site's current tier."""
    kg = st.kg
    site = kg.g.nodes[el].get("site")
    site_tier = kg.g.nodes.get(f"site.{site}", {}).get("tier", "baseline")
    ty = kg.type_of(el)
    if not ty or not kg.g.nodes[ty].get("documented"):
        return
    cls_ = kg.g.nodes[el].get("cls")
    for mp in kg.g.nodes[ty].get("monitoring_points", []):
        obs = _as_list(mp.get("observes"))
        if not obs or not _tier_ok(mp["tier"], site_tier):
            continue
        if cls_ == "ROADM":                             # one channel monitor per ingress degree
            for oms, _ in kg.inn(f"roadm.{site}", "terminatesAt"):
                if kg.g.nodes[oms].get("dst") != site:
                    continue
                iid = f"inst.{site}:{oms}.{mp['id']}"
                if iid not in kg.g:
                    kg.put_node(iid, "Instrument", tag, point=mp["id"], tier=mp["tier"],
                                lake_element=f"{site}:{oms}", per_channel=True, observes=obs)
                    kg.put_edge(el, iid, "monitoredBy", tag)
                    kg.put_edge(oms, iid, "monitoredBy", tag)
                    for o in obs:
                        kg.put_edge(iid, f"obs.{o}", "observes", tag)
            continue
        iid = f"inst.{el}.{mp['id']}"
        if iid in kg.g:
            continue
        kg.put_node(iid, "Instrument", tag, point=mp["id"], tier=mp["tier"], lake_element=el,
                    rate_s=mp.get("rate_s"), observes=obs)
        kg.put_edge(el, iid, "monitoredBy", tag)
        for o in obs:
            kg.put_edge(iid, f"obs.{o}", "observes", tag)


def apply_event(st: LifecycleState, ev) -> None:
    kg, p = st.kg, ev.payload
    tag = f"{ev.kind}@{ev.t_recorded:.3f}"
    k = ev.kind
    if k == "equipment_type_onboarded":
        onboard_type(st, p["type"], tag)
    elif k == "site_in_service":
        s = p["site"]
        kg.put_node(f"site.{s}", "Site", tag, x_km=p["x_km"], y_km=p["y_km"], hub=p["hub"], tier="baseline")
    elif k == "link_in_service":
        kg.put_node(f"link.{p['link']}", "Link", tag, a=p["a"], b=p["b"], length_km=p["length_km"])
        for o in p["oms"]:
            kg.put_node(o["id"], "OMS", tag, src=o["src"], dst=o["dst"], link=p["link"])
            kg.put_edge(o["id"], "PLANT-OMS-INPUT", "hasType", tag)
            kg.put_edge(o["id"], f"link.{p['link']}", "partOf", tag)
            kg.put_edge(o["id"], f"roadm.{o['src']}", "terminatesAt", tag, end="src")
            kg.put_edge(o["id"], f"roadm.{o['dst']}", "terminatesAt", tag, end="dst")
            prev = None
            for i, e in enumerate(o["elements"]):
                if e["kind"] == "span":
                    kg.put_node(e["id"], "FibreSpan", tag, oms=o["id"], index=e["index"], length_km=e["length_km"],
                                nominal_loss_db=e["nominal_loss_db"], aerial=e["aerial"])
                    kg.put_edge(e["id"], "PLANT-SPAN", "hasType", tag)
                    kg.put_node(e["cable"], "Cable", tag)
                    kg.put_node(e["duct"], "Duct", tag)
                    kg.put_edge(e["id"], e["cable"], "inCable", tag)
                    kg.put_edge(e["id"], e["duct"], "inDuct", tag)
                else:
                    kg.put_node(e["id"], "Amplifier", tag, oms=o["id"], role=e["role"], index=e["index"])
                kg.put_edge(o["id"], e["id"], "traverses", tag, order=i)
                if prev:
                    kg.put_edge(prev, e["id"], "feeds", tag)
                prev = e["id"]
            if f"roadm.{o['dst']}" in kg.g:
                _add_instruments(st, f"roadm.{o['dst']}", tag)
    elif k == "equipment_installed":
        el = p["element"]
        attrs = {a: b for a, b in p.items() if a not in ("element", "type", "cls")}
        kg.put_node(el, p.get("cls", "Equipment"), tag, **attrs)
        if p.get("type"):
            if p["type"] not in kg.g:
                kg.put_node(p["type"], "EquipmentType", tag, documented=False)
            kg.put_edge(el, p["type"], "hasType", tag)
        if p.get("site"):
            kg.put_edge(el, f"site.{p['site']}", "locatedAt", tag)
        if p.get("host"):
            kg.put_edge(el, p["host"], "hostedOn", tag)
        if p.get("cls") == "ClientPort" and p.get("service") in kg.g:
            kg.put_edge(p["service"], el, "hasPort", tag)
        _add_instruments(st, el, tag)
    elif k == "lightpath_provisioned":
        lp = p["lightpath"]
        kg.put_node(lp, "OCh", tag, slot=p["slot"], rate_g=p["rate_g"], kind=p["kind"], nodes=p["nodes"])
        kg.put_edge(lp, p["src_trx"], "endsAt", tag, end="a")
        kg.put_edge(lp, p["dst_trx"], "endsAt", tag, end="z")
        n = p["nodes"]
        for j, o in enumerate(p["oms_path"]):
            kg.put_edge(lp, o, "carriedBy", tag, direction="fwd", order=j)
        rev = [f"oms.{n[j + 1]}-{n[j]}" for j in range(len(n) - 1)][::-1]
        for j, o in enumerate(rev):
            kg.put_edge(lp, o, "carriedBy", tag, direction="rev", order=j)
    elif k == "service_provisioned":
        sid = p["service"]
        kg.put_node(sid, "ClientService", tag, src=p["src"], dst=p["dst"], rate=p["rate"], protection=p["protection"])
        for site in (p["src"], p["dst"]):
            cp = f"cp.{sid}.{site}"
            if cp in kg.g:
                kg.put_edge(sid, cp, "hasPort", tag)
        for role, rel in (("working", "working"), ("protect", "protecting")):
            path = p.get(role)
            if not path:
                continue
            kg.put_node(path["id"], "ODUPath", tag, odu=path["odu"], service=sid)
            kg.put_edge(sid, path["id"], rel, tag)
            for j, h in enumerate(path["hops"]):
                kg.put_edge(path["id"], h["lightpath"], "oduHop", tag, order=j, a=h["a"], z=h["z"])
                if j > 0:
                    kg.put_edge(path["id"], f"otn.{h['a']}", "crossConnectedAt", tag)
        if p.get("protect"):
            pg = f"pg.{sid}"
            kg.put_node(pg, "ProtectionGroup", tag, scheme=p["protection"])
            kg.put_edge(sid, pg, "protectedBy", tag)
    elif k == "service_removed":
        sid = p["service"]
        for path in (p_ for p_ in kg.service_paths(sid).values() if p_):
            kg.drop_node(path, tag)
        kg.drop_node(f"pg.{sid}", tag)
        node = kg.g.nodes.get(sid, {})
        ports = {cp for cp, _ in kg.out(sid, "hasPort")} | {f"cp.{sid}.{x}" for x in (node.get("src"), node.get("dst")) if x}
        for cp in ports:
            for inst, _ in kg.out(cp, "monitoredBy"):
                kg.drop_node(inst, tag)
            kg.drop_node(cp, tag)
        kg.drop_node(sid, tag)
    elif k == "instrument_tier_deployed":
        s = p["site"]
        node = f"site.{s}"
        cur = kg.g.nodes[node].get("tier", "baseline")
        if TIER_ORDER.index(p["tier"]) > TIER_ORDER.index(cur):
            kg.update(node, tag, tier=p["tier"])
        for el, _ in kg.inn(node, "locatedAt"):
            _add_instruments(st, el, tag)
    elif k == "maintenance_window":
        mid = f"mw.{p['site']}.{ev.t:.3f}"
        kg.put_node(mid, "MaintenanceWindow", tag, t_day=ev.t, duration_h=p["duration_h"])
        kg.put_edge(mid, f"site.{p['site']}", "at", tag)
    elif k == "incident_closed":
        tk = p["ticket"]
        st.closed_tickets.append(tk)
        loc = tk.get("recorded_location")
        ty = kg.type_of(loc) if loc and loc in kg.g else None
        st.ticket_state[tk["id"]] = (ty, tk.get("recorded_fault_mode"), tk["verification"])
        if tk.get("recorded_fault_mode"):
            _count_cause(st, ty, tk["recorded_fault_mode"], VERIFICATION_WEIGHT.get(tk["verification"], 0.5), tag)
    elif k == "ticket_followup":
        # A later finding confirms, completes or refutes a closed ticket's explanation: the base rates move the
        # ticket's contribution from its old explanation and weight to the new.
        ty, mode0, ver0 = st.ticket_state[p["ticket_id"]]
        mode1, ver1 = p.get("recorded_fault_mode", mode0), p["verification"]
        if mode0:
            _count_cause(st, ty, mode0, -VERIFICATION_WEIGHT.get(ver0, 0.5), tag)
        if mode1:
            _count_cause(st, ty, mode1, VERIFICATION_WEIGHT.get(ver1, 0.5), tag)
        st.ticket_state[p["ticket_id"]] = (ty, mode1, ver1)


def _count_cause(st: LifecycleState, ty: Optional[str], mode: str, w: float, tag: str) -> None:
    """Base rates are counted on the equipment type the location had when the ticket closed."""
    kg = st.kg
    if not ty or w == 0:
        return
    counts = dict(kg.g.nodes[ty].get("cause_counts", {}))
    counts[mode] = round(counts.get(mode, 0.0) + w, 10)
    if counts[mode] <= 1e-9:
        del counts[mode]
    kg.update(ty, tag, cause_counts=counts)


def build_graph(events: Iterable, t: float, sources: Optional[Sources] = None,
                observers: Iterable = (), procedures_in_graph: bool = False) -> LifecycleState:
    """The knowledge graph as recorded by time t (days).

    Observers are processes outside the graph that act on lifecycle events as they are recorded, with the
    graph as it stands at that moment (for example the closure process that writes a tool's experience
    store when a ticket closes). Each has a method on_event(event, kg) called after the event is applied.
    """
    st = LifecycleState(KnowledgeGraph(), sources or Sources.load(), procedures_in_graph=procedures_in_graph)
    initial_knowledge(st)
    recorded = sorted((e for e in events if e.t_recorded is not None and e.t_recorded <= t),
                      key=lambda e: (e.t_recorded, _ORDER.get(e.kind, 9)))
    for ev in recorded:
        apply_event(st, ev)
        for ob in observers:
            ob.on_event(ev, st.kg)
    return st


# Within one instant: types before equipment, sites before links, links before equipment on them,
# equipment before the lightpaths and services that use it.
_ORDER = {"equipment_type_onboarded": 0, "site_in_service": 1, "link_in_service": 2,
          "equipment_installed": 3, "instrument_tier_deployed": 4, "lightpath_provisioned": 5,
          "service_provisioned": 6, "service_removed": 7, "maintenance_window": 8, "incident_closed": 9, "ticket_followup": 10}
