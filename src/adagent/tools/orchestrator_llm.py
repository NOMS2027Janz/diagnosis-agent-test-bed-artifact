"""The LLM orchestrator: the planner arm in which an LLM chooses what to do next ("The Diagnosis Agent Test Bed"
§9.5, §11; decision D8).

The two planner arms differ only in who plans. In both, the ledger alone holds the posterior and decides
commitment (no LLM writes to it), the ledger's signals trigger returns to generation, and the same tools score
the same checks. At each step the orchestrator sees the question, the contenders with their probabilities, what
graph consistency and the twin said of each, the checks made so far with their answers, and the checks available
at the contenders' locations, each with its effort and, where the twin can say, the answer it predicts under each
contender. It chooses one of: make one of the listed checks; return to generation (with its reason); or stop and
report that it cannot decide (with its reason). It is not shown the advisor's information scores.

Version 2 (2026-10-16): each check's predicted answers are shown grouped (answers within the instruments' noise and
the twin's error are one group, by the same comparison the interpreters use), the tolerances are stated, and so is
how stopping is judged. Version 1 showed raw predicted numbers; in development the model read differences far inside
the twin's error as real and chose checks that could not separate anything.

Version 3 (2026-10-17): on the held-out run of v2, 5 of the 13 unjustified "I don't know" outcomes that arm A did not
share came from the planner, not the tools. In 4 the model asked to return to generation after all returns were used
(the loop treated that as a stop; it now refuses the return and asks again) and was never told how many returns were
left or that earlier returns had produced no new candidates; it preferred returning to a cheap check that separated
candidates. In 1 it declined a check that separated a low-probability candidate, judging stopping by the leading
candidates only, while "I don't know" is judged over every candidate reported. v3 shows the passes with their yield
and the returns left, says that a return tests nothing and is not a substitute for a separating check, and states
the stopping rule over every candidate in contention.

Economy (plan §9): the system prompt is fixed and comes first, so that it is billed at the cached-input rate after
the first call; the state is compact; one call per step.
"""
from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel

from .advisor import SAME_NATS, Advisor, checks_for
from .interpret import compare

PROMPT_VERSION = "orchestrator.v5"   # v5 (amendment 15): the check groups are the advisor's own, "none of these" included; v4: the tolerances of the shared accuracy standard
# v3: the actions the loop applies stated (a refused return), stopping judged on the reported set
# v2: returns and their yield not shown, stopping judged on the leaders
# v1: raw predicted answers, no grouping, no tolerances, no stopping rule

SYSTEM = """You plan the diagnosis of an anomaly in an optical transport network (ROADMs, amplified fibre spans,
coherent line interfaces, OTN switches, client services). A deterministic ledger holds the probability of each
candidate cause and decides when one is probable enough to commit to; you cannot change it. Your job is to choose
the next action so that the diagnosis is reached, or shown to be out of reach, with as little effort as possible.

Actions:
- check: make one of the listed checks (give its index). Remote checks run from the operations centre; dispatch
  checks send a technician to a site, and travel to a site not yet visited typically takes about two hours. Some
  checks interrupt service. A check that failed may be tried again once. A check whose answer is already known is
  not worth repeating.
- return_to_generation: ask for new candidate causes, when the evidence suggests the true cause is not among the
  candidates (for example every candidate fits the data poorly, or a check contradicted all the leading ones). A
  return tests nothing. Returns are limited: the state shows how many are left and how many new candidates each
  earlier pass produced; when earlier passes produced few or none, another is unlikely to help. When any listed check
  separates candidates, make the check rather than return, even if every candidate fits poorly or "none of these" is
  probable: a check can raise a low-probability candidate as well as remove a leading one, and the ledger may return
  to generation itself afterwards.
- stop: report that the candidates cannot be told apart with the checks available. Say why.

Each listed check shows the candidates grouped by the answer it would give. Answers count as the same when they
differ by less than the instruments' accuracy and the twin's together allow (two standard deviations): about 0.75 dB
in received power, 1.4 dB in OSNR, 0.65 dB in margin, 1.3 dB in span loss, half a decade in bit error rate. A check that puts all the leading candidates in one group
tells you nothing, however different the numbers look. A check is worth its effort in proportion to the probability
it moves between groups.

How the outcome is judged: a diagnosis committed by the ledger is judged against the truth. Stopping with "cannot
decide" reports every candidate still in contention, not only the leaders, and it is right only when no check within
the effort left could separate any of them from the others. If one could, stopping is a failure, even when the
candidate it separates has low probability, the check is a dispatch, or it interrupts service (an interruption of
one test is acceptable when it narrows the diagnosis). Prefer the cheaper of two checks that separate the same
groups; among checks that separate different candidates, prefer the one that moves the most probability per hour.
Reply with the action, the check index if any, and a one-sentence reason."""


