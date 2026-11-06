"""Troubleshooting procedures (build step 8): one content, two forms, one executor."""
import re
from pathlib import Path

import pytest

from adagent.harness.cases import context, make_case, sources
from adagent.harness.metrics import expected_hit
from adagent.substrate.lifecycle import build_graph
from adagent.tools.frame import DeterministicFramer
from adagent.tools.grounding import GroundingTool
from adagent.tools.procedures import ProcedureLibrary, ProcedureTool
from adagent.tools.proposer_llm import LLMProposer
from adagent.world.faults import CATALOGUE

ROOT = Path(__file__).resolve().parents[1]


def test_procedure_code_names_no_fault_mode_or_alarm():
    text = (ROOT / "src/adagent/tools/procedures.py").read_text()
    assert not [m for m in CATALOGUE if re.search(rf"\b{m}\b", text)]
    for alarm in ("OCH_LOS", "AMP_INPUT_LOS", "CLIENT_SF", "ODU_TIM"):
        assert alarm not in text


def test_the_two_forms_hold_the_same_procedures():
    src = sources()
    tool = ProcedureLibrary.from_sources(src)
    graph = ProcedureLibrary.from_graph(build_graph(context().events, 100.0, src, procedures_in_graph=True).kg)
    assert len(tool.procedures) >= 15
    assert tool.canonical() == graph.canonical()


def test_tool_arm_graph_has_no_procedures():
    kg = build_graph(context().events, 100.0, sources()).kg
    assert not kg.nodes_of("Procedure") and not kg.nodes_of("ProcedureStep")


def test_every_procedure_reference_resolves():
    lib = ProcedureLibrary.from_sources(sources())
    terms = set(sources().vocabulary["locations"])
    for pid, p in lib.procedures.items():
        nodes = set(p["steps"]) | set(p["leaves"])
        assert p["start"] in nodes, pid
        for sid, st in p["steps"].items():
            for o in ("yes", "no", "unknown"):
                if o in st:
                    assert st[o] in nodes, (pid, sid, o)
            if "where" in st["ask"]:
                assert st["ask"]["where"] in terms, (pid, sid)
        for lid, lf in p["leaves"].items():
            if "goto" in lf:
                assert lf["goto"] in lib.procedures, (pid, lid)
            for c in lf.get("causes", []):
                assert c["mode"] in CATALOGUE and c["where"] in terms, (pid, lid, c)
    for sel in lib.selection:
        assert sel["procedure"] in lib.procedures


@pytest.mark.parametrize("seed", [9, 15])
def test_both_forms_give_the_same_answer(seed):
    a = make_case(2190.0, seed)
    b = make_case(2190.0, seed, procedures_in_graph=True)
    fa = DeterministicFramer(a.substrate).build(a.question)
    fb = DeterministicFramer(b.substrate).build(b.question)
    pa = ProcedureTool(a.substrate, ProcedureLibrary.from_sources(sources())).run(fa)
    pb = ProcedureTool(b.substrate, ProcedureLibrary.from_graph(b.substrate.kg)).run(fb)
    key = lambda p: [(c.hypothesis.id, round(c.prior_mass, 12)) for c in p.candidates] + [round(p.residual_mass, 12)]
    assert key(pa) == key(pb)
    assert expected_hit({c.hypothesis.id: c.prior_mass for c in pa.candidates}, a.truth.fault.key(), 1) == 1.0


def test_field_tests_stay_open_and_are_kept_as_advice():
    c = make_case(2190.0, 4)                       # a fibre cut: OTDR needed to tell a cut from a loss increase
    fr = DeterministicFramer(c.substrate).build(c.question)
    t = ProcedureTool(c.substrate, ProcedureLibrary.from_sources(sources()))
    p = t.run(fr)
    assert t.trace["tests_pending"] and "OTDR" in t.trace["tests_pending"][0]["test"]
    modes = {x.hypothesis.id.split("@")[0] for x in p.candidates}
    assert {"FIBRE_CUT", "SPAN_LOSS_INCREASE"} <= modes


def test_proposer_sees_procedures_only_in_the_graph_arm():
    a = make_case(2190.0, 15)
    b = make_case(2190.0, 15, procedures_in_graph=True)
    fa = DeterministicFramer(a.substrate).build(a.question)
    fb = DeterministicFramer(b.substrate).build(b.question)
    ua = LLMProposer(None, a.substrate.kg, GroundingTool(a.substrate.kg)).prompt(fa)[0]
    ub = LLMProposer(None, b.substrate.kg, GroundingTool(b.substrate.kg), procedures_from_graph=True).prompt(fb)[0]
    assert "troubleshooting procedures" not in ua
    assert "troubleshooting procedures" in ub and "P-AMP-FAIL" in ub
