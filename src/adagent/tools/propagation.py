"""Forward propagation of a hypothesised fault through the knowledge graph.

Given a fault mode at an element, predict what should be seen: alarms (type, element), changes in
instrument streams (observable, lake element), and client outcomes (down, hit) per service. Every piece of
domain knowledge comes from the graph:

- what the fault does to the element: the hasFaultMode edge of the element's equipment type (effects,
  their scope and where they start; the fault's nature);
- how each element passes effects on: the signal behaviour in its type's description package (rules
  "in -> out", "pass" by default, absorption "within_db" which makes the outcome uncertain, and per-channel
  routing at ROADMs);
- which instruments see which effects: the vocabulary's "sees" and "scope" for each observable, and each
  instrument's monitoring point and location;
- which alarms are raised: each type's alarm definitions ("raised_when ... sees", first-on-OMS suppression,
  "reported_against", and probable causes at "this_unit"/"this_port");
- what happens on the digital layer: the G.798 extract's defects, correlations and consequent actions (AIS
  downstream on every ODU carried by a failed OTU) and its SNCP behaviour (the selector takes a defect-free
  path).

The code below names no fault mode. It is a general interpreter of the knowledge in the graph, the kind of
reasoner a fault-propagation tool contains. Predictions carry a strength: "required" (follows from a hard
fault through certain transfers), "expected" (follows from a soft fault; visible if large enough) or
"possible" (depends on margin or on absorption the documents cannot quantify).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

RANK = {"required": 0, "expected": 1, "possible": 2}
NATURE_STRENGTH = {"hard": "required", "soft": "expected", "incipient": "expected", "transient": "possible"}


def weaker(a: str, b: str) -> str:
    return a if RANK[a] >= RANK[b] else b


class Prediction(dict):
    """key -> strength; keys are ("alarm", type, element), ("obs", observable, lake element),
    ("client", "down"|"hit", service). `transient` is set when the fault's effects are brief, so they can
    explain excursions but not lasting level changes."""
    transient: bool = False

    def add(self, key, strength: str) -> None:
        if key not in self or RANK[strength] < RANK[self[key]]:
            self[key] = strength


class Propagator:
    def __init__(self, kg):
        self.kg = kg
        g = kg.g
        self.effect_defect = {n[7:]: d.get("defect") for n, d in g.nodes(data=True) if d.get("cls") == "Effect"}
        self.obs_scope = {n[4:]: d.get("scope") for n, d in g.nodes(data=True) if d.get("cls") == "Observable"}
        self.obs_sees = defaultdict(set)
        for n, d in g.nodes(data=True):
            if d.get("cls") == "Observable":
                for e, _ in kg.out(n, "sees"):
                    self.obs_sees[n[4:]].add(e[7:])
        std = [d for n, d in g.nodes(data=True) if d.get("cls") == "Standard"]
        self.correlations = {}
        self.consequent = []
        self.protection_reports = {}
        for d in std:
            self.correlations.update(d.get("correlations") or {})
            self.consequent += d.get("consequent_actions") or []
            self.protection_reports.update(((d.get("protection") or {}).get("reports")) or {})
        # defects whose consequent action inserts AIS downstream on every carried ODU
        self.ais_triggers = set()
        for c in self.consequent:
            if any(isinstance(a, dict) and a.get("insert") == "ODU_AIS" for a in c.get("actions", [])):
                self.ais_triggers |= {w for w in c.get("when", []) if isinstance(w, str)}
        self._sp_cache: dict = {}
        self._type_cache: dict = {}

    # ------------------------------------------------------------------ graph helpers
    def tinfo(self, el: str) -> dict:
        if el not in self._type_cache:
            ty = self.kg.type_of(el)
            self._type_cache[el] = self.kg.g.nodes[ty] if ty else {}
        return self._type_cache[el]

    def path(self, lp: str, d: str) -> list[str]:
        if (lp, d) not in self._sp_cache:
            self._sp_cache[(lp, d)] = self.kg.signal_path(lp, d)
        return self._sp_cache[(lp, d)]

    def fault_edge(self, mode: str, el: str) -> Optional[dict]:
        base = self._owner(el)
        for m, d in self.kg.fault_modes_for(base):
            if m == mode:
                return d
        return None

    def _owner(self, el: str) -> str:
        """The graph element that owns a candidate location ("oms|slot" is the WSS channel and "oms|port" the WSS
        input port at the ROADM the OMS ends at; "switch|ODU path" is a cross-connect on a switch)."""
        if "|" in el:
            a, _ = el.split("|", 1)
            if self.kg.cls(a) == "OMS":
                return f"roadm.{self.kg.g.nodes[a]['dst']}"
            return a
        return el

    def _transfer(self, el: str, effects: dict) -> dict:
        """Apply an element's documented signal behaviour to the effects arriving at it."""
        info = self.tinfo(el)
        rules = info.get("signal_behaviour")
        if not isinstance(rules, list):
            return dict(effects)
        out: dict = {}
        for (eff, scope), st in effects.items():
            matched = [r for r in rules if isinstance(r, dict) and (r.get("in") == eff or
                                                                   (isinstance(r.get("in"), list) and eff in r["in"]))]
            if not matched:
                default = next((r for r in rules if isinstance(r, dict) and r.get("in") == "default"), None)
                if default is None or default.get("out") == "pass":
                    out[(eff, scope)] = weaker(out.get((eff, scope), st), st) if (eff, scope) in out else st
                continue
            uncertain = any("within_db" in r or "beyond_db" in r for r in matched)
            for r in matched:
                for o in (r.get("out") or []):
                    if o == "pass":
                        o = eff
                    s2 = "possible" if uncertain else st
                    out[(o, scope)] = s2 if (o, scope) not in out else min(out[(o, scope)], s2, key=RANK.get)
        if info.get("routing") and "per channel" in info.get("routing", ""):
            out = {(e, "channel" if sc == "all_channels" else sc): s for (e, sc), s in out.items()}
        return out

    def _instruments(self, el: str) -> list[dict]:
        res = []
        mps = {m["id"]: m for m in self.tinfo(el).get("monitoring_points", [])}
        for inst, a in self.kg.instruments_on(el):
            mp = mps.get(a.get("point"), {})
            res.append({"id": inst, "observes": a.get("observes", []), "lake": a.get("lake_element"),
                        "location": mp.get("location"), "per_channel": a.get("per_channel")})
        return res

    def _sees(self, observable: str, eff: str, scope: str) -> bool:
        if eff not in self.obs_sees.get(observable, ()):
            return False
        if self.obs_scope.get(observable) == "aggregate":
            return scope == "all_channels"
        return True

    def _same_section(self, span: str) -> list[str]:
        """The span of the same cable section in the other direction of the link (from the graph)."""
        kg = self.kg
        a = kg.g.nodes[span]
        oms = a["oms"]
        o = kg.g.nodes[oms]
        other = f"oms.{o['dst']}-{o['src']}"
        if not kg.has(other):
            return []
        spans = [e for e in kg.oms_elements(other) if kg.cls(e) == "FibreSpan"]
        mirror = len(spans) - 1 - a["index"]
        return [spans[mirror]] if 0 <= mirror < len(spans) else []

    def _upstream_of(self, el: str) -> Optional[str]:
        prev = self.kg.inn(el, "feeds")
        return prev[0][0] if prev else None

    # ------------------------------------------------------------------ prediction
    def predict(self, mode: str, el: str) -> Prediction:
        return self.predict_joint([(mode, el)])

    def predict_joint(self, causes: list) -> Prediction:
        """The documented effects of causes acting together ([(fault mode, location)]): optical and digital
        effects accumulate across causes before the services are judged, so a working path hit by one cause and its
        protecting path hit by another take the service down."""
        P = Prediction()
        lost_hops: dict = {}                    # (lightpath, direction) -> strength of OTU frame loss there
        errored_hops: dict = {}
        odu_effects: dict = {}                  # (ODU path, reverse?) -> (effect, strength)
        flags = []
        for mode, el in causes:
            edge = self.fault_edge(mode, el)
            if edge is None:
                continue
            flags.append(edge.get("nature") == "transient")
            self._predict_into(P, mode, el, edge, lost_hops, errored_hops, odu_effects)
        P.transient = bool(flags) and all(flags)
        self._finish(P, lost_hops, errored_hops, odu_effects)
        return P

    def _predict_into(self, P, mode, el, edge, lost_hops, errored_hops, odu_effects) -> None:
        base = NATURE_STRENGTH.get(edge.get("nature"), "expected")
        owner = self._owner(el)
        cls = self.kg.cls(owner)
        # alarms documented as raised at this unit for this cause
        for al, d in self.kg.alarm_definitions(owner):
            if d.get("severity") == "not_alarmed":
                continue
            for pc in d.get("probable_causes") or []:
                if pc.get("mode") == mode and pc.get("where") in ("this_unit", "this_port"):
                    P.add(("alarm", al, owner), weaker(base, "expected"))   # a documented cause *can* raise it
        for e in edge.get("effects", []):
            eff, scope, where = e["effect"], e.get("scope", "all_channels"), e.get("where")
            base_e = "possible" if e.get("if") else base               # conditional effects are uncertain
            starts = self._starts(owner, el, cls, eff, scope, where)
            if edge.get("extends_to") == "same_cable_section" and cls == "FibreSpan":
                for twin in self._same_section(owner):
                    starts += self._starts(twin, twin, cls, eff, scope, where)
            for kind, payload in starts:
                if kind == "optical":
                    lp, d, idx, sc = payload
                    self._own_output(P, self.path(lp, d)[idx], eff, sc, base_e)
                    self._walk(P, lp, d, idx, {(eff, sc): base_e}, lost_hops, errored_hops)
                elif kind == "arrive_rx":
                    lp, d = payload
                    path = self.path(lp, d)
                    self._walk(P, lp, d, len(path) - 2, {(eff, "channel"): base_e}, lost_hops, errored_hops)
                elif kind == "odu":
                    pth, rev = payload
                    old = odu_effects.get((pth, rev))
                    odu_effects[(pth, rev)] = (eff, base_e) if old is None or RANK[base_e] < RANK[old[1]] else old
                elif kind == "client":
                    svc = payload
                    for cp in self.kg.ports_of(svc):
                        P.add(("client", "down", svc), base)
                        P.add(("obs", "client_errored_seconds_pm15", cp), base)
                        for al, d in self.kg.alarm_definitions(cp):
                            rw = d.get("raised_when") or {}
                            if rw.get("sees") == "CLIENT_DOWN" and d.get("severity") != "not_alarmed":
                                P.add(("alarm", al, cp), base)
                elif kind == "unit":
                    for ins in self._instruments(owner):
                        for ob in ins["observes"]:
                            if self._sees(ob, eff, "all_channels"):
                                P.add(("obs", ob, ins["lake"]), base)

    def _finish(self, P, lost_hops, errored_hops, odu_effects) -> None:
        # digital layer: OTU frame loss -> AIS on every ODU carried in that direction (consequent action)
        for (lp, d), st in lost_hops.items():
            for pth in self.kg.paths_using_lightpath(lp):
                for h in self.kg.odu_hops(pth):
                    if h["lightpath"] != lp:
                        continue
                    hop_dir = "fwd" if self.kg.g.nodes[lp]["nodes"][0] == h["a"] else "rev"
                    rev = hop_dir != d
                    odu_effects[(pth, rev)] = ("ODU_AIS", st) if (pth, rev) not in odu_effects else \
                        (odu_effects[(pth, rev)][0], min(st, odu_effects[(pth, rev)][1], key=RANK.get))
        for (lp, d), st in errored_hops.items():
            for pth in self.kg.paths_using_lightpath(lp):
                svc = self.kg.service_of_path(pth)
                for cp in self.kg.ports_of(svc):
                    P.add(("obs", "client_errored_seconds_pm15", cp), "possible")
        self._services(P, odu_effects)

    def _starts(self, owner, el, cls, eff, scope, where):
        kg = self.kg
        out = []
        if where == "far_end_receiver" or where == "both_directions":
            for lp, d in kg.inn(owner, "endsAt"):
                direction = "fwd" if d["end"] == "a" else "rev"
                out.append(("optical", (lp, direction, 0, "channel")))
                if where == "both_directions":
                    other = "rev" if direction == "fwd" else "fwd"
                    out.append(("arrive_rx", (lp, other)))
            return out
        if scope == "this_receiver" or (scope == "channel" and cls == "LineInterface"):
            for lp, d in kg.inn(owner, "endsAt"):
                direction = "rev" if d["end"] == "a" else "fwd"
                out.append(("arrive_rx", (lp, direction)))
            return out
        if scope in ("every_odu_through",):
            for pth, _ in kg.inn(owner, "crossConnectedAt"):
                out += [("odu", (pth, False)), ("odu", (pth, True))]
            return out
        if scope == "one_odu":
            if "|" in el:
                pth = el.split("|", 1)[1]
                out += [("odu", (pth, False)), ("odu", (pth, True))]
            return out
        if scope == "this_service":
            svc = kg.g.nodes[owner].get("service")
            return [("client", svc)] if svc else []
        if scope == "unit":
            return [("unit", None)]
        if "|" in el and cls == "ROADM":                     # one channel at a WSS, or a WSS input port
            oms, slot = el.split("|", 1)
            port = scope == "port" or slot == "port"         # every channel arriving on the OMS, from the ROADM on
            for lp, d in kg.lightpaths_through(oms):
                if port or str(kg.g.nodes[lp].get("slot")) == slot:
                    path = self.path(lp, d)
                    if owner in path:
                        out.append(("optical", (lp, d, path.index(owner), "channel")))
            return out
        for lp, d in kg.lightpaths_through(owner):
            path = self.path(lp, d)
            if owner in path:
                out.append(("optical", (lp, d, path.index(owner), scope)))
        if not out and cls in ("FibreSpan", "Amplifier", "OMS"):
            out.append(("oms_only", None))
        return out

    def _own_output(self, P, el, eff, scope, st):
        """What the faulty unit's own output monitors see."""
        for ins in self._instruments(el):
            if ins["location"] == "output" and not ins["per_channel"]:
                for ob in ins["observes"]:
                    if self._sees(ob, eff, scope):
                        P.add(("obs", ob, ins["lake"]), st)

    def _walk(self, P, lp, d, idx, effects, lost_hops, errored_hops):
        """Carry effects leaving path[idx] downstream to the receiver."""
        path = self.path(lp, d)
        slot = self.kg.g.nodes[lp].get("slot")
        kg = self.kg
        raised_on_oms: set = set()
        cur_oms = None
        for j in range(idx + 1, len(path)):
            el = path[j]
            cls = kg.cls(el)
            if cls == "OMS":
                cur_oms = el
            arriving = effects
            if cls == "LineInterface":                         # the receiver
                self._receiver(P, lp, d, el, arriving, lost_hops, errored_hops)
                return
            leaving = self._transfer(el, arriving)
            for ins in self._instruments(el):
                if ins["per_channel"]:
                    oms_in = ins["lake"].split(":", 1)[1]
                    if j > 0 and oms_in in path[:j]:
                        for (eff, sc), st in arriving.items():
                            for ob in ins["observes"]:
                                if self._sees(ob, eff, sc):
                                    P.add(("obs", ob, f"{ins['lake']}|{slot}"), st)
                    continue
                look = leaving if ins["location"] == "output" else arriving
                for (eff, sc), st in look.items():
                    for ob in ins["observes"]:
                        if self._sees(ob, eff, sc):
                            P.add(("obs", ob, ins["lake"]), st)
            for al, dfn in kg.alarm_definitions(el):
                rw = dfn.get("raised_when") or {}
                if not rw.get("sees") or dfn.get("severity") == "not_alarmed":
                    continue
                point = next((m for m in self.tinfo(el).get("monitoring_points", []) if m["id"] == rw.get("point")), {})
                look = leaving if point.get("location") == "output" else arriving
                for (eff, sc), st in look.items():
                    if eff != rw["sees"] or (sc != "all_channels" and cls == "Amplifier"):
                        continue
                    if point.get("location") == "output" and (eff, sc) in arriving:
                        continue                               # passed on, not produced, by this unit
                    if dfn.get("first_on_oms"):                # only the first unit on the OMS raises
                        if cur_oms in raised_on_oms:
                            continue
                        raised_on_oms.add(cur_oms)
                    target = el
                    if dfn.get("reported_against") == "upstream_span":
                        target = self._upstream_of(el) or el
                    P.add(("alarm", al, target), st)
            effects = leaving
            if not effects:
                return

    def _receiver(self, P, lp, d, rx, arriving, lost_hops, errored_hops):
        kg = self.kg
        rules = self.tinfo(rx).get("signal_behaviour") or []
        out: dict = {}
        defects = set()
        for (eff, sc), st in arriving.items():
            for r in rules:
                if not isinstance(r, dict):
                    continue
                ins = r.get("in")
                if ins == eff or (isinstance(ins, list) and eff in ins):
                    for o in r.get("out") or []:
                        s2 = st if r.get("defect") else "possible"     # without a defect it depends on margin
                        out[o] = min(out.get(o, s2), s2, key=RANK.get)
                    if r.get("defect"):
                        defects.add(r["defect"])
            if self.effect_defect.get(eff):
                defects.add(self.effect_defect[eff])
        for o, st in out.items():
            if self.effect_defect.get(o):
                defects.add(self.effect_defect[o])
        for ins in self._instruments(rx):
            for ob in ins["observes"]:
                for (eff, sc), st in arriving.items():
                    if self._sees(ob, eff, sc):
                        P.add(("obs", ob, ins["lake"]), st)
                for o, st in out.items():
                    if self._sees(ob, o, "channel"):
                        P.add(("obs", ob, ins["lake"]), st)
        present = {**{e: s for (e, _), s in arriving.items()}, **out}
        for al, dfn in kg.alarm_definitions(rx):
            rw = dfn.get("raised_when") or {}
            eff = rw.get("sees")
            if eff not in present or dfn.get("severity") == "not_alarmed":
                continue
            corr = self.correlations.get(al) or {}
            if set(corr.get("suppressed_by") or []) & defects:
                continue
            P.add(("alarm", al, rx), present[eff])
        for o, st in out.items():
            if self.effect_defect.get(o) in self.ais_triggers or (defects & self.ais_triggers and
                                                                   self.effect_defect.get(o) is None):
                lost_hops[(lp, d)] = min(lost_hops.get((lp, d), st), st, key=RANK.get)
            else:
                errored_hops[(lp, d)] = st

    def _services(self, P, odu_effects: dict):
        """SNCP: the egress selector takes a defect-free path; else the service is down."""
        kg = self.kg
        by_svc = defaultdict(dict)
        for (pth, rev), (eff, st) in odu_effects.items():
            svc = kg.service_of_path(pth)
            if svc:
                by_svc[svc][(pth, rev)] = (eff, st)
        for svc, eff_map in by_svc.items():
            node = kg.g.nodes.get(svc, {})
            paths = kg.service_paths(svc)
            for rev in (False, True):
                egress = node.get("src") if rev else node.get("dst")
                cp = f"cp.{svc}.{egress}"
                w = eff_map.get((paths["working"], rev)) if paths["working"] else None
                p = eff_map.get((paths["protect"], rev)) if paths["protect"] else None
                if w and (paths["protect"] is None or p):
                    eff, st = w
                    P.add(("client", "down", svc), st)
                    P.add(("obs", "client_errored_seconds_pm15", cp), st)
                    for al, dfn in kg.alarm_definitions(cp):
                        rw = dfn.get("raised_when") or {}
                        if dfn.get("severity") != "not_alarmed" and rw.get("sees") in ("CLIENT_DOWN", eff):
                            P.add(("alarm", al, cp), st)
                elif w:
                    eff, st = w
                    P.add(("client", "hit", svc), st)
                    P.add(("obs", "client_errored_seconds_pm15", cp), st)
                    if "PROT_ACTIVE" in self.protection_reports:
                        P.add(("alarm", "PROT_ACTIVE", cp), st)
                elif p and "PROT_UNAVAIL" in self.protection_reports:
                    P.add(("alarm", "PROT_UNAVAIL", cp), p[1])