class Decision(BaseModel):
    action: Literal["check", "return_to_generation", "stop"]
    check_index: Optional[int]
    reason: str


def _short(v, n=160) -> str:
    s = json.dumps(v, default=str)
    return s if len(s) <= n else s[:n] + "..."


class LLMOrchestrator:
    name = "llm_orchestrator"

    def __init__(self, client, max_checks_listed: int = 25):
        self.client = client
        self.max_listed = max_checks_listed
        self.advisor: Optional[Advisor] = None
        self.log: list = []

    def bind(self, loop) -> None:
        # the advisor's machinery is used only to list checks, predict answers and, with the "can this decide?" step
        # (amendment 13), to keep off the list the costly checks the step refuses; it does not score or choose them
        self.advisor = Advisor(loop.kg, loop.src.checks, loop.twin, loop.seed,
                               decide_gate=getattr(loop, "decide_gate", None))

    def state_text(self, loop, budget_left: float, done: list, visited: set) -> tuple[str, list]:
        led, adv = loop.ledger, self.advisor
        cont = adv.contenders(led)
        lines = [f"Question: {loop.frame.question}", f"Effort left: {budget_left:.1f} h. Sites visited: "
                 f"{', '.join(sorted(visited)) or 'none'}.", "", "Leading candidates (probability):"]
        post = led.posterior()
        for k, p in cont:
            lines.append(f"  {k}  p={p:.3f}")
        lines.append(f"  none of these  p={post.get('NONE_OF_THESE', 0.0):.3f}")
        lines.append("Generation passes: " + "; ".join(f"pass {x['pass']} ({x['trigger'][:40]}): {x['new_hypotheses']} new "
                                                   "candidates" for x in loop.record.get("passes", [])))
        left = getattr(loop, "returns_left", 0)
        lines.append(f"Returns to generation left: {left}."
                     + (" Return to generation is not available; choose a check or stop." if left <= 0 else ""))
        if getattr(loop, "return_refused", False):
            lines.append("Your request to return to generation was refused: no returns are left.")
        if loop.c1 is not None:
            lines.append("Graph consistency of the leading candidates:")
            for k, _ in cont:
                v = next((x for x in reversed(loop.c1.log) if x.hypothesis == k), None)
                if v:
                    lines.append(f"  {k}: {v.verdict}; " + "; ".join(f"{r.rule} {r.verdict}" for r in v.rules))
        if loop.twin is not None:
            lines.append("Twin fit (log-likelihood, higher is better; 'cannot simulate' where undocumented):")
            for k, _ in cont:
                f = loop.twin.fits.get(_cause(led.hypothesis(k))) if len(led.hypothesis(k).causes) == 1 else None
                if f is None:
                    lines.append(f"  {k}: not fitted")
                elif "declined" in f:
                    lines.append(f"  {k}: cannot simulate")
                else:
                    lines.append(f"  {k}: {f['ll']:.1f} {f.get('params', {})}")
            lines.append(f"  background (an unproposed cause): {loop.twin.background():.1f}")
        lines.append("Checks made:")
        for d in done:
            lines.append(f"  {d['check']} {d['params']}: {d['status']} {_short(d.get('value'))}")
        if not done:
            lines.append("  none")
        stood = {(d["check"], tuple(sorted(d["params"].items()))) for d in done if d.get("status") == "done"}
        options, seen = [], set()
        for k, _ in cont:
            for c in led.hypothesis(k).causes:
                loc = c.location.element if c.location.kind == "element" else getattr(c.location, "span", None)
                for ch, prm in (checks_for(loop.kg, loc) if loc else []):
                    sig = (ch, tuple(sorted(prm.items())))
                    if sig in seen or sig in stood:
                        continue
                    seen.add(sig)
                    eff = adv.effort_h(ch, prm, visited)
                    if eff <= budget_left:
                        options.append((ch, prm, eff))
        # amendment 13: the "can this decide?" step, as the advisor applies it, on every check offered
        adv.last_refused = []
        if adv.decide_gate is not None:
            kept = []
            for ch, prm, eff in options:
                r = adv.gate_refuses(led, ch, prm, visited)
                if r is None:
                    kept.append((ch, prm, eff))
                else:
                    adv.refused.append(r)
                    adv.last_refused.append(r)
            options = kept
        options = options[: self.max_listed]
        if adv.last_refused:
            lines.append(f"Not listed: {len(adv.last_refused)} check(s) of half an hour or more that could not lead to "
                         "a decision (under one chance in twenty), which the agent does not make.")
        lines.append("Checks available (index: check, parameters, effort). For each, the candidates grouped by the "
                     "answer the check would give: candidates in the same group give answers within the "
                     "instrument's noise and the twin's error of one another, so the check cannot tell them apart; "
                     "it can only tell one group from another. 'Unknown' means no prediction is available.")
        for i, (ch, prm, eff) in enumerate(options):
            lines.append(f"  [{i}] {ch} {prm} ({adv.cls.get(ch)}, {eff:.1f} h"
                         f"{', interrupts service' if adv.impact.get(ch) else ''})")
            # amendment 15: the groups are the advisor's own (Advisor._assess), so the LLM sees what the advisor
            # counts, including "none of these" as a group of its own when the check could confirm the leader
            # against a cause nobody proposed (the leader's predicted answer differs from the no-fault answer)
            p_none = led.posterior().get("NONE_OF_THESE", 0.0)
            _gain, agroups, _unk, _base = adv._assess(led, cont, ch, prm, visited, p_none)
            named_known = {k for g in agroups for k in g[2] if k != "NONE_OF_THESE"}
            unknown = [k for k, _ in cont if k not in named_known]
            if len(agroups) <= 1:
                lines.append("      separates nothing: every candidate with a prediction gives the same answer, and the "
                             "answer would not confirm them against a cause nobody proposed")
            for n, (ans, mass, members) in enumerate(agroups, 1):
                if ans is None:
                    lines.append(f"      group {n} (probability {mass:.3f}): an answer other than the leader's, as a cause "
                                 "nobody proposed would give; none of these (the check can confirm the candidates that "
                                 "predict the leader's answer against an unproposed cause)")
                else:
                    lines.append(f"      group {n} (probability {mass:.3f}): answer {_short(ans, 110)}; "
                                 + ", ".join(members))
            if unknown:
                lines.append("      unknown for: " + ", ".join(unknown))
        return "\n".join(lines), options

    def next(self, loop, budget_left: float, done: list, visited: set) -> dict:
        text, options = self.state_text(loop, budget_left, done, visited)
        try:
            d = self.client.parse("orchestrator", PROMPT_VERSION, SYSTEM, text, Decision)
        except RuntimeError as e:
            self.log.append({"error": str(e)[:200]})
            return {"action": "stop", "reason": "orchestrator call failed"}
        self.log.append({"action": d.action, "index": d.check_index, "reason": d.reason, "options": len(options),
                         "none_groups_shown": text.count("as a cause nobody proposed would give")})
        if d.action == "check":
            if d.check_index is None or not (0 <= d.check_index < len(options)):
                return {"action": "stop", "reason": f"invalid check index {d.check_index}: {d.reason}"}
            ch, prm, eff = options[d.check_index]
            return {"action": "check", "check": ch, "params": prm, "effort_h": round(eff, 2), "reason": d.reason}
        if d.action == "return_to_generation":
            return {"action": "return", "reason": d.reason}
        return {"action": "stop", "reason": d.reason}

    def trace(self) -> list:
        return self.log


def _cause(h):
    c = h.causes[0]
    loc = c.location.element if c.location.kind == "element" else getattr(c.location, "span", None)
    return (c.fault_mode, loc)
