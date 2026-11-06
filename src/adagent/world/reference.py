"""Reference network ref-national-24 ("Reference Network, Data Sets and Knowledge Graph",
revision 3, §4.1 and Table 2).

A seeded design procedure, not a copy of any operator's network. It produces:

- 24 ROADM sites on a plane with inter-city distances typical of a national backbone, joined
  by fibre links of 50 to 600 km chosen as a Gabriel graph and repaired to be two-edge-connected
  with degree at most 5;
- spans of 60 to 100 km, amplified in constant-gain mode, 10 to 15 per cent aerial;
- shared-risk groups of two kinds: ducts shared by links leaving a site in nearly the same
  direction (metro exits), and long cables shared by two links over their first spans. Some are
  recorded in plant records and some are not (`SRLG.known`);
- OTN switches at 8 hub sites; every other site homes to its nearest hub (and, for protected
  services, to a second hub);
- about 400 client services (100GE, 10GE, 400GE) carried on ODU paths that cross one to several
  lightpaths through the hubs' OTN switches; ODU SNCP for about 30 per cent of 100GE and 10GE
  services, with protection routed to avoid the working path's links and *known* shared risk
  only; OCh 1+1 for half of the 400GE services;
- lightpaths created on demand: 400G DP-16QAM up to 800 km, 200G DP-QPSK beyond, with
  first-fit spectrum assignment on 64 slots of 75 GHz.

This module is world-side: it is the simulated network itself. Nothing in it is visible to the
agent except through the lifecycle processes that write the knowledge graph.
"""
from __future__ import annotations

import itertools
import math
import random
from typing import Optional

import networkx as nx

from .network import (ClientService, Lightpath, Network, ODUHop, ODUPath, OTNSwitch, SRLG,
                      Transponder, _build_oms)

UNITS = {"ODU4": 10, "ODU2": 1, "ODUflex": 40}          # 10G tributary units
RATE_ODU = {"100GE": "ODU4", "10GE": "ODU2", "400GE": "ODUflex"}
N_SLOTS = 64


def _line_for(length_km: float) -> tuple[int, str, float, int]:
    """(rate_g, modulation, baud_gbd, units) for a lightpath of this length."""
    if length_km <= 800:
        return 400, "DP-16QAM", 64.0, 40
    return 200, "DP-QPSK", 64.0, 20


class _Infeasible(Exception):
    pass


