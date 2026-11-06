"""Case frames (design note §6.1): deterministic builder, generic change detection, LLM framer offline."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from adagent.harness.cases import Question, context, make_case, sources
from adagent.harness.frame_score import score_frame
from adagent.substrate.access import Substrate
from adagent.substrate.lifecycle import build_graph
from adagent.tools.frame import DeterministicFramer, role_text
from adagent.tools.frame_llm import LLMFramer, LLMFrameOut
from adagent.world.faults import CATALOGUE
from adagent.world.simulator import WorldSim

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cases():
    return [make_case(2190.0, s) for s in (0, 3, 4, 7)]


def test_framing_code_knows_no_fault_modes():
    """Independence: the framing tools and the change detector name no fault mode."""
    for f in ("src/adagent/tools/frame.py", "src/adagent/tools/frame_llm.py", "src/adagent/substrate/changes.py"):
        text = (ROOT / f).read_text()
        named = [m for m in CATALOGUE if re.search(rf"\b{m}\b", text)]
        assert not named, (f, named)


def test_deterministic_frames_find_onset_scope_and_services(cases):
    for c in cases:
        fr = DeterministicFramer(c.substrate).build(c.question)
        s = score_frame(fr, c.truth)
        assert s["location_in_scope"], (c.id, s)
        assert s["onset_error_s"] <= 30, (c.id, s)
        assert s["down_precision_recall"] == (1.0, 1.0), (c.id, s)
        assert s["hit_precision_recall"][1] == 1.0, (c.id, s)
        assert "Symptoms" in role_text(fr, c.substrate.kg)


def test_no_fault_gives_almost_no_changes():
    h = context()
    day = 2190.0
    kg = build_graph(h.events, day, sources()).kg
    net = h.network_at(day)
    total = 0
    for seed in range(3):
        sub = Substrate(kg, WorldSim(net, seed=seed, deployment=h.deployment_at(day)).run(0, 1200), now=1200, day=day)
        fr = DeterministicFramer(sub).build(Question("customer", "x", sorted(net.services)[seed * 50], 600))
        total += len(fr.symptoms)
    assert total <= 6


class _FakeSession:
    def __init__(self, final):
        self.turn, self.final, self.got_results = 0, final, False

    def _calls(self):
        self.turn += 1
        if self.turn == 1:
            return [SimpleNamespace(id="c1", name="list_alarms", arguments=json.dumps({"t_from": 0, "t_to": 1200}))]
        if self.turn == 2:
            return [SimpleNamespace(id="c2", name="new_errored_seconds", arguments=json.dumps({"after": 500}))]
        return []

    def start(self, user):
        return self._calls()

    def submit(self, results):
        self.got_results = bool(results)
        return self._calls()

    def finish(self, instruction, schema):
        assert self.got_results
        return schema.model_validate(self.final)


class _FakeClient:
    """Scripted stand-in for the LLM: two tool turns, then the final frame."""
    model = "fake"

    def __init__(self, final: dict):
        self.final = final

    def tool_session(self, role, pv, system, tools):
        return _FakeSession(self.final)


def test_llm_framer_runs_tools_and_wraps_the_frame(cases):
    c = cases[0]
    final = {"onset_estimate": 600.0, "symptoms": [], "services": [{"service": c.question.anchor, "state": "down", "on_protection": None, "evidence": []}],
             "affected_elements": [], "shared_elements": [], "quiet_instruments": [], "silent_streams": [],
             "unknown_sources_count": 0, "maintenance": [], "scope": [], "notes": "scripted"}
    fr = LLMFramer(_FakeClient(final), c.substrate)
    frame = fr.build(c.question)
    assert frame.builder == "llm:fake" and frame.services[0].state == "down"
    assert [t["tool"] for t in fr.trace] == ["list_alarms", "new_errored_seconds"]
    assert LLMFrameOut.model_json_schema()["required"]


def test_frame_lists_each_symptom_once():
    """An instrument recorded on two elements (a channel monitor on a ROADM and on its line) is examined once."""
    from collections import Counter
    from adagent.harness.cases import make_case
    for seed in (10, 15):
        c = make_case(2190.0, seed)
        fr = DeterministicFramer(c.substrate).build(c.question)
        k = Counter((s.kind, s.observable, s.element, s.t, s.detail) for s in fr.symptoms)
        assert max(k.values()) == 1
