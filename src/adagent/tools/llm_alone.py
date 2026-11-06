"""Baseline: the LLM diagnoses and decides alone (campaign to the NOMS 2027 paper, stage 1).

The question a reader asks of the agent: why not give the LLM the data and let it diagnose? This baseline does that.
The LLM receives the case exactly as the agent's LLM proposer does (the question, symptoms, services, the
instruments that saw no change, the equipment and its documented fault modes, the location terms), and must end
with a decision: commit to one cause, or say "I don't know". There is no ledger, no twin and no knowledge-graph
reasoning beyond what the brief carries. Two forms:

- single shot (with_checks=False): one call;
- with checks (with_checks=True): the LLM may ask for checks from the same catalogue, with the same effort, as the
  agent, one at a time, within the same budget, and sees each check's raw result before its next turn.

The commitment threshold the agent's ledger uses (THETA_COMMIT) is stated to the LLM, so the comparison is on
judgement, not on a different standard. A commitment is grounded with the proposer's own grounding; a proposal that
grounds to several locations is reported as a set (it names no single place to act on). Effort is counted by the
harness from the substrate's record of checks, as for every arm.
"""
from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import create_model

from ..defaults import LedgerDefaults
from .advisor import Advisor, checks_for
from .proposer_llm import LLMProposer
from .walks import single

PROMPT_VERSION = "llm-alone-0.2"   # 0.2: a commitment that resolves to several locations is clarified once
MAX_TURNS = 10
MAX_OPTIONS = 25
THETA_COMMIT = LedgerDefaults().theta_commit

SYSTEM = f"""You diagnose anomalies in an optical transport network carrying OTN services (ROADMs, amplified fibre \
lines, coherent line interfaces, OTN switches, client services with optional SNCP protection). You are the \
engineer responsible for the diagnosis: your decision is acted on. A commitment sends a repair to the cause you name; \
a wrong commitment costs a wasted repair and leaves the fault in place. Saying "I don't know" is an accepted outcome \
when the evidence does not settle the cause.

You are given a case: the question, the symptoms found, the services affected and their neighbours, the instruments \
that saw no change, and the equipment involved with the fault modes its documentation lists.

Each turn, give:
- assessment: your reasoning in a few sentences;
- proposals: the faults that could explain the case, each with fault_mode (one of the documented fault modes listed \
for the equipment in the case); where it lies, in one of three ways (location_kind "term": a location term relative \
to a symptom, anchor_symptom e.g. "O4", or a service, anchor_service e.g. "V2"; location_kind "shared": one of the \
elements every affected service's working path depends on, by its reference e.g. "E3"; location_kind "element": an \
element named in the brief, by its identifier exactly as written, or a site name for a node's own equipment); set \
the fields that do not apply to null; probability (0 to 1); rationale;
- probability_other: your probability that the cause is none of your proposals;
- action: "commit", "dont_know"{', or "check"' if True else ''};
- commit_index: when committing, the index (0-based) of the proposal you commit to, which must name one element \
(location_kind "element" or "shared"); otherwise null;
- check_index: when asking for a check, the index of the check in the list of checks available; otherwise null.

Commit only when you judge the proposal you commit to at least {THETA_COMMIT:.0%} likely to be the cause. Reason from \
what was seen and from what was not: an instrument that should have seen an effect and saw no change is evidence \
against a fault that would have caused it. Do not invent elements, fault modes or location terms."""

SYSTEM_SINGLE = SYSTEM.replace(', or "check"', "").replace(
    "- check_index: when asking for a check, the index of the check in the list of checks available; otherwise null.\n",
    "- check_index: null (no checks can be made in this case).\n")


def _short(v, n=300) -> str:
    s = json.dumps(v, default=str) if not isinstance(v, str) else v
    return s if len(s) <= n else s[: n - 3] + "..."


