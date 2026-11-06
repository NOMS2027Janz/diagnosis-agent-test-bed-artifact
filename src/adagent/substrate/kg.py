"""The knowledge graph ("Reference Network, Data Sets and Knowledge Graph" §5).

An in-memory directed multigraph behind two interfaces:

- a write interface used only by the lifecycle processes (src/adagent/substrate/lifecycle.py); every
  write records the lifecycle event that caused it, so the graph's content can always be traced;
- named read queries used by tools, each of which counts the nodes it touches.

A database backend can replace the implementation without changing either interface. The graph holds
configured and documented knowledge only; observations live in the data lake.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import networkx as nx


@dataclass
class QueryCost:
    queries: int = 0
    nodes_touched: int = 0

    def charge(self, n: int) -> None:
        self.queries += 1
        self.nodes_touched += n


@dataclass
class Provenance:
    op: str            # put_node | put_edge | drop_node | update
    target: str
    event: str         # e.g. "initial", "link_in_service@123.4"


class KnowledgeGraph:
    def __init__(self) -> None:
        self.g = nx.MultiDiGraph()
        self.cost = QueryCost()
        self.log: list[Provenance] = []
        self.record_quality: dict[str, str] = {}   # relation -> complete_once_recorded | incomplete | as_documented

    # ------------------------------------------------------------------ write (lifecycle only)
    def put_node(self, nid: str, cls: str, _ev: str, **attrs) -> None:
        if nid in self.g:
            self.g.nodes[nid].update(attrs)
            self.g.nodes[nid]["cls"] = cls
            self.log.append(Provenance("update", nid, _ev))
        else:
            self.g.add_node(nid, cls=cls, **attrs)
            self.log.append(Provenance("put_node", nid, _ev))

    def put_edge(self, u: str, v: str, rel: str, _ev: str, **attrs) -> None:
        if self.g.has_edge(u, v):
            for d in self.g.get_edge_data(u, v).values():
                if d.get("rel") == rel and all(d.get(k) == x for k, x in attrs.items()):
                    return
        self.g.add_edge(u, v, rel=rel, **attrs)
        self.log.append(Provenance("put_edge", f"{u}-{rel}->{v}", _ev))

    def drop_node(self, nid: str, _ev: str) -> None:
        if nid in self.g:
            self.g.remove_node(nid)
            self.log.append(Provenance("drop_node", nid, _ev))

    def update(self, nid: str, _ev: str, **attrs) -> None:
        self.g.nodes[nid].update(attrs)
        self.log.append(Provenance("update", nid, _ev))

    # ------------------------------------------------------------------ basic reads
    def has(self, nid: str) -> bool:
        return nid in self.g

    def cls(self, nid: str) -> Optional[str]:
        self.cost.charge(1)
        return self.g.nodes[nid].get("cls") if nid in self.g else None

    def attrs(self, nid: str) -> dict:
        self.cost.charge(1)
        return dict(self.g.nodes[nid]) if nid in self.g else {}

    def out(self, nid: str, rel: str) -> list[tuple[str, dict]]:
        res = [(v, d) for _, v, d in self.g.out_edges(nid, data=True) if d.get("rel") == rel] if nid in self.g else []
        self.cost.charge(1 + len(res))
        return res

    def inn(self, nid: str, rel: str) -> list[tuple[str, dict]]:
        res = [(u, d) for u, _, d in self.g.in_edges(nid, data=True) if d.get("rel") == rel] if nid in self.g else []
        self.cost.charge(1 + len(res))
        return res

    def nodes_of(self, cls: str) -> list[str]:
        res = [n for n, d in self.g.nodes(data=True) if d.get("cls") == cls]
        self.cost.charge(len(res))
        return res

    # ------------------------------------------------------------------ named queries
    def type_of(self, el: str) -> Optional[str]:
        t = self.out(el, "hasType")
        return t[0][0] if t else None

    def fault_modes_for(self, el: str) -> list[tuple[str, dict]]:
        """Documented fault modes of an element, from its equipment type's description package."""
        ty = self.type_of(el)
        return self.out(ty, "hasFaultMode") if ty else []

    def alarm_definitions(self, el: str) -> list[tuple[str, dict]]:
        """Alarms the element's type can raise, with documented conditions and probable causes."""
        ty = self.type_of(el)
        if not ty:
            return []
        role = self.g.nodes[el].get("role")
        return [(a, d) for a, d in self.out(ty, "canRaise") if not d.get("roles") or role in d["roles"]]

    def instruments_on(self, el: str) -> list[tuple[str, dict]]:
        return [(i, self.attrs(i)) for i, _ in self.out(el, "monitoredBy")]

    def site_of(self, el: str) -> Optional[str]:
        s = self.out(el, "locatedAt")
        return s[0][0] if s else None

    def oms_elements(self, oms: str) -> list[str]:
        """Amplifiers and spans of an OMS in signal order."""
        return [v for v, _ in sorted(self.out(oms, "traverses"), key=lambda x: x[1]["order"])]

    def lightpath_oms(self, lp: str, direction: str = "fwd") -> list[str]:
        """OMSs of a lightpath in signal order for a direction ("fwd" from its a-end)."""
        return [v for v, _ in sorted((x for x in self.out(lp, "carriedBy") if x[1]["direction"] == direction),
                                     key=lambda x: x[1]["order"])]

    def lightpath_ends(self, lp: str) -> dict[str, str]:
        return {d["end"]: v for v, d in self.out(lp, "endsAt")}

    def odu_hops(self, path: str) -> list[dict]:
        return [dict(lightpath=v, **{k: d[k] for k in ("order", "a", "z")})
                for v, d in sorted(self.out(path, "oduHop"), key=lambda x: x[1]["order"])]

    def service_paths(self, svc: str) -> dict[str, Optional[str]]:
        w = self.out(svc, "working")
        p = self.out(svc, "protecting")
        return {"working": w[0][0] if w else None, "protect": p[0][0] if p else None}

    def shared_risk(self, span: str) -> list[str]:
        """Spans recorded in the same cable or duct as `span` (recorded risk only)."""
        out = set()
        for rel in ("inCable", "inDuct"):
            for c, _ in self.out(span, rel):
                out |= {s for s, _ in self.inn(c, rel)}
        out.discard(span)
        return sorted(out)

    # ------------------------------------------------------------------ signal paths and the OTN layer
    def signal_path(self, lp: str, direction: str = "fwd") -> list[str]:
        """Elements a lightpath's signal passes in one direction, in order: transmitter, then for each OMS
        the ROADM it leaves, the OMS input patch, its amplifiers and spans, and finally the receiver."""
        ends = self.lightpath_ends(lp)
        tx, rx = (ends.get("a"), ends.get("z")) if direction == "fwd" else (ends.get("z"), ends.get("a"))
        out = [tx] if tx else []
        for oms in self.lightpath_oms(lp, direction):
            src = self.g.nodes[oms].get("src")
            out += [f"roadm.{src}", oms] + self.oms_elements(oms)
        if out and len(out) > 1:
            dst = self.g.nodes[self.lightpath_oms(lp, direction)[-1]].get("dst")
            out.append(f"roadm.{dst}")
        if rx:
            out.append(rx)
        return out

    def receiver(self, lp: str, direction: str = "fwd") -> Optional[str]:
        ends = self.lightpath_ends(lp)
        return ends.get("z") if direction == "fwd" else ends.get("a")

    def lightpaths_through(self, el: str) -> list[tuple[str, str]]:
        """(lightpath, direction) pairs whose signal passes the element."""
        c = self.cls(el)
        if c == "OMS":
            return sorted({(lp, d["direction"]) for lp, d in self.inn(el, "carriedBy")})
        if c in ("Amplifier", "FibreSpan"):
            return sorted({x for oms, _ in self.inn(el, "traverses") for x in self.lightpaths_through(oms)})
        if c == "LineInterface":
            out = []
            for lp, d in self.inn(el, "endsAt"):
                out += [(lp, "fwd"), (lp, "rev")]
            return sorted(out)
        if c == "ROADM":
            site = el.split(".", 1)[1]
            res = set()
            for oms, d in self.inn(el, "terminatesAt"):
                res |= set(self.lightpaths_through(oms))
            return sorted(res)
        return []

    def paths_using_lightpath(self, lp: str) -> list[str]:
        return sorted({p for p, _ in self.inn(lp, "oduHop")})

    def service_of_path(self, path: str) -> Optional[str]:
        return self.g.nodes[path].get("service") if path in self.g else None

    def services_through(self, el: str) -> list[str]:
        """Services with a working or protection ODU path through the element."""
        c = self.cls(el)
        if c == "ClientService":
            return [el]
        if c == "ClientPort":
            return [s for s, _ in self.inn(el, "hasPort")]
        if c == "OTNSwitch":
            paths = [p for p, _ in self.inn(el, "crossConnectedAt")]
        elif c == "OCh":
            paths = self.paths_using_lightpath(el)
        else:
            paths = [p for lp in {x for x, _ in self.lightpaths_through(el)} for p in self.paths_using_lightpath(lp)]
        return sorted({self.service_of_path(p) for p in paths if self.service_of_path(p)})

    def ports_of(self, svc: str) -> list[str]:
        return sorted(p for p, _ in self.out(svc, "hasPort"))

    def path_elements(self, path: str) -> list[str]:
        """Everything an ODU path depends on: its lightpaths' signal paths in both directions and the
        switches that cross-connect it."""
        out: list[str] = []
        for h in self.odu_hops(path):
            out.append(h["lightpath"])
            for d in ("fwd", "rev"):
                out += self.signal_path(h["lightpath"], d)
        out += [sw for sw, _ in self.out(path, "crossConnectedAt")]
        return list(dict.fromkeys(out))

    def service_closure(self, svc: str, which: str = "both") -> list[str]:
        paths = self.service_paths(svc)
        sel = [paths["working"]] if which == "working" else [p for p in paths.values() if p]
        out = list(self.ports_of(svc))
        for p in sel:
            if p:
                out += self.path_elements(p)
        return list(dict.fromkeys(out))

    def common_elements(self, services: Iterable[str], which: str = "working") -> list[str]:
        sets = [set(self.service_closure(s, which)) for s in services]
        return sorted(set.intersection(*sets)) if sets else []

    def instruments_for_lake_element(self, lake_element: str) -> list[str]:
        if not hasattr(self, "_lake_index") or self._lake_index_n != self.g.number_of_nodes():
            self._lake_index = {}
            for n, d in self.g.nodes(data=True):
                if d.get("cls") == "Instrument":
                    self._lake_index.setdefault(d["lake_element"], []).append(n)
            self._lake_index_n = self.g.number_of_nodes()
        base = lake_element.split("|")[0]
        res = self._lake_index.get(base, [])
        self.cost.charge(1 + len(res))
        return res

    def neighbourhood(self, nodes: Iterable[str], hops: int = 1) -> set[str]:
        frontier, seen = set(nodes), set(nodes)
        for _ in range(hops):
            nxt = set()
            for n in frontier:
                if n in self.g:
                    nxt |= set(self.g.successors(n)) | set(self.g.predecessors(n))
            nxt -= seen
            seen |= nxt
            frontier = nxt
        self.cost.charge(len(seen))
        return seen

    def completeness(self, rel: str) -> str:
        """How far absence of this relation can be trusted: 'complete_once_recorded' (absence means false, apart
        from changes not yet recorded), 'incomplete' (absence means unknown) or 'as_documented' (absence means the
        documentation does not say). Relations not declared are treated as incomplete."""
        return self.record_quality.get(rel, "incomplete")

    def stats(self) -> dict:
        from collections import Counter
        return {"nodes": self.g.number_of_nodes(), "edges": self.g.number_of_edges(),
                "classes": dict(Counter(d.get("cls") for _, d in self.g.nodes(data=True))),
                "relations": dict(Counter(d.get("rel") for _, _, d in self.g.edges(data=True)))}
