"""OTN service layer on the reference network: ODU paths, SNCP, ODU-layer alarms, deployment."""
import collections

import pytest

from adagent.world.faults import Event
from adagent.world.otn import client_port
from adagent.world.reference import build_reference_national
from adagent.world.simulator import WorldSim


@pytest.fixture(scope="module")
def net():
    return build_reference_national(24)


def _lps_through_spans(net, spans):
    oms = {net.spans[s].oms for s in spans}
    out = set()
    for lp in net.lightpaths.values():
        rev = [f"oms.{lp.nodes[j + 1]}-{lp.nodes[j]}" for j in range(len(lp.nodes) - 1)]
        if oms & set(lp.oms_path + rev):
            out.add(lp.id)
    return out


def _run(net, events, tier="baseline", **kw):
    w = WorldSim(net, seed=3, tier=tier, **kw)
    for ev in events:
        w.inject(ev)
    return w, w.run(0, 1200)


def test_cut_unprotected_down_protected_switch(net):
    use = collections.Counter(o for lp in net.lightpaths.values() for o in lp.oms_path)
    span = net.oms[use.most_common(1)[0][0]].spans[0].id
    w, lake = _run(net, [Event("FIBRE_CUT", span, {}, onset=600)])
    hit = _lps_through_spans(net, [span])
    sf = {o.element for o in lake.query(observable="CLIENT_SF")}
    act = {o.element for o in lake.query(observable="PROT_ACTIVE")}
    n_unprot = n_switch = 0
    for sid, s in net.services.items():
        w_lps = {h.lightpath for h in net.odu_paths[s.working].hops}
        if not w_lps & hit:
            continue
        ports = {client_port(sid, s.src), client_port(sid, s.dst)}
        if s.protection == "none":
            n_unprot += 1
            assert ports & sf, sid                       # at least the direction through the cut fails
        elif s.protection == "sncp":
            p_lps = {h.lightpath for h in net.odu_paths[s.protect].hops}
            if not p_lps & hit:
                n_switch += 1
                assert ports & act and not ports & sf, sid
    assert n_unprot > 0 and n_switch > 0
    assert lake.query(source="event", observable="PROT_SWITCH")


def test_hidden_shared_duct_defeats_protection(net):
    """Cutting every span of an unrecorded shared-risk group can take both SNCP paths down."""
    for g in net.srlgs.values():
        if g.known:
            continue
        hit = _lps_through_spans(net, g.spans)
        both = [sid for sid, s in net.services.items() if s.protection == "sncp"
                and {h.lightpath for h in net.odu_paths[s.working].hops} & hit
                and {h.lightpath for h in net.odu_paths[s.protect].hops} & hit]
        if not both:
            continue
        evs = [Event("FIBRE_CUT", sp, {}, onset=600) for sp in g.spans]
        _, lake = _run(net, evs)
        sf = {o.element for o in lake.query(observable="CLIENT_SF")}
        s = net.services[both[0]]
        assert {client_port(both[0], s.src), client_port(both[0], s.dst)} & sf
        return
    pytest.skip("no protected service crosses an unrecorded shared-risk group in this seed")


def test_cross_connect_fault_is_optically_silent(net):
    sid, s = next((k, v) for k, v in net.services.items()
                  if v.protection == "none" and len(net.odu_paths[v.working].hops) >= 2)
    mid = net.odu_paths[s.working].hops[1].a
    _, lake = _run(net, [Event("XC_MISCONNECT", f"otn.{mid}|{s.working}", {}, onset=600)])
    types = {o.observable for o in lake.query(source="alarm")}
    assert "ODU_TIM" in types and "CLIENT_SF" in types
    assert not types & {"OCH_LOS", "OTU_LOF", "AMP_INPUT_LOS", "OMS_LOS_P"}


def test_deployment_by_site_gates_streams(net):
    site = net.hubs[0]
    dep = {s: "baseline" for s in net.nodes}
    dep[site] = "coherent"
    w, lake = _run(net, [], tier="baseline", deployment=dep)
    els = lake.elements("est_osnr")
    assert els and all(w.site_of(e) == site for e in els)
    assert all(len(o.value) == 60 and o.period == 1.0 for o in lake.query(observable="est_osnr"))
    assert not lake.query(observable="amp_input_power", element=next(
        a for a in net.amps if w.site_of(a) != site))