class _Builder:
    def __init__(self, seed: int, n_sites: int, n_hubs: int, n_services: int):
        self.rng = random.Random(seed)
        self.seed, self.n_sites, self.n_hubs, self.n_services = seed, n_sites, n_hubs, n_services

    # ---------------------------------------------------------------- sites and links
    def place_sites(self):
        rng = self.rng
        pts: dict[str, tuple[float, float]] = {}
        while len(pts) < self.n_sites:
            x, y = rng.uniform(0, 2000), rng.uniform(0, 1000)
            if all(math.dist((x, y), p) >= 120 for p in pts.values()):
                pts[f"S{len(pts) + 1:02d}"] = (round(x, 1), round(y, 1))
        self.sites = pts
        self.pop = {s: math.exp(rng.gauss(0, 0.8)) for s in pts}

    def fibre_km(self, a, b) -> float:
        return round(1.2 * math.dist(self.sites[a], self.sites[b]), 1)

    def choose_links(self):
        S = list(self.sites)
        g = nx.Graph()
        g.add_nodes_from(S)
        for a, b in itertools.combinations(S, 2):          # Gabriel graph
            pa, pb = self.sites[a], self.sites[b]
            mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
            r = math.dist(pa, pb) / 2
            if all(math.dist(self.sites[c], mid) >= r for c in S if c not in (a, b)):
                if self.fibre_km(a, b) <= 600:
                    g.add_edge(a, b, km=self.fibre_km(a, b))
        cand = sorted(((self.fibre_km(a, b), a, b) for a, b in itertools.combinations(S, 2)
                       if not g.has_edge(a, b) and self.fibre_km(a, b) <= 650))
        # connect, then remove bridges by adding the shortest bypass
        for km, a, b in cand:
            if nx.is_connected(g):
                break
            if not nx.has_path(g, a, b):
                g.add_edge(a, b, km=km)
        if not nx.is_connected(g):
            raise _Infeasible("cannot connect within 650 km")
        for _ in range(100):
            bridges = list(nx.bridges(g))
            if not bridges:
                break
            u, v = bridges[0]
            h = g.copy()
            h.remove_edge(u, v)
            cu = nx.node_connected_component(h, u)
            opts = [(km, a, b) for km, a, b in cand if not g.has_edge(a, b)
                    and ((a in cu) != (b in cu)) and {a, b} != {u, v}]
            if not opts:
                raise _Infeasible("no bypass within 650 km")
            km, a, b = opts[0]
            g.add_edge(a, b, km=km)
        # densify towards about 40 links with the shortest remaining candidates
        for km, a, b in cand:
            if g.number_of_edges() >= 40:
                break
            if not g.has_edge(a, b) and km <= 450 and g.degree(a) < 5 and g.degree(b) < 5:
                g.add_edge(a, b, km=km)
        # degree cap 5: drop the longest edge at an over-degree site while two-edge-connected
        for s in S:
            for (_, a, b) in sorted(((g[s][t]["km"], s, t) for t in list(g[s])), reverse=True):
                if g.degree(s) <= 5:
                    break
                g.remove_edge(a, b)
                if not nx.is_connected(g) or list(nx.bridges(g)):
                    g.add_edge(a, b, km=self.fibre_km(a, b))
        self.g = g
        self.topology = {}
        for i, (a, b) in enumerate(sorted(tuple(sorted(e)) for e in g.edges)):
            self.topology[f"L{i + 1:02d}"] = (a, b, g[a][b]["km"])
            g[a][b]["link"] = f"L{i + 1:02d}"

    # ---------------------------------------------------------------- plant
    def build_plant(self):
        rng = self.rng
        self.oms, self.spans, self.amps = {}, {}, {}
        self.span_order: dict[str, list[str]] = {}       # link -> span ids in order from node a (fwd)
        for lk, (a, b, L) in self.topology.items():
            n = max(1, math.ceil(L / 92))
            raw = [rng.uniform(0.92, 1.08) for _ in range(n)]
            lengths = [round(L * r / sum(raw), 1) for r in raw]
            aerial = [rng.random() < 0.12 for _ in range(n)]
            cable, duct = f"cable.{lk}", f"duct.{lk}"
            for (s, d) in ((a, b), (b, a)):
                oid = f"oms.{s}-{d}"
                o = _build_oms(oid, s, d, lk, lengths if s == a else lengths[::-1], cable, duct,
                               aerial if s == a else aerial[::-1])
                self.oms[oid] = o
                self.spans.update({sp.id: sp for sp in o.spans})
                self.amps.update({am.id: am for am in o.amps})
        for o in self.oms.values():                      # constant-gain commissioning
            for i, e in enumerate(o.elements):
                if hasattr(e, "role") and e.index > 0:
                    e.gain_db = o.elements[i - 1].nominal_loss_db

    def spans_from(self, lk: str, site: str, k: int) -> list[str]:
        """The k-th span counted from `site` on link lk, in both directions."""
        a, b, _ = self.topology[lk]
        other = b if site == a else a
        fwd = self.oms[f"oms.{site}-{other}"].spans
        rev = self.oms[f"oms.{other}-{site}"].spans
        if k >= len(fwd):
            return []
        return [fwd[k].id, rev[len(rev) - 1 - k].id]

    def shared_risk(self):
        rng = self.rng
        self.srlgs: dict[str, SRLG] = {}

        def bearing(s, t):
            (x1, y1), (x2, y2) = self.sites[s], self.sites[t]
            return math.degrees(math.atan2(y2 - y1, x2 - x1)) % 360

        def angle(u, v):
            d = abs(u - v) % 360
            return min(d, 360 - d)

        pairs = []
        for s in self.sites:
            inc = [(bearing(s, t), self.g[s][t]["link"]) for t in self.g[s]]
            for (b1, l1), (b2, l2) in itertools.combinations(inc, 2):
                pairs.append((angle(b1, b2), s, l1, l2))
        pairs.sort()
        used_first = set()
        # metro-exit ducts: first span from the site, links within 35 degrees
        n_duct = 0
        for ang, s, l1, l2 in pairs:
            if ang > 45 or n_duct >= 8:
                break
            if (s, l1) in used_first or (s, l2) in used_first:
                continue
            sp = self.spans_from(l1, s, 0) + self.spans_from(l2, s, 0)
            gid = f"srlg.duct.{s}.{n_duct}"
            known = rng.random() < 0.6
            self.srlgs[gid] = SRLG(gid, "duct", sp, known)
            for sid in sp:
                self.spans[sid].recorded_duct = gid if known else self.spans[sid].duct
                self.spans[sid].duct = gid
            used_first |= {(s, l1), (s, l2)}
            n_duct += 1
        # two long shared cables over the first two spans, never recorded
        n_cab = 0
        for ang, s, l1, l2 in pairs:
            if n_cab >= 2:
                break
            if ang > 30:
                continue
            sp = []
            for k in (0, 1):
                a1, a2 = self.spans_from(l1, s, k), self.spans_from(l2, s, k)
                if not a1 or not a2 or k + 1 >= min(len(self.oms[f"oms.{s}-{self._other(l1, s)}"].spans),
                                                    len(self.oms[f"oms.{s}-{self._other(l2, s)}"].spans)):
                    break
                sp += a1 + a2
            if len(sp) < 8 or any(self.spans[x].cable.startswith("srlg.") for x in sp):
                continue
            gid = f"srlg.cable.{s}.{n_cab}"
            self.srlgs[gid] = SRLG(gid, "cable", sp, False)
            for sid in sp:
                self.spans[sid].recorded_cable = self.spans[sid].cable
                self.spans[sid].cable = gid
            n_cab += 1

    def _other(self, lk, s):
        a, b, _ = self.topology[lk]
        return b if s == a else a

    # ---------------------------------------------------------------- service layer
    def hubs_and_homing(self):
        score = {s: self.pop[s] * self.g.degree(s) for s in self.sites}
        self.hubs = sorted(sorted(self.sites, key=lambda s: -score[s])[:self.n_hubs])
        dist = dict(nx.all_pairs_dijkstra_path_length(self.g, weight="km"))
        self.dist = dist
        self.home, self.home2 = {}, {}
        for s in self.sites:
            order = sorted(self.hubs, key=lambda h: dist[s][h])
            self.home[s] = s if s in self.hubs else order[0]
            self.home2[s] = s if s in self.hubs else order[1]
        hg = nx.Graph()
        for h1, h2 in itertools.combinations(self.hubs, 2):
            if dist[h1][h2] <= 1200:
                hg.add_edge(h1, h2, w=dist[h1][h2] + 300)
        assert nx.is_connected(hg), "hub graph disconnected"
        self.hg = hg

    def demands(self):
        rng = self.rng
        S = list(self.sites)
        pairs = [(a, b) for a, b in itertools.combinations(S, 2)]
        w = [self.pop[a] * self.pop[b] / math.sqrt(self.dist[a][b]) for a, b in pairs]
        out = []
        while len(out) < self.n_services:
            a, b = rng.choices(pairs, weights=w)[0]
            if rng.random() < 0.5:
                a, b = b, a
            r = rng.random()
            rate = "100GE" if r < 0.35 else "10GE" if r < 0.95 else "400GE"
            if rate == "400GE" and self.dist[a][b] > 800:
                continue
            prot = "none"
            if rate == "400GE":
                prot = "och1+1" if rng.random() < 0.5 else "none"
            elif rng.random() < 0.30:
                prot = "sncp"
            out.append((a, b, rate, prot))
        self.demand_list = out

    def _site_route(self, a, b, home) -> list[str]:
        ha, hb = home[a], home[b]
        hubpath = [ha] if ha == hb else nx.shortest_path(self.hg, ha, hb, weight="w")
        seq = ([a] if a != ha else []) + hubpath + ([b] if b != hb else [])
        return [x for i, x in enumerate(seq) if i == 0 or x != seq[i - 1]]

    def _fibre_path(self, a, b, avoid_links: set[str]) -> Optional[list[str]]:
        h = self.g.copy()
        h.remove_edges_from([(u, v) for u, v, d in self.g.edges(data=True) if d["link"] in avoid_links])
        try:
            return nx.shortest_path(h, a, b, weight="km")
        except nx.NetworkXNoPath:
            return None

    def _links_of(self, path: list[str]) -> set[str]:
        return {self.g[path[i]][path[i + 1]]["link"] for i in range(len(path) - 1)}

    def _known_risk_links(self, links: set[str]) -> set[str]:
        """Links that share a *recorded* SRLG with any of `links`."""
        out = set(links)
        for g in self.srlgs.values():
            if not g.known:
                continue
            ls = {self.spans[s].oms and self.oms[self.spans[s].oms].link for s in g.spans}
            if ls & links:
                out |= ls
        return out

    def _alloc(self, a, b, units, avoid: set[str], kind: str) -> tuple[str, bool]:
        """Find or create a lightpath a<->b with free units avoiding links; returns (id, diverse)."""
        for lid in self.lp_by_pair.get(frozenset((a, b)), []):
            lp = self.lps_meta[lid]
            if lp["free"] >= units and not (lp["links"] & avoid):
                lp["free"] -= units
                return lid, True
        path = self._fibre_path(a, b, avoid)
        diverse = path is not None
        if path is None:
            path = self._fibre_path(a, b, set())
        km = sum(self.g[path[i]][path[i + 1]]["km"] for i in range(len(path) - 1))
        rate, mod, baud, cap = _line_for(km)
        lid = f"och{len(self.lps_meta)}"
        self.lps_meta[lid] = {"path": path, "links": self._links_of(path), "free": cap - units,
                              "rate": rate, "mod": mod, "baud": baud, "cap": cap, "km": km, "kind": kind}
        self.lp_by_pair.setdefault(frozenset((a, b)), []).append(lid)
        return lid, diverse

    def route_services(self):
        self.lps_meta, self.lp_by_pair = {}, {}
        self.svc, self.odu = {}, {}
        self.non_diverse = 0
        for i, (a, b, rate, prot) in enumerate(self.demand_list):
            sid = f"svc{i:03d}"
            odu = RATE_ODU[rate]
            u = UNITS[odu]
            if rate == "400GE":
                lid, _ = self._alloc_dedicated(a, b, set(), "express")
                wp = ODUPath(f"odu.{sid}.w", odu, [ODUHop(lid, a, b)], u)
                self.odu[wp.id] = wp
                pp = None
                if prot == "och1+1":
                    lid2, div = self._alloc_dedicated(a, b, self._known_risk_links(self.lps_meta[lid]["links"]), "express")
                    self.non_diverse += (not div)
                    pp = ODUPath(f"odu.{sid}.p", odu, [ODUHop(lid2, a, b)], u)
                    self.odu[pp.id] = pp
                self.svc[sid] = ClientService(sid, lid, rate, a, b, wp.id, pp.id if pp else None, prot)
                continue
            route = self._site_route(a, b, self.home)
            hops, used = [], set()
            for x, y in zip(route, route[1:]):
                kind = "trunk" if x in self.hubs and y in self.hubs else "access"
                lid, _ = self._alloc(x, y, u, set(), kind)
                hops.append(ODUHop(lid, x, y))
                used |= self.lps_meta[lid]["links"]
            wp = ODUPath(f"odu.{sid}.w", odu, hops, u)
            self.odu[wp.id] = wp
            pp = None
            if prot == "sncp":
                avoid = self._known_risk_links(used)
                route2 = self._site_route(a, b, self.home2)
                hops2, ok = [], True
                for x, y in zip(route2, route2[1:]):
                    kind = "trunk" if x in self.hubs and y in self.hubs else "access"
                    lid, div = self._alloc(x, y, u, avoid, kind)
                    ok &= div
                    hops2.append(ODUHop(lid, x, y))
                self.non_diverse += (not ok)
                pp = ODUPath(f"odu.{sid}.p", odu, hops2, u)
                self.odu[pp.id] = pp
            self.svc[sid] = ClientService(sid, hops[0].lightpath, rate, a, b, wp.id,
                                          pp.id if pp else None, prot)

    def _alloc_dedicated(self, a, b, avoid, kind):
        path = self._fibre_path(a, b, avoid)
        diverse = path is not None
        if path is None:
            path = self._fibre_path(a, b, set())
        km = sum(self.g[path[i]][path[i + 1]]["km"] for i in range(len(path) - 1))
        lid = f"och{len(self.lps_meta)}"
        self.lps_meta[lid] = {"path": path, "links": self._links_of(path), "free": 0, "rate": 400,
                              "mod": "DP-16QAM", "baud": 64.0, "cap": 40, "km": km, "kind": kind}
        self.lp_by_pair.setdefault(frozenset((a, b)), []).append(lid)
        return lid, diverse

    # ---------------------------------------------------------------- optical layer
    def assign_spectrum(self):
        use: dict[str, set[int]] = {o: set() for o in self.oms}
        self.lightpaths, self.trx = {}, {}
        order = sorted(self.lps_meta, key=lambda l: -len(self.lps_meta[l]["path"]))
        for lid in order:
            m = self.lps_meta[lid]
            p = m["path"]
            fwd = [f"oms.{p[j]}-{p[j + 1]}" for j in range(len(p) - 1)]
            rev = [f"oms.{p[j + 1]}-{p[j]}" for j in range(len(p) - 1)]
            slot = next((s for s in range(N_SLOTS) if all(s not in use[o] for o in fwd + rev)), None)
            if slot is None:
                raise RuntimeError(f"spectrum exhausted for {lid}")
            for o in fwd + rev:
                use[o].add(slot)
            ends = []
            for site, tag in ((p[0], "a"), (p[-1], "z")):
                tid = f"trx.{lid}.{tag}"
                if site in self.hubs and m["kind"] != "express":
                    t = Transponder(tid, site, m["mod"], m["baud"], kind="otn_line", rate_g=m["rate"],
                                    host=f"otn.{site}")
                elif m["kind"] == "express":
                    t = Transponder(tid, site, m["mod"], m["baud"], kind="transponder", rate_g=m["rate"])
                else:
                    t = Transponder(tid, site, m["mod"], m["baud"], kind="muxponder", rate_g=m["rate"])
                self.trx[tid] = t
                ends.append(tid)
            self.lightpaths[lid] = Lightpath(lid, ends[0], ends[1], p, fwd, slot, rate_g=m["rate"],
                                             kind=m["kind"], units=m["cap"])
        self.slot_use = use


