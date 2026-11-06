"""Lifecycle processes and the knowledge graph they build (design note §2 checks, §5.1)."""
import ast
from pathlib import Path

import pytest

from adagent.substrate.lifecycle import Sources, TIER_ORDER, build_graph
from adagent.world.history import generate_history
from adagent.world.reference import build_reference_national

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def hist():
    return generate_history(build_reference_national(24), seed=1)


@pytest.fixture(scope="module")
def mature(hist):
    return build_graph(hist.events, 2190.0)


def test_agent_side_never_imports_the_world():
    """Independence: the substrate reads event records and knowledge files, never simulator code."""
    for f in (ROOT / "src/adagent/substrate").glob("*.py"):
        tree = ast.parse(f.read_text())
        mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        mods += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any("world" in m for m in mods), (f.name, mods)


def test_every_node_traces_to_an_event(mature):
    """Evolution: each node in the graph was written by a logged lifecycle event or initial knowledge."""
    written = {p.target for p in mature.kg.log if p.op in ("put_node", "update")}
    assert set(mature.kg.g.nodes) <= written


def test_every_equipment_type_is_documented(mature):
    kg = mature.kg
    for ty in kg.nodes_of("EquipmentType"):
        assert kg.g.nodes[ty]["documented"], ty


def test_graph_follows_recorded_time(hist):
    """Content appears only once recorded: lagged records are absent until they arrive."""
    lagged = next(e for e in hist.events if e.kind == "lightpath_provisioned"
                  and e.t_recorded is not None and e.t_recorded - e.t > 1)
    before = build_graph(hist.events, (e_t := lagged.t + 0.5)).kg
    after = build_graph(hist.events, lagged.t_recorded + 0.01).kg
    lp = lagged.payload["lightpath"]
    assert not before.has(lp) and after.has(lp)
    assert e_t < lagged.t_recorded


def test_unregistered_instruments_never_appear(hist):
    e = next(e for e in hist.events if e.kind == "instrument_tier_deployed" and e.t_recorded is None)
    kg = build_graph(hist.events, e.t + 0.01).kg
    recorded = kg.g.nodes[f"site.{e.payload['site']}"]["tier"]
    assert TIER_ORDER.index(recorded) < TIER_ORDER.index(e.payload["tier"])


def test_unrecorded_shared_risk_is_absent_from_the_graph(hist, mature):
    net, kg = hist.net, mature.kg
    for g in net.srlgs.values():
        a, b = g.spans[0], g.spans[-1]
        if net.spans[a].oms == net.spans[b].oms:
            continue
        if g.known:
            assert b in kg.shared_risk(a)
        else:
            assert b not in kg.shared_risk(a)


def test_vendor_documentation_gaps_carry_through(mature):
    kg = mature.kg
    va = next(a for a in kg.nodes_of("Amplifier") if kg.type_of(a) == "VA-OA-ILA")
    vb = next(a for a in kg.nodes_of("Amplifier") if kg.type_of(a) == "VB-OA-ILA")
    assert "AMP_NF_RISE" in {m for m, _ in kg.fault_modes_for(va)}
    assert "AMP_NF_RISE" not in {m for m, _ in kg.fault_modes_for(vb)}


def test_instruments_follow_recorded_tier(mature):
    kg = mature.kg
    for site in kg.nodes_of("Site"):
        tier = kg.g.nodes[site]["tier"]
        for el, _ in kg.inn(site, "locatedAt"):
            for _, a in kg.instruments_on(el):
                assert TIER_ORDER.index(a["tier"]) <= TIER_ORDER.index(tier)


def test_closure_updates_documented_base_rates(mature):
    kg = mature.kg
    counts = [kg.g.nodes[t].get("cause_counts", {}) for t in kg.nodes_of("EquipmentType")]
    assert sum(sum(c.values()) for c in counts) > 100
    assert len(mature.closed_tickets) > 1000
    assert all("truth" not in t for t in mature.closed_tickets)


def test_knowledge_sources_load():
    s = Sources.load()
    assert len(s.types) == 28          # 26 vendor types and 2 plant types


def test_graph_instruments_match_lake_streams(hist, mature):
    """Every instrument the graph records produces its documented streams in the lake."""
    from adagent.world.simulator import WorldSim
    day = 2190.0
    w = WorldSim(hist.network_at(day), seed=5, deployment=hist.deployment_at(day))
    lake = w.run(0.0, 120.0)
    kg = mature.kg
    have = {(ob, el.split("|")[0]) for (ob, el) in lake._by_obs_el}
    missing = []
    for inst in kg.nodes_of("Instrument"):
        a = kg.g.nodes[inst]
        if a.get("per_channel"):                     # a channel monitor reports only channels present
            oms = a["lake_element"].split(":", 1)[1]
            if not kg.inn(oms, "carriedBy"):
                continue
        for ob in a["observes"]:
            if ob.endswith("_pm15") and not lake.query(observable=ob):   # 15-minute bins need a bin edge
                continue
            if (ob, a["lake_element"]) not in have:
                missing.append((inst, ob))
    assert not missing, missing[:10]


def test_types_are_onboarded_before_their_first_installation(hist):
    first_install = {}
    onboard = {}
    for e in hist.events:
        if e.kind == "equipment_installed" and e.payload.get("type"):
            first_install.setdefault(e.payload["type"], e.t)
            first_install[e.payload["type"]] = min(first_install[e.payload["type"]], e.t)
        if e.kind == "equipment_type_onboarded":
            onboard[e.payload["type"]] = e.t
    assert all(onboard[t] < first_install[t] for t in first_install)