class LLMAlone:
    name = "llm_alone"

    def __init__(self, client, substrate, sources, frame, grounding, with_checks: bool, budget_h: float = 8.0):
        self.client, self.sub, self.frame = client, substrate, frame
        self.kg = substrate.kg
        self.proposer = LLMProposer(client, self.kg, grounding)
        self.adv = Advisor(self.kg, sources.checks)          # used only to list checks and their effort
        self.with_checks, self.budget_h = with_checks, budget_h
        self.record: dict = {"turns": [], "checks": []}

    def _schema(self, modes, terms):
        base = self.proposer.schema(modes, terms)
        P = base.model_fields["proposals"].annotation
        actions = ("commit", "dont_know", "check") if self.with_checks else ("commit", "dont_know")
        return create_model("Turn", assessment=(str, ...), proposals=(P, ...), probability_other=(float, ...),
                            action=(Literal[actions], ...), commit_index=(Optional[int], ...),
                            check_index=(Optional[int], ...))

    def _options(self, refs, last_props, visited, spent, done_sigs) -> list:
        locs: list = []
        for d in (refs["S"], refs["E"]):
            for el in d.values():
                if el not in locs:
                    locs.append(el)
        for p in last_props or []:
            for loc in p.get("grounded", [])[:5]:
                if loc not in locs:
                    locs.insert(0, loc)
        opts, seen = [], set()
        for loc in locs:
            for ch, prm in checks_for(self.kg, loc):
                sig = (ch, tuple(sorted(prm.items())))
                if sig in seen or sig in done_sigs:
                    continue
                seen.add(sig)
                eff = self.adv.effort_h(ch, prm, visited)
                if spent + eff <= self.budget_h:
                    opts.append((ch, prm, eff))
                if len(opts) >= MAX_OPTIONS:
                    return opts
        return opts

    def _clarify(self, user0: str, system: str, p: dict, g: list) -> Optional[int]:
        g = g[:40]
        text = (user0 + f"\n\nYou committed to {p['fault_mode']} ({p.get('rationale', '')[:300]}). The place you named "
                f"resolves to {len(g)} locations:\n" + "\n".join(f"  [{i}] {loc}" for i, loc in enumerate(g)) +
                "\n\nCommit to one of them by its index, or say \"I don't know\" if the evidence does not tell "
                "them apart.")
        C = create_model("Clarify", assessment=(str, ...), action=(Literal["choose", "dont_know"], ...),
                         index=(Optional[int], ...))
        out = self.client.parse("llm_alone_clarify", PROMPT_VERSION, system, text, C)
        self.record["clarify"] = {"locations": g, "action": out.action, "index": out.index,
                                  "assessment": out.assessment}
        if out.action == "choose" and out.index is not None and 0 <= out.index < len(g):
            return out.index
        return None

    def run(self) -> tuple[dict, dict, dict]:
        """Returns (decision, hypotheses by key, record)."""
        user0, refs, modes, terms = self.proposer.prompt(self.frame)
        if not modes:
            self.record["error"] = "no documented fault modes in the case"
            return {"outcome": "set", "members": [], "cause_probably_unconsidered": True}, {}, self.record
        schema = self._schema(modes, terms)
        system = SYSTEM if self.with_checks else SYSTEM_SINGLE
        spent, visited, done, done_sigs = 0.0, set(), [], set()
        last_props: list = []
        out = None
        for turn in range(MAX_TURNS if self.with_checks else 1):
            opts = self._options(refs, last_props, visited, spent, done_sigs) if self.with_checks else []
            text = user0
            if self.with_checks:
                text += f"\n\nEffort spent: {spent:.1f} h of {self.budget_h:.0f} h. Sites visited: " \
                        f"{', '.join(sorted(visited)) or 'none'}.\nChecks made so far:\n" + \
                        ("\n".join(f"  {d['check']} {d['params']}: {d['status']} {_short(d['value'])}" for d in done)
                         or "  none") + \
                        "\n\nChecks available (index: check, parameters, kind, effort in hours including travel " \
                        "to a site not yet visited):\n" + \
                        ("\n".join(f"  [{i}] {ch} {prm} ({self.adv.cls.get(ch)}, {eff:.1f} h"
                                   f"{', interrupts service' if self.adv.impact.get(ch) else ''})"
                                   for i, (ch, prm, eff) in enumerate(opts)) or "  none within the effort left")
                if turn == MAX_TURNS - 1 or not opts:
                    text += "\n\nThis is your last turn: commit or say \"I don't know\"."
            out = self.client.parse("llm_alone", PROMPT_VERSION, system, text, schema)
            pay = self.proposer.ground(self.frame, out, refs)
            last_props = self.proposer.trace.get("proposals", [])
            self.record["turns"].append({"assessment": out.assessment, "action": out.action,
                                         "commit_index": out.commit_index, "check_index": out.check_index,
                                         "proposals": last_props, "probability_other": out.probability_other})
            if out.action == "check" and self.with_checks and out.check_index is not None \
                    and 0 <= out.check_index < len(opts) and turn < MAX_TURNS - 1:
                ch, prm, eff = opts[out.check_index]
                res = self.sub.run_check(ch, prm)
                spent += res.get("minutes", 0) / 60.0
                for s in res.get("dispatch_sites") or []:
                    if s not in visited:
                        visited.add(s)
                        spent += 2.0
                done.append({"check": ch, "params": prm, "status": res.get("status"), "value": res.get("value")})
                done_sigs.add((ch, tuple(sorted(prm.items()))))
                self.record["checks"].append(done[-1])
                continue
            break
        # the decision
        hyps = {f"q{i}": c.hypothesis for i, c in enumerate(pay.candidates)}
        if out.action == "commit" and out.commit_index is not None and 0 <= out.commit_index < len(last_props):
            p = last_props[out.commit_index]
            g = p.get("grounded") or []
            if len(g) == 1:
                key = "commit"
                hyps[key] = single(p["fault_mode"], g[0])
                self.record["decision"] = "commit"
                return {"outcome": "commit", "hypothesis": key}, hyps, self.record
            if len(g) > 1:
                # the commitment names a place the documentation resolves to several locations (the ports of a unit,
                # the spans of a line): the LLM is asked once to choose among them, as the agent's ledger would have
                # to separate them before committing
                choice = self._clarify(user0, system, p, g)
                if choice is not None:
                    key = "commit"
                    hyps[key] = single(p["fault_mode"], g[choice])
                    self.record["decision"] = f"commit after choosing one of {len(g)} locations"
                    return {"outcome": "commit", "hypothesis": key}, hyps, self.record
            self.record["decision"] = f"commit not to one place ({len(g)} locations); reported as a set"
            keys = [k for k, h in hyps.items()
                    if h.causes and h.causes[0].fault_mode == p["fault_mode"]] or list(hyps)
            return {"outcome": "set", "members": keys, "cause_probably_unconsidered": False}, hyps, self.record
        if out.action == "commit":
            self.record["decision"] = "commit without a valid proposal index; reported as a set"
        else:
            self.record["decision"] = "dont_know"
        return {"outcome": "set", "members": list(hyps),
                "cause_probably_unconsidered": out.probability_other >= 0.5}, hyps, self.record