def build_reference_national(seed: int = 24, n_sites: int = 24, n_hubs: int = 8,
                             n_services: int = 400, gap_scale: float = 0.0,
                             gap_calibrated: bool = False, mismatch: int = 0) -> Network:
    """The reference network for `seed`. gap_scale > 0 gives world version 2: hidden true parameter values and
    instrument calibration offsets that the records and the knowledge graph do not hold (world/gap.py)."""
    b = _Builder(seed, n_sites, n_hubs, n_services)
    for _ in range(50):                  # re-place sites until a valid fibre graph exists
        b.place_sites()
        try:
            b.choose_links()
            break
        except _Infeasible:
            continue
    else:
        raise RuntimeError("no feasible site placement")
    b.build_plant()
    b.shared_risk()
    b.hubs_and_homing()
    b.demands()
    b.route_services()
    b.assign_spectrum()
    net = Network(f"ref-national-{n_sites}", sorted(b.sites),
                  {k: (v[0], v[1]) for k, v in b.topology.items()}, b.oms, b.spans, b.amps,
                  b.trx, b.lightpaths, b.svc,
                  sites=b.sites, hubs=b.hubs,
                  otn_switches={f"otn.{h}": OTNSwitch(f"otn.{h}", h) for h in b.hubs},
                  odu_paths=b.odu, srlgs=b.srlgs,
                  link_km={k: v[2] for k, v in b.topology.items()})
    net._build_report = {"non_diverse_protection": b.non_diverse,
                         "oms_fill": {o: len(s) / N_SLOTS for o, s in b.slot_use.items()}}
    from .gap import apply_gap, apply_mismatch
    from . import spectral
    net.spectral = spectral.on()
    return apply_mismatch(apply_gap(net, gap_scale, seed, calibrated=gap_calibrated), mismatch, seed)


