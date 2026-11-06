"""Baseline: the LLM diagnoses and decides alone (campaign stage 1), tested offline with a stand-in for the model."""
import re

import pytest

from adagent.harness.cases import make_case
from adagent.harness.cases2 import sources
from adagent.tools.frame import DeterministicFramer
from adagent.tools.grounding import GroundingTool
from adagent.tools.llm_alone import SYSTEM, SYSTEM_SINGLE, THETA_COMMIT, LLMAlone


class StandIn:
    model = "stand-in"

    def __init__(self, answers):
        self.answers, self.seen = list(answers), []

    def parse(self, role, pv, system, user, schema):
        self.seen.append((system, user))
        a = self.answers.pop(0)
        return schema.model_validate(a(user) if callable(a) else a)

    def records(self):
        return []


@pytest.fixture(scope="module")
def pump():
    c = make_case(2190.0, 15)                      # an amplifier pump failure
    fr = DeterministicFramer(c.substrate).build(c.question)
    return c, fr


def _turn(user, action, ci=None, ki=None):
    s = re.search(r"\n  (O\d+) t=[\d.]+ alarm AMP_FAIL ", user).group(1)
    return {"assessment": "x", "probability_other": 0.05, "action": action, "commit_index": ci, "check_index": ki,
            "proposals": [
                {"fault_mode": "AMP_PUMP_FAIL", "location_kind": "term", "term": "this_unit", "anchor_symptom": s,
                 "anchor_service": None, "element": None, "probability": 0.9, "rationale": "r"},
                {"fault_mode": "FIBRE_CUT", "location_kind": "term", "term": "upstream_span", "anchor_symptom": s,
                 "anchor_service": None, "element": None, "probability": 0.05, "rationale": "r"}]}


def test_prompts_state_the_commit_threshold_and_single_shot_offers_no_checks():
    assert f"{THETA_COMMIT:.0%}" in SYSTEM and '"check"' in SYSTEM
    assert '"check"' not in SYSTEM_SINGLE.split("- action:")[1].split("\n")[0]


def test_single_shot_commit_grounds_to_the_truth(pump):
    c, fr = pump
    la = LLMAlone(StandIn([lambda u: _turn(u, "commit", ci=0)]), c.substrate, sources(), fr,
                  GroundingTool(c.substrate.kg), with_checks=False)
    dec, hyps, rec = la.run()
    assert dec["outcome"] == "commit" and hyps[dec["hypothesis"]].id == c.truth.fault.key()
    assert rec["checks"] == [] and len(rec["turns"]) == 1


def test_dont_know_reports_the_set(pump):
    c, fr = pump
    la = LLMAlone(StandIn([lambda u: _turn(u, "dont_know")]), c.substrate, sources(), fr,
                  GroundingTool(c.substrate.kg), with_checks=False)
    dec, hyps, _ = la.run()
    assert dec["outcome"] == "set" and set(dec["members"]) == set(hyps) and not dec["cause_probably_unconsidered"]


def test_with_checks_runs_the_chosen_check_then_commits(pump):
    c, fr = pump
    client = StandIn([lambda u: _turn(u, "check", ki=0), lambda u: _turn(u, "commit", ci=0)])
    la = LLMAlone(client, c.substrate, sources(), fr, GroundingTool(c.substrate.kg), with_checks=True)
    dec, _, rec = la.run()
    assert len(rec["checks"]) == 1 and dec["outcome"] == "commit"
    assert "Checks available" in client.seen[0][1] and rec["checks"][0]["check"] in client.seen[1][1]


def test_a_commitment_to_several_locations_is_clarified_once(pump):
    c, fr = pump

    def turn(u):
        s = re.search(r"\n  (O\d+) t=[\d.]+ alarm AMP_FAIL ", u).group(1)
        return {"assessment": "x", "probability_other": 0.05, "action": "commit", "commit_index": 0,
                "check_index": None, "proposals": [
                    {"fault_mode": "FIBRE_CUT", "location_kind": "term", "term": "upstream_on_path",
                     "anchor_symptom": s, "anchor_service": None, "element": None, "probability": 0.9,
                     "rationale": "r"}]}
    client = StandIn([turn, {"assessment": "y", "action": "choose", "index": 0}])
    la = LLMAlone(client, c.substrate, sources(), fr, GroundingTool(c.substrate.kg), with_checks=False)
    dec, hyps, rec = la.run()
    n = len(rec["turns"][0]["proposals"][0]["grounded"])
    if n > 1:
        assert dec["outcome"] == "commit" and "clarify" in rec and len(client.seen) == 2
    else:
        assert "clarify" not in rec
