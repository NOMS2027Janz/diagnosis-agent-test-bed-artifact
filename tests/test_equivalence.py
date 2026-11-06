"""Equivalence classes at reference scale (harness side)."""
import ast
from pathlib import Path

from adagent.harness.cases import make_case
from adagent.harness.equivalence import equivalence_class

ROOT = Path(__file__).resolve().parents[1]


def test_a_filter_shift_cannot_be_told_from_receiver_degradation():
    c = make_case(2190.0, 5)                       # a WSS filter shift on one channel
    r = equivalence_class(c, ["trx.och8.z", "oms.S08-S23|36", "oms.S17-S18.amp1"])
    assert "RX_DEGRADATION@trx.och8.z" in r["members"]
    assert "WSS_FILTER_SHIFT@oms.S08-S23|36" in r["members"]
    assert not any(m.startswith("AMP_") for m in r["members"])


def test_a_hard_failure_is_alone_in_its_class():
    c = make_case(2190.0, 15)                      # a pump failure, alarmed
    r = equivalence_class(c, [c.truth.fault.targets[0], "oms.S17-S18.amp1", "oms.S17-S18.span2"])
    assert r["members"] == [c.truth.fault.key()]


def test_the_agent_side_never_imports_the_equivalence_module():
    for f in list((ROOT / "src/adagent/tools").glob("*.py")) + list((ROOT / "src/adagent/substrate").glob("*.py")):
        tree = ast.parse(f.read_text())
        mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any("equivalence" in m or "harness" in m for m in mods), f.name


def test_a_transient_cannot_stand_in_for_a_lasting_fault():
    c = make_case(2190.0, 3)                       # a dirty connector at a section input: lasting
    r = equivalence_class(c, [c.truth.fault.targets[0]])
    assert not any(m.startswith("CHANNEL_LOADING_TRANSIENT") for m in r["members"])