def report(net: Network) -> dict:
    """Summary statistics for the build note."""
    from collections import Counter
    import statistics as st
    deg = Counter()
    for a, b in net.links.values():
        deg[a] += 1
        deg[b] += 1
    fill = list(net._build_report["oms_fill"].values())
    hops = [len(net.odu_paths[s.working].hops) for s in net.services.values()]
    return {
        "sites": len(net.nodes), "hubs": len(net.hubs), "links": len(net.links),
        "link_km": (min(net.link_km.values()), round(st.mean(net.link_km.values())), max(net.link_km.values())),
        "degree": (min(deg.values()), max(deg.values())),
        "spans_per_direction": len(net.spans) // 2, "spans_total": len(net.spans),
        "span_km": (min(s.length_km for s in net.spans.values()), max(s.length_km for s in net.spans.values())),
        "aerial_fraction": round(sum(s.aerial for s in net.spans.values()) / len(net.spans), 3),
        "amplifiers": len(net.amps),
        "srlgs": {k: sum(1 for g in net.srlgs.values() if g.kind == k) for k in ("duct", "cable")},
        "srlgs_unknown": sum(1 for g in net.srlgs.values() if not g.known),
        "lightpaths": len(net.lightpaths),
        "lightpath_kinds": dict(Counter(lp.kind for lp in net.lightpaths.values())),
        "lightpath_rates": dict(Counter(lp.rate_g for lp in net.lightpaths.values())),
        "transponder_kinds": dict(Counter(t.kind for t in net.transponders.values())),
        "oms_fill": (round(min(fill), 2), round(st.mean(fill), 2), round(max(fill), 2)),
        "services": len(net.services),
        "service_rates": dict(Counter(s.rate for s in net.services.values())),
        "protection": dict(Counter(s.protection for s in net.services.values())),
        "working_hops": dict(sorted(Counter(hops).items())),
        "non_diverse_protection": net._build_report["non_diverse_protection"],
    }
