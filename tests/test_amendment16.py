"""Amendment 16: the controlled rewrite stays resolvable, and the answer checks catch what they should."""
import json
from pathlib import Path

from adagent.tools.answer_llm import Conclusion, Fact, Report, check, expected_conclusion, summarise, template


def _summary(outcome="commit"):
    loop = {"decision": {"outcome": "commit", "hypothesis": "FIBRE_CUT@el:oms.S12-S15.span1()", "probability": 0.98},
            "checks": [{"check": "otdr_trace", "params": {"span": "oms.S12-S15.span1"}, "status": "done",
                        "value": {"event_km": 31.2}}]}
    if outcome == "set":
        loop["decision"] = {"outcome": "set", "members": ["A@el:x()", "B@el:y()"], "mass": 0.93,
                            "cause_probably_unconsidered": False}
    return summarise("Alarm X on oms.S12-S15.amp1. What is the cause?", loop, 1.5, 1)


def test_faithful_report_passes():
    s = _summary()
    r = Report(report="The cause is FIBRE_CUT on oms.S12-S15.span1 (probability 0.98); an OTDR trace found an event "
                      "at 31.2 km.", conclusion=expected_conclusion(s),
               facts=[Fact(field="decision.probability", value="0.98"), Fact(field="checks.0.answer", value="31.2")])
    c = check(r, s)
    assert c["faithful"], c


def test_invented_element_and_wrong_conclusion_fail():
    s = _summary()
    r = Report(report="The cause is a failed amplifier oms.S12-S15.amp3.", conclusion=Conclusion(kind="cause",
               items=["AMP_FAIL@el:oms.S12-S15.amp3()"]), facts=[])
    c = check(r, s)
    assert not c["conclusion_matches"] and "oms.S12-S15.amp3" in c["unsupported_identifiers"]


def test_set_reported_as_a_cause_fails():
    s = _summary("set")
    r = Report(report="It is A.", conclusion=Conclusion(kind="cause", items=["A@el:x()"]), facts=[])
    assert not check(r, s)["conclusion_matches"]
    assert "could not be decided" in template(s)


def test_cited_fact_must_be_found():
    s = _summary()
    r = Report(report="x", conclusion=expected_conclusion(s), facts=[Fact(field="decision.probability", value="0.5")])
    assert check(r, s)["facts_not_found"]


def test_the_question_reader_uses_the_rewriter_clock():
    from adagent.harness import rephrase
    from adagent.tools import question_llm
    assert question_llm.CLOCK0_MIN == rephrase.CLOCK0_MIN
