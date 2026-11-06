"""Expressing the agent's answer for an operator (amendment 16): the LLM writer, a coded template, and the
automated faithfulness checks.

The input is a summary of the agent's record for a case: the question, the ledger's outcome (a cause committed to
with its probability; a set of candidates with their probabilities, flagged when the cause was probably never
proposed; or a commitment at a coarser grain), the checks made with their answers, and the effort spent. The LLM
writes the report an operator would read and states, separately, the conclusion it gave and the facts it used, each
with the field of the summary it came from. The coded template writes the same report from the same summary by a
fixed form.

Faithfulness is checked by code: (1) the conclusion the report gives must be the ledger's (the same kind, and the
same cause, candidates or grain); (2) every identifier in the report (services, wavelengths, elements, sites, fault
mode names) must appear in the summary; (3) every fact cited must be found at the field named with the value given.
Whether a report is useful to an operator is judged by people, blind, on a sample (not here).
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel

PROMPT_VERSION = "answer-writer-0.1"
SYSTEM = """You write the report that a diagnosis system gives the operations engineer who asked the question. The \
system has already decided; you only express its result. Write for an engineer: say what the system concluded, how \
sure it is, what was checked and what that showed, and what to do next if the system could not settle the cause. \
Use only the facts in the summary; never add a cause, a probability, an element or a recommendation the summary \
does not support. If the system committed to a cause, say so plainly; if it reported a set of candidates, say that \
it could not decide among them and name them; if it committed only to a location or a kind of fault (a coarser \
grain), say exactly that and no more. Keep it under 180 words.
Then state the conclusion you gave (kind and items, copied exactly from the summary) and list the facts you used, \
each with the summary field it came from (a dotted path such as decision.probability or checks.0.value) and the \
value as it appears there."""


class Fact(BaseModel):
    field: str
    value: str


class Conclusion(BaseModel):
    kind: Literal["cause", "candidates", "coarser_grain", "nothing_found"]
    items: list[str]


class Report(BaseModel):
    report: str
    conclusion: Conclusion
    facts: list[Fact]


def summarise(question_text: str, loop: dict, effort_h: float, visits: int) -> dict:
    d = loop.get("decision") or {}
    if d.get("outcome") == "commit":
        dec = {"outcome": "committed to a cause", "cause": d.get("hypothesis"),
               "probability": round(float(d.get("probability", 0.0)), 3)}
    elif (d.get("coarse") or {}).get("at"):
        c = d["coarse"]
        dec = {"outcome": "committed at a coarser grain", "grain": c.get("grain"), "at": c.get("at"),
               "probability": round(float(c.get("mass", 0.0)), 3), "candidates": list(c.get("members", []))[:8]}
    else:
        dec = {"outcome": "could not decide", "candidates": list(d.get("members", []))[:8],
               "probability_of_the_set": round(float(d.get("mass", 0.0)), 3),
               "cause_probably_not_considered": bool(d.get("cause_probably_unconsidered"))}
    checks = []
    for c in loop.get("checks", []) or []:
        v = c.get("value")
        checks.append({"check": c.get("check"), "at": c.get("params"), "status": c.get("status"),
                       "answer": json.loads(json.dumps(v, default=str))[:3] if isinstance(v, list) else v})
    return {"question": question_text, "decision": dec, "checks": checks,
            "effort_hours": round(effort_h, 2), "site_visits": visits}


def expected_conclusion(summary: dict) -> Conclusion:
    d = summary["decision"]
    if d["outcome"] == "committed to a cause":
        return Conclusion(kind="cause", items=[d["cause"]])
    if d["outcome"] == "committed at a coarser grain":
        return Conclusion(kind="coarser_grain", items=[str(d["at"])])
    if d["candidates"]:
        return Conclusion(kind="candidates", items=list(d["candidates"]))
    return Conclusion(kind="nothing_found", items=[])


def template(summary: dict) -> str:
    d = summary["decision"]
    if d["outcome"] == "committed to a cause":
        head = f"Cause: {d['cause']} (probability {d['probability']})."
    elif d["outcome"] == "committed at a coarser grain":
        head = (f"The cause lies at {d['at']} ({d['grain']} grain, probability {d['probability']}); the exact cause "
                f"could not be settled among: {', '.join(d['candidates'])}.")
    else:
        head = ("The cause could not be decided. Candidates: " + (", ".join(d["candidates"]) or "none") +
                (". The cause was probably not among those proposed." if d["cause_probably_not_considered"] else "."))
    ch = "; ".join(f"{c['check']} {c['at']}: {c['status']}" for c in summary["checks"]) or "none"
    return (f"{head} Checks made: {ch}. Effort: {summary['effort_hours']} h of tests and travel, "
            f"{summary['site_visits']} site visit(s).")


_ID = re.compile(r"\b(svc\d+|och\d+|trx\.och\d+\.[az]|oms\.S\d+-S\d+(?:\.(?:amp|span)\d+)?(?:\|\d+)?|otn\.S\d+|S\d{2}|"
                 r"[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)\b")


def check(rep: Report, summary: dict) -> dict:
    blob = json.dumps(summary)
    exp = expected_conclusion(summary)
    concl_ok = rep.conclusion.kind == exp.kind and (
        set(rep.conclusion.items) == set(exp.items) if exp.kind in ("cause", "coarser_grain")
        else set(rep.conclusion.items) <= set(exp.items) and bool(rep.conclusion.items or not exp.items))
    unsupported = sorted({m for m in _ID.findall(rep.report) if m not in blob})

    def at(path):
        cur = summary
        for p in path.split("."):
            if isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
                cur = cur[int(p)]
            elif isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return None
        return cur
    bad_facts = [f.model_dump() for f in rep.facts
                 if at(f.field) is None or (str(f.value).strip() not in json.dumps(at(f.field))
                                            and str(f.value).strip() != str(at(f.field)))]
    return {"conclusion_matches": concl_ok, "unsupported_identifiers": unsupported, "facts_not_found": bad_facts,
            "faithful": concl_ok and not unsupported and not bad_facts}
