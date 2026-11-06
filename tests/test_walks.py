"""Generation by graph walks and classical alarm correlation (build step 5)."""
import re
from pathlib import Path

import pytest

from adagent.harness.cases import make_case
from adagent.harness.metrics import expected_hit
from adagent.tools.correlation import AlarmCorrelation
from adagent.tools.frame import DeterministicFramer
from adagent.tools.propagation import Propagator
from adagent.tools.walks import AbductionWalk, MinimumExplanation, Observations, RandomWalkRanking
from adagent.world.faults import CATALOGUE

ROOT = Path(__file__).resolve().parents[1]
SEEDS = (0, 3, 7, 13, 14)       # client port, dirty connector, transmitter, card, span loss (day 2190)


@pytest.fixture(scope="module")
def framed():
    out = []
    for s in SEEDS:
        c = make_case(2190.0, s)
        out.append((c, DeterministicFramer(c.substrate).build(c.question)))
    return out


def test_generators_name_no_fault_mode():
    for f in ("propagation.py", "walks.py", "correlation.py"):
        text = (ROOT / "src/adagent/tools" / f).read_text()
        assert not [m for m in CATALOGUE if re.search(rf"\b{m}\b", text)], f


def test_forward_prediction_explains_what_was_seen(framed):
    for c, fr in framed:
        obs = Observations(fr, c.substrate.kg)
        P = Propagator(c.substrate.kg).predict(c.truth.fault.mode, c.truth.fault.targets[0])
        explained = sum(1 for k in obs.O if k in P) / len(obs.O)
        assert explained >= 0.9, (c.id, c.truth.fault.key(), explained)
        missing = [k for k, s in P.items() if s == "required" and k not in obs.O and obs.examined(k)]
        assert not missing, (c.id, missing[:5])


def test_abduction_and_set_cover_put_the_truth_first(framed):
    for c, fr in framed:
        kg = c.substrate.kg
        abd = AbductionWalk(kg)
        for gen in (abd, MinimumExplanation(kg, abd)):
            p = gen.run(fr)
            scores = {x.hypothesis.id: x.prior_mass for x in p.candidates}
            hit = max(expected_hit(scores, f"{c.truth.fault.mode}@{t}", 1) for t in c.truth.fault.targets)
            assert hit == 1.0, (gen.name, c.id, c.truth.fault.key(), list(scores)[:3])


def test_payloads_are_valid_distributions(framed):
    for c, fr in framed:
        kg = c.substrate.kg
        for gen in (AbductionWalk(kg), RandomWalkRanking(kg), AlarmCorrelation(kg)):
            p = gen.run(fr)
            assert sum(x.prior_mass for x in p.candidates) + p.residual_mass <= 1 + 1e-9


def test_alarm_correlation_is_silent_without_alarms_and_finds_equipment_faults(framed):
    for c, fr in framed:
        p = AlarmCorrelation(c.substrate.kg).run(fr)
        if not any(s.kind == "alarm" for s in fr.symptoms):
            assert not p.candidates
        if c.truth.alarms and c.substrate.kg.cls(c.truth.fault.targets[0]) in ("LineInterface", "ClientPort"):
            assert p.candidates[0].hypothesis.id == c.truth.fault.key()
