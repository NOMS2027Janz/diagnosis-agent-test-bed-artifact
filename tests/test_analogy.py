"""Experience store, grounding and analogy search (build step 6)."""
import re
from pathlib import Path

import pytest

from adagent.harness.cases import context, make_case, sources
from adagent.harness.metrics import expected_hit
from adagent.substrate.lifecycle import build_graph
from adagent.tools.analogy import AnalogySearch
from adagent.tools.experience import ExperienceStore
from adagent.tools.frame import DeterministicFramer
from adagent.tools.grounding import GroundingTool
from adagent.world.faults import CATALOGUE

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def full_store():
    s = ExperienceStore()
    build_graph(context().events, 2190.0, sources(), observers=[s])
    return s


@pytest.fixture(scope="module")
def store(full_store):
    return full_store


def test_stored_cases_are_written_at_closure_and_never_revised(full_store):
    """No hindsight: the store on day 730 from a replay to day 2190 equals a replay stopped at day 730, so later
    changes to the network do not alter what earlier closures recorded."""
    early = ExperienceStore()
    build_graph(context().events, 730.0, sources(), observers=[early])
    late = full_store.as_of(730.0)
    assert len(early.cases) > 100
    key = lambda c: (c.id, c.relation, tuple(sorted(c.signature.items())), c.mode, c.location, c.verification,
                     c.weight, tuple(c.updates))
    assert [key(c) for c in early.cases] == [key(c) for c in late.cases]


def test_followups_confirm_complete_or_refute_and_are_dated(full_store):
    truth = {i.id: i.truth for i in context().incidents}
    upd = [c for c in full_store.cases if c.updates]
    assert len(upd) > 50
    kinds = set()
    for c in upd:
        assert all(d > c.recorded_day for d, _ in c.updates)
        q = truth[c.id]["ticket_quality"]
        kinds.add(q)
        if q == "wrong":
            assert c.verification == "refuted" and c.weight == 0.0
        else:
            assert c.verification == "repair_confirmed" and c.mode == truth[c.id]["fault_mode"]
    assert kinds == {"accurate", "coarse", "wrong"}
    c = upd[0]                                  # before its follow-up, the case is as closed
    before = full_store.as_of(c.updates[0][0] - 1e-6)
    b = next(x for x in before.cases if x.id == c.id)
    assert b.verification == "engineer_concluded" and not b.updates


def test_base_rates_follow_followups():
    """Base rates on a day count each ticket once, with the explanation and weight it then had."""
    from adagent.substrate.lifecycle import VERIFICATION_WEIGHT as W
    ev = context().events
    day = 2190.0
    st = build_graph(ev, day, sources())
    expect = {}
    for ty, mode, ver in st.ticket_state.values():
        if ty and mode and W[ver]:
            expect.setdefault(ty, {}).setdefault(mode, 0.0)
            expect[ty][mode] += W[ver]
    for ty in st.kg.nodes_of("EquipmentType"):
        got = st.kg.g.nodes[ty].get("cause_counts", {})
        want = expect.get(ty, {})
        assert set(got) == set(want), ty
        assert all(abs(got[m] - want[m]) < 1e-6 for m in got), ty


def test_alarms_on_elements_unknown_at_closure_are_kept(full_store):
    assert any("Unknown" in k for c in full_store.cases for k in c.signature)


def test_analogy_tools_name_no_fault_mode():
    for f in ("experience.py", "grounding.py", "analogy.py"):
        text = (ROOT / "src/adagent/tools" / f).read_text()
        assert not [m for m in CATALOGUE if re.search(rf"\b{m}\b", text)], f


def test_store_holds_only_what_tickets_record(store):
    assert len(store.cases) > 1000
    truth = {i.id: i.truth for i in context().incidents}
    for c in store.cases[:200]:
        assert not hasattr(c, "truth")
        if c.mode and truth[c.id]["fault_mode"] != c.mode:
            break
    else:
        pytest.fail("expected some recorded explanations to differ from the truth (wrong tickets)")
    assert {c.verification for c in store.cases} >= {"repair_confirmed", "engineer_concluded"}


def test_analogy_finds_precedented_equipment_faults(store):
    for seed in (0, 7, 13, 15):                # client port, transmitter, card, pump failures
        c = make_case(2190.0, seed)
        fr = DeterministicFramer(c.substrate).build(c.question)
        kg = c.substrate.kg
        p = AnalogySearch(kg, store, GroundingTool(kg)).run(fr)
        sc = {x.hypothesis.id: x.prior_mass for x in p.candidates}
        assert expected_hit(sc, c.truth.fault.key(), 1) == 1.0, (c.id, list(sc)[:3])


def test_unprecedented_faults_leave_analogy_with_nothing(store):
    c = make_case(2190.0, 24)                  # a cross-connect misconnection: none in the history
    fr = DeterministicFramer(c.substrate).build(c.question)
    kg = c.substrate.kg
    p = AnalogySearch(kg, store, GroundingTool(kg)).run(fr)
    assert c.truth.fault.key() not in {x.hypothesis.id for x in p.candidates}


def test_grounding_keeps_only_documented_locations(store):
    c = make_case(2190.0, 15)
    fr = DeterministicFramer(c.substrate).build(c.question)
    kg = c.substrate.kg
    g = GroundingTool(kg)
    locs = g.ground(fr, c.truth.fault.mode, element=c.truth.fault.targets[0])
    assert locs == [c.truth.fault.targets[0]]
    assert g.ground(fr, c.truth.fault.mode, element="no.such.element") == []
