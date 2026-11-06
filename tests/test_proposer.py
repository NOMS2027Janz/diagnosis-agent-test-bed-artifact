"""LLM proposer (build step 7), tested offline with a stand-in for the model."""
import re
from pathlib import Path

import pytest

from adagent.harness.cases import make_case
from adagent.tools.frame import DeterministicFramer
from adagent.tools.grounding import GroundingTool
from adagent.tools.proposer_llm import SYSTEM, LLMProposer
from adagent.world.faults import CATALOGUE

ROOT = Path(__file__).resolve().parents[1]


class StandIn:
    """Returns a fixed answer in the requested schema; records what it was shown."""
    model = "stand-in"

    def __init__(self, answer):
        self.answer, self.seen = answer, None

    def parse(self, role, pv, system, user, schema):
        self.seen = (system, user, schema)
        ans = self.answer(user) if callable(self.answer) else self.answer
        return schema.model_validate(ans)


@pytest.fixture(scope="module")
def pump():
    c = make_case(2190.0, 15)                      # an amplifier pump failure
    fr = DeterministicFramer(c.substrate).build(c.question)
    return c, fr


def _ref(user, alarm):
    m = re.search(rf"\n  (O\d+) t=[\d.]+ alarm {alarm} ", user)
    return m.group(1)


def test_proposer_names_no_fault_mode():
    text = (ROOT / "src/adagent/tools/proposer_llm.py").read_text()
    assert not [m for m in CATALOGUE if re.search(rf"\b{m}\b", text)]


def test_brief_offers_only_documented_modes_and_no_truth(pump):
    c, fr = pump
    kg = c.substrate.kg
    p = LLMProposer(None, kg, GroundingTool(kg))
    user, refs, modes, terms = p.prompt(fr)
    documented = {m for el in p.case_equipment(fr) for m, _ in kg.fault_modes_for(el)}
    assert set(modes) == documented
    assert "upstream_on_path" in terms and "far_end_transmitter" in terms
    low = (SYSTEM + user).lower()
    for word in ("truth", "ticket_quality", "simulat", "inject", c.truth.fault.mode.lower() + "@"):
        assert word not in low


def test_schema_is_strict(pump):
    c, fr = pump
    kg = c.substrate.kg
    p = LLMProposer(None, kg, GroundingTool(kg))
    _, _, modes, terms = p.prompt(fr)
    js = p.schema(modes, terms).model_json_schema()
    for name, d in [("Proposals", js)] + list(js["$defs"].items()):
        assert set(d["required"]) == set(d["properties"]), name


def test_a_term_anchored_on_a_symptom_grounds_to_the_unit(pump):
    c, fr = pump
    kg = c.substrate.kg

    def answer(user):
        s = _ref(user, "AMP_FAIL")
        return {"assessment": "x", "probability_other": 0.1, "proposals": [
            {"fault_mode": "AMP_PUMP_FAIL", "location_kind": "term", "term": "this_unit", "anchor_symptom": s,
             "anchor_service": None, "element": None, "probability": 0.7, "rationale": "r"},
            {"fault_mode": "FIBRE_CUT", "location_kind": "term", "term": "upstream_span", "anchor_symptom": s,
             "anchor_service": None, "element": None, "probability": 0.2, "rationale": "r"}]}
    p = LLMProposer(StandIn(answer), kg, GroundingTool(kg))
    out = p.run(fr)
    sc = {x.hypothesis.id: x.prior_mass for x in out.candidates}
    assert sc[c.truth.fault.key()] == pytest.approx(0.7)
    assert sum(sc.values()) + out.residual_mass == pytest.approx(1.0)
    span = [k for k in sc if k.startswith("FIBRE_CUT@")]
    assert len(span) == 1 and kg.cls(span[0].split("@")[1]) == "FibreSpan"


def test_ungroundable_proposals_go_to_the_residual(pump):
    c, fr = pump
    kg = c.substrate.kg
    ans = {"assessment": "x", "probability_other": 0.0, "proposals": [
        {"fault_mode": "AMP_PUMP_FAIL", "location_kind": "element", "term": None, "anchor_symptom": None,
         "anchor_service": None, "element": "no.such.element", "probability": 0.5, "rationale": "r"},
        {"fault_mode": "FIBRE_CUT", "location_kind": "term", "term": "this_unit", "anchor_symptom": "S9999",
         "anchor_service": None, "element": None, "probability": 0.3, "rationale": "r"},
        {"fault_mode": "CLIENT_PORT_FAIL", "location_kind": "element", "term": None, "anchor_symptom": None,
         "anchor_service": None, "element": c.truth.fault.targets[0], "probability": 0.2, "rationale": "r"}]}
    p = LLMProposer(StandIn(ans), kg, GroundingTool(kg))
    out = p.run(fr)
    assert out.candidates == [] and out.residual_mass == pytest.approx(1.0)
    notes = [x["note"] for x in p.trace["proposals"]]
    assert notes[0].startswith("unknown element") and notes[1].startswith("unknown anchor")
    assert "does not document" in notes[2]


