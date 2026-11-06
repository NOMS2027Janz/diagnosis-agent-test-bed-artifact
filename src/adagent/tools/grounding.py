"""Grounding: maps abstract hypotheses onto elements of the current case ("Reference Network, Data Sets
and Knowledge Graph" §6.3).

An abstract hypothesis is a fault mode with one of:
- an element named directly;
- a relation to what was seen, as the experience store writes it ("at the element raising alarm A", "k elements
  upstream of it", "the far-end transmitter", "a port of a down service", "a cross-connect on its path", or
  only an element class);
- a location term of the shared vocabulary ("upstream_on_path", "far_end_transmitter", "odu_path_switch", ...;
  the "where" of probable causes in the description packages, defined in the graph as LocationTerm nodes),
  anchored on the element that saw a symptom or on a service;
- an element every affected service's working path depends on.

Grounding resolves it with the graph against the case frame and keeps only locations whose equipment type
documents the fault mode, at the grain the documentation gives (a mode whose documented effect is on one
channel lies at a ROADM's channel, "oms|slot"; a mode whose effect is on every channel arriving on one degree lies
at that degree's WSS input port, "oms|port"; a mode whose effect is on one ODU lies at a cross-connect,
"switch|ODU path"). It is a tool of its own, used by analogy search, the LLM proposer and alarm correlation, so
none of them reaches into another's internals. It names no fault mode.
"""
from __future__ import annotations

from typing import Optional

from ..schemas import ScenarioFrame
from .experience import element_role

PATH_CLASSES = ("FibreSpan", "Amplifier", "OMS")
PORT = "port"            # "oms|port": the WSS input port on which the OMS arrives at the ROADM it ends at


def is_port(loc: str) -> bool:
    """Whether a location is a ROADM's WSS input port ("oms|port")."""
    return loc.partition("|")[2] == PORT


def wss_locations(oms: str, slot) -> list[str]:
    """The WSS locations a channel meets where `oms` ends: the channel itself ("oms|slot") and the input port it
    arrives on ("oms|port"). Grounding keeps whichever the documented mode's grain admits."""
    return [f"{oms}|{slot}", f"{oms}|{PORT}"]


def owner(kg, loc: str) -> str:
    """The graph element that owns a location ("oms|slot" is the WSS channel and "oms|port" the WSS input port at
    the ROADM the OMS ends at; "switch|ODU path" is a cross-connect on a switch)."""
    if "|" in loc:
        a = loc.split("|", 1)[0]
        if kg.cls(a) == "OMS":
            return f"roadm.{kg.g.nodes[a]['dst']}"
        return a
    return loc


def _scopes(d: dict) -> set:
    return {e.get("scope") for e in d.get("effects", [])}


def admits(kg, loc: str, mode: str) -> bool:
    """Whether the location's equipment type documents the mode, at the location's grain."""
    base = loc.split("|")[0]
    if not kg.has(base):
        return False
    own = owner(kg, loc)
    d = next((d for m, d in kg.fault_modes_for(own) if m == mode), None)
    return d is not None and _grain_ok(kg.cls(own), d, loc)


def _grain_ok(c: str, d: dict, loc: str) -> bool:
    if c == "ROADM":
        sc = _scopes(d)
        if "|" not in loc:                       # the whole ROADM: only for modes on neither a channel nor a port
            return not (sc & {"channel", PORT})
        return PORT in sc if is_port(loc) else "channel" in sc
    if c == "OTNSwitch":
        return ("one_odu" in _scopes(d)) == ("|" in loc)
    return True


def class_admits(kg, loc: str, mode: str) -> bool:
    """Whether some equipment type of the location's class documents the mode (at the location's grain), even if
    the location's own type does not: the mode is known for that kind of equipment, not for this vendor's."""
    base = loc.split("|")[0]
    if not kg.has(base):
        return False
    own = owner(kg, loc)
    c = kg.cls(own)
    for ty in kg.nodes_of("EquipmentType"):
        if kg.g.nodes[ty].get("element_class") != c:
            continue
        d = next((d for m, d in kg.out(ty, "hasFaultMode") if m == mode), None)
        if d is not None and _grain_ok(c, d, loc):
            return True
    return False


