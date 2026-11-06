"""Invariants of the reference network ref-national-24 (design note rev 3, §4.1)."""
from collections import defaultdict

import networkx as nx
import pytest

from adagent.world.physics import evaluate
from adagent.world.reference import UNITS, build_reference_national, report


@pytest.fixture(scope="module")
def net():
    return build_reference_national(24)


def test_deterministic():
    assert report(build_reference_national(24)) == report(build_reference_national(24))


def test_fibre_graph(net):
    g = nx.Graph([(a, b) for a, b in net.links.values()])
    assert len(net.nodes) == 24 and nx.is_connected(g)
    assert not list(nx.bridges(g)), "every link must have a bypass"
    assert max(dict(g.degree).values()) <= 5 and min(dict(g.degree).values()) >= 2
    assert all(60 <= s.length_km <= 100 for s in net.spans.values())


def test_spectrum_has_no_conflicts(net):
    seen = defaultdict(set)
    for lp in net.lightpaths.values():
        nodes = lp.nodes
        rev = [f"oms.{nodes[j + 1]}-{nodes[j]}" for j in range(len(nodes) - 1)]
        for o in lp.oms_path + rev:
            assert lp.slot not in seen[o], (lp.id, o)
            seen[o].add(lp.slot)


def test_every_channel_has_margin(net):
    snap = evaluate(net)
    assert len(snap.rx) == 2 * len(net.lightpaths)
    assert min(r.margin_db for r in snap.rx.values()) > 1.0


def test_odu_paths_are_contiguous_and_within_capacity(net):
    used = defaultdict(int)
    for svc in net.services.values():
        for pid in filter(None, (svc.working, svc.protect)):
            p = net.odu_paths[pid]
            assert p.hops[0].a == svc.src and p.hops[-1].z == svc.dst
            for h1, h2 in zip(p.hops, p.hops[1:]):
                assert h1.z == h2.a
            for h in p.hops:
                lp = net.lightpaths[h.lightpath]
                assert {h.a, h.z} == {lp.nodes[0], lp.nodes[-1]}
                used[h.lightpath] += UNITS[p.odu]
    for lid, u in used.items():
        assert u <= net.lightpaths[lid].units, lid


def test_service_layer_shape(net):
    r = report(net)
    assert r["services"] == 400 and r["hubs"] == 8
    assert 0.2 <= r["protection"].get("sncp", 0) / r["services"] <= 0.4
    assert sum(v for k, v in r["working_hops"].items() if k >= 2) > 150   # many multi-hop ODU paths
    for svc in net.services.values():
        if svc.protection == "sncp":
            w = {h.lightpath for h in net.odu_paths[svc.working].hops}
            p = {h.lightpath for h in net.odu_paths[svc.protect].hops}
            assert w != p


def test_shared_risk_some_unrecorded(net):
    unknown = [g for g in net.srlgs.values() if not g.known]
    known = [g for g in net.srlgs.values() if g.known]
    assert unknown and known
    for g in unknown:
        for sid in g.spans:
            sp = net.spans[sid]
            recorded = sp.recorded_duct if g.kind == "duct" else sp.recorded_cable
            assert recorded is not None and recorded != g.id