def test_stated_probabilities_above_one_are_scaled(pump):
    c, fr = pump
    kg = c.substrate.kg
    t = c.truth.fault.targets[0]
    ans = {"assessment": "x", "probability_other": 0.5, "proposals": [
        {"fault_mode": "AMP_PUMP_FAIL", "location_kind": "element", "term": None, "anchor_symptom": None,
         "anchor_service": None, "element": t, "probability": 1.5, "rationale": "r"}]}
    out = LLMProposer(StandIn(ans), kg, GroundingTool(kg)).run(fr)
    assert out.candidates[0].prior_mass == pytest.approx(1 / 1.5)
    assert out.residual_mass == pytest.approx(0.5 / 1.5)


def test_whole_roadm_named_for_a_channel_fault_becomes_its_channels():
    c = make_case(2190.0, 10)                      # a WSS attenuation drift on one channel
    fr = DeterministicFramer(c.substrate).build(c.question)
    kg = c.substrate.kg
    loc = c.truth.fault.targets[0]
    roadm = f"roadm.{kg.g.nodes[loc.split('|')[0]]['dst']}"
    locs = GroundingTool(kg).ground(fr, c.truth.fault.mode, element=roadm)
    assert loc in locs and all("|" in x for x in locs)


def test_shared_names_one_element_by_reference():
    c = make_case(2190.0, 9)                       # a pulled patch: many services share the OMS
    fr = DeterministicFramer(c.substrate).build(c.question)
    kg = c.substrate.kg
    target = c.truth.fault.targets[0]
    assert target in fr.shared_elements

    def answer(user):
        import re as _re
        e = _re.search(rf"\n  (E\d+) {_re.escape(target)}:", user).group(1)
        return {"assessment": "x", "probability_other": 0.1, "proposals": [
            {"fault_mode": c.truth.fault.mode, "location_kind": "shared", "term": None, "anchor_symptom": None,
             "anchor_service": None, "element": e, "probability": 0.9, "rationale": "r"}]}
    out = LLMProposer(StandIn(answer), kg, GroundingTool(kg)).run(fr)
    assert [x.hypothesis.id for x in out.candidates] == [c.truth.fault.key()]


def test_undocumented_modes_are_kept_only_when_asked_and_at_reduced_weight():
    c = make_case(2190.0, 1)                       # a noise-figure rise on an amplifier whose type omits it
    fr = DeterministicFramer(c.substrate).build(c.question)
    kg = c.substrate.kg
    t = c.truth.fault.targets[0]
    assert c.truth.fault.mode not in {m for m, _ in kg.fault_modes_for(t)}
    assert GroundingTool(kg).ground(fr, c.truth.fault.mode, element=t) == []
    g = GroundingTool(kg, undocumented_weight=0.3)
    assert g.ground_weighted(fr, c.truth.fault.mode, element=t) == [(t, 0.3)]
    assert g.ground_weighted(fr, "CLIENT_PORT_FAIL", element=t) == []       # not documented for the class at all
    ans = {"assessment": "x", "probability_other": 0.0, "proposals": [
        {"fault_mode": c.truth.fault.mode, "location_kind": "element", "term": None, "anchor_symptom": None,
         "anchor_service": None, "element": t, "probability": 1.0, "rationale": "r"}]}
    out = LLMProposer(StandIn(ans), kg, g).run(fr)
    assert out.candidates[0].prior_mass == pytest.approx(0.3) and out.residual_mass == pytest.approx(0.7)


def test_a_site_named_for_its_node_equipment_grounds_to_its_channels():
    c = make_case(2190.0, 10)                      # a WSS attenuation drift at a ROADM
    fr = DeterministicFramer(c.substrate).build(c.question)
    kg = c.substrate.kg
    loc = c.truth.fault.targets[0]
    site = kg.g.nodes[loc.split("|")[0]]["dst"]
    ans = {"assessment": "x", "probability_other": 0.0, "proposals": [
        {"fault_mode": c.truth.fault.mode, "location_kind": "element", "term": None, "anchor_symptom": None,
         "anchor_service": None, "element": site, "probability": 1.0, "rationale": "r"}]}
    out = LLMProposer(StandIn(ans), kg, GroundingTool(kg)).run(fr)
    ids = [x.hypothesis.id for x in out.candidates]
    assert c.truth.fault.key() in ids and all("|" in i for i in ids)