def graph_element(el: str) -> str:
    """The graph element behind a frame element (a channel monitor reading "site:oms|slot" is the channel
    "oms|slot"; "oms|port" is returned as it is)."""
    base, _, slot = el.partition("|")
    if ":" in base:
        base = base.split(":", 1)[1]
    return f"{base}|{slot}" if slot else base


class GroundingTool:
    """undocumented_weight: 0 (the default) keeps only locations whose own equipment documents the mode. A value
    above 0 also keeps locations whose equipment class has the mode documented by another type (a vendor's
    documentation gap), at that weight; ground_weighted() reports the weights. An option for an experimental arm."""
    name, version = "grounding", "0.4"

    def __init__(self, kg, undocumented_weight: float = 0.0):
        self.kg = kg
        self.undocumented_weight = undocumented_weight
        self._paths: dict = {}

    def _path(self, lp, d):
        if (lp, d) not in self._paths:
            self._paths[(lp, d)] = self.kg.signal_path(lp, d)
        return self._paths[(lp, d)]

    def admits(self, loc: str, mode: str) -> bool:
        return admits(self.kg, loc, mode)

    # ------------------------------------------------------------------ location terms
    def terms(self) -> dict:
        """The location vocabulary, as held in the graph."""
        return {n[4:]: self.kg.g.nodes[n].get("meaning") for n in sorted(self.kg.nodes_of("LocationTerm"))}

    def resolve(self, where: str, unit: str, reported: Optional[str] = None, services: Optional[list] = None,
                on_protection: frozenset = frozenset()) -> list[str]:
        """Elements a location term names, relative to a unit (or, for the ODU-path terms, to services)."""
        kg = self.kg
        reported = reported or unit
        base = unit.split("|")[0]
        if where in ("this_unit", "this_port"):
            return [unit]
        if where == "upstream_span":
            if kg.cls(reported) == "FibreSpan":
                return [reported]
            return [u for u, _ in kg.inn(base, "feeds")]
        if where == "upstream_amplifier":
            prev = [u for u, _ in kg.inn(base, "feeds")]
            return [p for q in prev for p, _ in kg.inn(q, "feeds") if kg.cls(p) == "Amplifier"]
        if where == "oms_input":
            return [o for o, _ in kg.inn(base, "traverses")] or ([base] if kg.cls(base) == "OMS" else [])
        if where in ("far_end_transmitter", "far_end_receiver"):
            out = []
            for lp, d in kg.inn(base, "endsAt"):
                ends = kg.lightpath_ends(lp)
                out.append(ends["z"] if d["end"] == "a" else ends["a"])
            return out
        if where == "upstream_roadm_channel":
            out = []
            slot_of = unit.split("|")[1] if "|" in unit and kg.cls(base) == "OMS" and not is_port(unit) else None
            for lp, d in kg.lightpaths_through(base):
                if slot_of is not None and str(kg.g.nodes[lp].get("slot")) != slot_of:
                    continue
                path = self._path(lp, d)
                if base not in path:
                    continue
                up = path[:path.index(base)]
                roadm = next((x for x in reversed(up) if kg.cls(x) == "ROADM"), None)
                if roadm is None:
                    continue
                prev = next((x for x in reversed(up[:up.index(roadm)]) if kg.cls(x) == "OMS"), None)
                if prev:
                    out += wss_locations(prev, kg.g.nodes[lp].get("slot"))
            return list(dict.fromkeys(out))
        if where in ("upstream_on_path", "path_to_far_end"):
            out = []
            slot_of = None
            if "|" in unit and kg.cls(base) == "OMS" and not is_port(unit):   # a channel: only its own lightpath
                slot_of = unit.split("|")[1]
            for lp, d in kg.lightpaths_through(base):
                if slot_of is not None and str(kg.g.nodes[lp].get("slot")) != slot_of:
                    continue
                path = self._path(lp, d)
                if base not in path:
                    continue
                up = path[:path.index(base)]
                out += [x for x in up if kg.cls(x) in PATH_CLASSES]
                slot = kg.g.nodes[lp].get("slot")
                out += [w for x in up if kg.cls(x) == "OMS" for w in wss_locations(x, slot)]
            if where == "path_to_far_end":
                out += self.resolve("far_end_transmitter", unit)
            return list(dict.fromkeys(out))
        svcs = services if services is not None else kg.services_through(base)
        if where == "either_port":
            return list(dict.fromkeys(p for s in svcs for p in kg.ports_of(s)))
        if where == "odu_path_switch":
            out = []
            for s in svcs:
                for p in kg.service_paths(s).values():
                    if p:
                        sws = [sw for sw, _ in kg.out(p, "crossConnectedAt")]
                        out += sws + [f"{sw}|{p}" for sw in sws]
            return list(dict.fromkeys(out))
        if where in ("working_odu_path", "protection_odu_path", "active_odu_path"):
            out = []
            for s in svcs:
                paths = kg.service_paths(s)
                which = {"working_odu_path": "working", "protection_odu_path": "protect"}.get(where)
                if which is None:
                    which = "protect" if s in on_protection and paths["protect"] else "working"
                p = paths[which]
                if p:
                    out += kg.path_elements(p)
                    out += [f"{sw}|{p}" for sw, _ in kg.out(p, "crossConnectedAt")]
                    for h in kg.odu_hops(p):              # the path's own channels at each ROADM it passes
                        slot = kg.g.nodes[h["lightpath"]].get("slot")
                        for d in ("fwd", "rev"):
                            out += [w for x in self._path(h["lightpath"], d) if kg.cls(x) == "OMS"
                                    for w in wss_locations(x, slot)]
            return list(dict.fromkeys(out))
        return []

    # ------------------------------------------------------------------ the case's symptomatic channels and paths
    def _case_lightpaths(self, frame: ScenarioFrame) -> set:
        kg, out = self.kg, set()
        for s in frame.symptoms:
            el = graph_element(s.element)
            base = el.split("|")[0]
            if kg.cls(base) == "LineInterface":
                out |= {lp for lp, _ in kg.inn(base, "endsAt")}
            elif "|" in el and kg.cls(base) == "OMS":
                slot = el.split("|")[1]
                out |= {lp for lp, _ in kg.lightpaths_through(base) if is_port(el) or str(kg.g.nodes[lp].get("slot")) == slot}
        for sv in frame.services:
            if sv.state != "ok" and kg.has(sv.service):
                w = kg.service_paths(sv.service)["working"]
                if w:
                    out |= {h["lightpath"] for h in kg.odu_hops(w)}
        return out

    def _case_paths(self, frame: ScenarioFrame) -> set:
        kg = self.kg
        return {p for sv in frame.services if sv.state != "ok" and kg.has(sv.service)
                for p in kg.service_paths(sv.service).values() if p}

    def _expand(self, loc: str, frame: ScenarioFrame) -> list[str]:
        """A whole ROADM or switch named for a per-channel, per-port or per-ODU fault becomes the channels and WSS
        input ports, or the cross-connects, of the case at it; a section (OMS) also names the WSS input port it
        arrives on."""
        kg = self.kg
        c = kg.cls(loc.split("|")[0])
        if "|" not in loc and c == "OMS":          # a section also names the WSS input port it arrives on
            return [loc, f"{loc}|{PORT}"]
        if "|" in loc or c not in ("ROADM", "OTNSwitch"):
            return [loc]
        out = [loc]
        if c == "ROADM":
            lps = self._case_lightpaths(frame)
            for lp, d in kg.lightpaths_through(loc):
                if lp not in lps:
                    continue
                path = self._path(lp, d)
                if loc in path:
                    i = path.index(loc)
                    prev = next((x for x in reversed(path[:i]) if kg.cls(x) == "OMS"), None)
                    if prev:
                        out += wss_locations(prev, kg.g.nodes[lp].get("slot"))
        else:
            paths = self._case_paths(frame)
            out += [f"{loc}|{p}" for p, _ in kg.inn(loc, "crossConnectedAt") if p in paths]
        return out

    # ------------------------------------------------------------------ grounding
    def ground(self, frame: ScenarioFrame, mode: str, relation=None, element: str = None) -> list[str]:
        """Locations, documented ones only (or, with undocumented_weight above 0, also class-documented ones)."""
        return [loc for loc, _ in self.ground_weighted(frame, mode, relation, element)]

    def ground_weighted(self, frame: ScenarioFrame, mode: str, relation=None, element: str = None) -> list:
        """(location, weight): 1 where the location's equipment documents the mode; undocumented_weight where only
        another type of its class does (when that option is on)."""
        out = []
        for loc in self._candidates(frame, mode, relation, element):
            if self.admits(loc, mode):
                out.append((loc, 1.0))
            elif self.undocumented_weight > 0 and class_admits(self.kg, loc, mode):
                out.append((loc, self.undocumented_weight))
        return out

    def _candidates(self, frame: ScenarioFrame, mode: str, relation=None, element: str = None) -> list[str]:
        kg = self.kg
        alarms = [(s.observable, s.element) for s in frame.symptoms if s.kind == "alarm"]
        down = [s.service for s in frame.services if s.state == "down"]
        if element:
            el = graph_element(element)
            locs = self._expand(el, frame) if kg.has(el.split("|")[0]) else []
        elif not relation:
            return []
        elif relation[0] == "where":
            # ("where", term, anchor element or None, service or None)
            _, term, anchor, svc = relation
            prot = frozenset(s.service for s in frame.services if s.on_protection)
            if anchor:
                unit = graph_element(anchor)
                if not kg.has(unit.split("|")[0]):
                    return []
                locs = self.resolve(term, unit, services=[svc] if svc else None, on_protection=prot)
            elif svc and kg.has(svc):
                unit = (kg.ports_of(svc) or [svc])[0]
                locs = self.resolve(term, unit, services=[svc], on_protection=prot)
            else:
                return []
            locs = [x for loc in locs for x in self._expand(loc, frame)]
        elif relation[0] == "shared":
            locs = [x for e in frame.shared_elements if kg.has(e.split("|")[0]) for x in self._expand(e, frame)]
        elif relation[0] == "at":
            locs = [el for a, el in alarms if a == relation[1]]
        elif relation[0] == "far_end_of":
            locs = []
            for a, el in alarms:
                if a == relation[1] and kg.cls(el) == "LineInterface":
                    for lp, d in kg.inn(el, "endsAt"):
                        ends = kg.lightpath_ends(lp)
                        locs += [x for x in ends.values() if x != el]
        elif relation[0] == "upstream_of":
            _, a0, k, role = relation
            locs = []
            for a, el in alarms:
                if a != a0:
                    continue
                for lp, d in kg.lightpaths_through(el.split("|")[0]):
                    path = self._path(lp, d)
                    e0 = el.split("|")[0]
                    if e0 in path and path.index(e0) - k >= 0:
                        x = path[path.index(e0) - k]
                        if role in ("WSSChannel", "WSSPort") and kg.cls(x) == "ROADM":
                            prev = next((p for p in reversed(path[:path.index(x)]) if kg.cls(p) == "OMS"), None)
                            if prev:
                                locs.append(f"{prev}|{PORT}" if role == "WSSPort"
                                            else f"{prev}|{kg.g.nodes[lp].get('slot')}")
                        elif element_role(kg, x) == role:
                            locs.append(x)
        elif relation[0] == "port_of_down_service":
            locs = [p for s in down for p in kg.ports_of(s)]
        elif relation[0] in ("switch_on_paths_of_down_services", "xc_on_path_of_down_service"):
            locs = []
            for s in down:
                w = kg.service_paths(s)["working"]
                if w:
                    sws = [sw for sw, _ in kg.out(w, "crossConnectedAt")]
                    locs += [f"{sw}|{w}" for sw in sws] if relation[0].startswith("xc") else sws
        elif relation[0] == "class":
            role = relation[1]
            locs = []
            for el in frame.affected_elements + frame.scope:
                base = el.split("|")[0]
                if not kg.has(base):
                    continue
                if role in ("WSSChannel", "WSSPort"):
                    if kg.cls(base) == "LineInterface":
                        for lp, d in kg.inn(base, "endsAt"):
                            direction = "rev" if d["end"] == "a" else "fwd"
                            path = self._path(lp, direction)
                            slot = kg.g.nodes[lp].get("slot")
                            for i, x in enumerate(path):
                                if kg.cls(x) == "ROADM" and i > 0:
                                    prev = next((p for p in reversed(path[:i]) if kg.cls(p) == "OMS"), None)
                                    if prev:
                                        locs.append(f"{prev}|{PORT}" if role == "WSSPort" else f"{prev}|{slot}")
                elif element_role(kg, base) == role and base in frame.affected_elements:
                    locs.append(base)
        else:
            locs = []
        return list(dict.fromkeys(locs))
