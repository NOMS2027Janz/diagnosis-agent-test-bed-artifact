"""The advisor: which check next (planner, deterministic reference arm; "The Diagnosis Agent Test Bed" §9.5).

At each step the advisor looks at the contenders holding most of the posterior, lists the checks that look at
their locations (the catalogue in knowledge/practice/checks.yaml, with availability read from the graph: an
embedded OTDR only at full-tier sites, for example), predicts the answer each check would give under each
contender, and recommends the check with the largest expected information per hour of effort.

Prediction. Where the twin has fitted a contender, the twin predicts the answer. Where it has not, the check's
own meaning gives a coarse prediction for checks that answer directly about a place (a unit swap clears the
symptoms only if the unit is at fault; a connector inspection finds dirt only if the connector is the cause); for
other checks such a contender's answer is unknown.

Information. "None of these" joins as a group of its own when the leader's predicted answer differs from the no-fault
answer (a confirming check). Contenders whose predicted answers agree (misfit under SAME_NATS, src/adagent/accuracy.py) fall in one
group; the expected information of the check is the entropy, in bits, of the posterior mass over the groups,
scaled down by the share of mass whose answer is unknown (that mass is not separated by the check). This is the
exact expected reduction in entropy when predictions are deterministic and contenders' answers known.

Effort. Time to result from the catalogue; travel of TRAVEL_ESTIMATE_H for each site not yet visited in the
case (the agent does not know the actual travel time until someone goes); and a penalty of IMPACT_PENALTY_H for a
check that interrupts service. A check already made whose answer still stands is not repeated; one that failed
may be tried once more.

Stopping. The advisor returns nothing when no check within the effort left is expected to yield MIN_GAIN_BITS:
the remaining contenders cannot be told apart by any check it knows of, which is what a justified "I don't know"
rests on.
"""
from __future__ import annotations

import math
from typing import Optional

from ..ledger import NONE_KEY
from . import interpret as _interp
from .interpret import answer_set, compare, direct

TRAVEL_ESTIMATE_H = 2.0
IMPACT_PENALTY_H = 1.0
MIN_GAIN_BITS = 0.05
from ..accuracy import SAME_NATS  # noqa: E402  (answers alike: one standard with the harness)
MAX_CONTENDERS = 12      # amendment 4: the ledger's reported set, as the harness judges a stop
MIN_CONTENDERS = 3


def checks_for(kg, loc: str) -> list[tuple[str, dict]]:
    """The catalogue's checks that look at a location, as the graph records it."""
    base, _, rest = loc.partition("|")
    out: list = []
    cls = kg.cls(base) if kg.has(base) else None
    if cls == "Instrument":
        base = kg.g.nodes[base].get("lake_element", base)
        cls = kg.cls(base) if kg.has(base) else None

    def receivers_through(oms, n=2):
        rx = []
        for lp, d in sorted(kg.lightpaths_through(oms)):
            ends = kg.lightpath_ends(lp)
            r = ends.get("z") if d == "fwd" else ends.get("a")
            if r and r not in rx:
                rx.append(r)
            if len(rx) >= n:
                break
        return rx

    oms = None
    if cls == "FibreSpan" or cls == "Amplifier":
        oms = kg.g.nodes[base].get("oms")
    elif cls == "OMS":
        oms = base
    if oms:
        for r in receivers_through(oms):
            out += [("coherent_diag", {"trx": r}), ("pm_detail", {"trx": r})]
    if cls == "FibreSpan":
        src = kg.g.nodes[oms]["src"]
        if kg.g.nodes.get(f"site.{src}", {}).get("tier") == "full":
            out.append(("otdr_embedded", {"span": base}))
        out += [("otdr_portable", {"span": base}), ("span_loss", {"span": base})]
    elif cls == "OMS":
        out += [("power_meter", {"oms": base}), ("inspect_clean", {"oms": base})]
        if rest:
            for lp, d in kg.lightpaths_through(base):
                if str(kg.g.nodes[lp].get("slot")) == rest:
                    ends = kg.lightpath_ends(lp)
                    r = ends.get("z") if d == "fwd" else ends.get("a")
                    out += [("coherent_diag", {"trx": r}),
                            ("retune_spare", {"lightpath": lp, "end": "z" if d == "fwd" else "a"})]
                    break
    elif cls == "Amplifier":
        out.append(("swap_unit", {"unit": base}))
        if kg.g.nodes[base].get("index") == 0:
            out.append(("power_meter", {"oms": oms}))
    elif cls == "LineInterface":
        out += [("coherent_diag", {"trx": base}), ("tx_power", {"trx": base}), ("pm_detail", {"trx": base}),
                ("swap_unit", {"unit": base})]
        lp = next((l for l, _ in kg.inn(base, "endsAt")), None)
        if lp:
            end = "z" if kg.lightpath_ends(lp).get("z") == base else "a"
            out.append(("retune_spare", {"lightpath": lp, "end": end}))
    elif cls == "OTNSwitch":
        out.append(("xc_audit", {"switch": base}))
        if rest:
            out.append(("tcm_segments", {"path": rest}))
    elif cls == "ClientPort":
        out += [("loopback", {"port": base}), ("swap_unit", {"unit": base})]
    elif cls == "OCh":
        ends = kg.lightpath_ends(base)
        out += [("coherent_diag", {"trx": t}) for t in (ends.get("z"), ends.get("a")) if t]
    return out


def _entropy(ps) -> float:
    s = sum(ps)
    return -sum(p / s * math.log2(p / s) for p in ps if p > 0) if s > 0 else 0.0


def contenders_of(ledger) -> list[tuple[str, float]]:
    """The named members of the set the ledger would report now (the leaders holding 90 per cent of the
    probability), at most MAX_CONTENDERS, so that the planner works on the set by which a stop is judged; filled
    up to MIN_CONTENDERS named hypotheses by probability, so that a leader short of commitment can still be
    confirmed against its runners-up."""
    post = ledger.posterior()
    named = set(ledger.named_keys())

    def ok(k):
        return k != NONE_KEY and k in post and (
            k in named or ledger.hypothesis(k).kind.value == "instrument_fault")
    members = ledger.decision().get("members") or []
    out = [(k, post[k]) for k in members if ok(k)][:MAX_CONTENDERS]
    for k, p in sorted(post.items(), key=lambda kv: -kv[1]):
        if len(out) >= MIN_CONTENDERS:
            break
        if ok(k) and k not in dict(out):
            out.append((k, p))
    return out


class Advisor:
    name, version = "advisor.voi", "0.1"

    def __init__(self, kg, catalogue: dict, twin=None, case_seed: int = 0, decide_gate=None):
        self.kg, self.twin, self.seed = kg, twin, case_seed
        # decide_gate (min_effort_h, p_min): the "can this decide?" step. A check whose estimated effort is at least
        # min_effort_h is not proposed when the probability, as the agent holds it now, of an answer from which the
        # ledger would commit (at once, or after one more check of the same strength) is below p_min.
        self.decide_gate = decide_gate
        self.refused: list = []          # every check refused by the step in this case
        self.last_refused: list = []     # those refused in the latest recommendation
        self.minutes = {c["id"]: c["minutes"] for c in catalogue.get("checks", [])}
        self.cls = {c["id"]: c["class"] for c in catalogue.get("checks", [])}
        self.impact = {c["id"]: bool(c.get("impact")) for c in catalogue.get("checks", [])}
        self.trace: list = []
        self.last_groups: Optional[dict] = None     # the chosen check's answer groups (for the can-decide record)
        self.extra_checks = None         # the capability ladder (rung L6): fn(kg, loc) -> further checks at loc

    def contenders(self, ledger) -> list[tuple[str, float]]:
        return contenders_of(ledger)

    def _site_of_check(self, check: str, params: dict) -> list[str]:
        if self.cls.get(check) != "dispatch":
            return []
        kg = self.kg
        if "span" in params or "oms" in params:
            oms = params.get("oms") or kg.g.nodes[params["span"]].get("oms")
            o = kg.g.nodes[oms]
            return sorted({o["src"], o["dst"]}) if check == "span_loss" else [o["src"]]
        if "unit" in params:
            return [kg.site_of(params["unit"]) or ""]
        return []

    def effort_h(self, check: str, params: dict, visited: set) -> float:
        h = self.minutes.get(check, 30) / 60.0
        h += TRAVEL_ESTIMATE_H * sum(1 for s in self._site_of_check(check, params) if s not in visited)
        if self.impact.get(check):
            h += IMPACT_PENALTY_H
        return h

    def when(self, check: str, params: dict, visited: set) -> Optional[float]:
        """The case time at which the check's answer would be read, as the agent estimates it: the case clock, plus
        the estimated travel to new sites and the check's minutes."""
        if self.twin is None:
            return None
        h = self.minutes.get(check, 30) / 60.0
        h += TRAVEL_ESTIMATE_H * sum(1 for s in self._site_of_check(check, params) if s not in visited)
        return self.twin.at_s + 3600.0 * h

    def base(self, check: str, params: dict, t: Optional[float] = None):
        """The twin's answer to the check with no fault, or None."""
        if self.twin is None:
            return None
        r = self.twin.predict_check([], check, params, self.seed, t=t)
        return r["value"] if r["status"] == "done" else None

    def predict(self, ledger, key: str, check: str, params: dict, t: Optional[float] = None):
        """('known', answer) or ('unknown', None); t: when the answer would be read (default the case clock)."""
        h = ledger.hypothesis(key)
        if self.twin is not None:
            fit = self.twin.fit(h)
            if "declined" not in fit:
                r = self.twin.predict_check(fit["events"], check, params, self.seed, t=t)
                if r["status"] == "done":
                    return ("known", r["value"])
                if r["status"] != "not_modelled":
                    return ("known", {"status": r["status"]})
        c, loc, mode = None, None, None
        if len(h.causes) == 1:
            c = h.causes[0]
        elif len(h.causes) > 1 and _interp.COMPOUND_READING:
            c = _interp.compound_cause(self.kg, check, params, h)
        if c == "elsewhere":
            loc, mode = "__elsewhere__", "NONE_OF_THESE"
        elif c is not None:
            loc = c.location.element if c.location.kind == "element" else getattr(c.location, "span", None)
            mode = c.fault_mode
        if c is not None:
            if loc is not None:
                # the documented meaning: the answer whose reading fits a fault here best, if one does
                paths = tuple(sorted(pth for pth, _ in self.kg.inn(params["switch"], "crossConnectedAt"))) \
                    if check == "xc_audit" and self.kg.has(params.get("switch", "")) else ()
                scored = [(direct(check, params, a, loc, mode, self.kg), a)
                          for a in answer_set(self.kg, check, params, paths)]
                # the answer the contender's reading fits explicitly; failing that, an answer the reading says
                # nothing against (a contender elsewhere expects no finding here); answers read alike (dirty,
                # unseated) stand for each other
                explicit = [(x, a) for x, a in scored if x is not None]
                silent = [a for x, a in scored if x is None]
                if explicit:
                    best = min(x for x, _ in explicit)
                    if best == 0.0:
                        return ("known", next(a for x, a in explicit if x == best))
                    if silent:
                        return ("known", silent[0])
                    if len({x for x, _ in explicit}) > 1:
                        return ("known", next(a for x, a in explicit if x == best))
        return ("unknown", None)

    def _assess(self, ledger, cont, ch: str, p: dict, visited: set, p_none: float):
        """One check's answer groups over the contenders and its expected information (bits), as recommend() scores
        it (factored out unchanged in amendment 13, so that the "can this decide?" step can be applied to a check
        another planner chooses)."""
        groups: list = []              # [(answer, mass)]
        unknown = 0.0
        t_read = self.when(ch, p, visited)
        base = self.base(ch, p, t_read)
        lead_ans = None
        for i, (k, mass) in enumerate(cont):
            kind, ans = self.predict(ledger, k, ch, p, t_read)
            if kind == "unknown":
                unknown += mass
                continue
            if i == 0:
                lead_ans = ans
            for g in groups:
                if compare(ans, g[0], "", base) < SAME_NATS:
                    g[1] += mass
                    g[2].append(k)
                    break
            else:
                groups.append([ans, mass, [k]])
        total = sum(m for _, m in cont)
        # "none of these": a cause nobody proposed would not be expected to give the specific answer the leader
        # predicts, when that answer differs from the no-fault answer; such a check can confirm the leader
        # against it (the ledger lowers "none of these" when the answer comes back as the leader predicted).
        # A check that would read normal whatever the cause confirms nothing and gains nothing from it.
        if isinstance(lead_ans, dict) and "status" in lead_ans:
            distinct = False                     # a test that would not complete confirms nothing
        elif base is not None:
            distinct = lead_ans is not None and compare(lead_ans, base, "", base) >= SAME_NATS
        else:
            # a check read by its documented meaning: the leader's answer is distinctive when it is a finding
            # (cleared, dirty, failed, mismatched), one that would count against a cause anywhere else
            distinct = (isinstance(lead_ans, dict) and
                        (direct(ch, p, lead_ans, "__elsewhere__", "NONE_OF_THESE", self.kg) or 0.0) > 0.0)
        if p_none > 0 and distinct:
            groups.append([None, p_none, [NONE_KEY]])
            total += p_none
        known = total - unknown
        gain = (known / total) * _entropy([g[1] for g in groups]) if groups and total > 0 else 0.0
        return gain, groups, unknown, base

    def _answer_groups(self, groups, ch: str, p: dict, base, unknown: float) -> dict:
        """The answer groups in the form can_decide() reads, with what "none of these" is charged under each."""
        # what "none of these" is charged under each answer, as the check's reading would charge it
        none_cost = []
        for g in groups:
            if g[0] is None:
                none_cost.append(0.0)
            elif base is not None:
                none_cost.append(SAME_NATS if compare(g[0], base, "", base) >= SAME_NATS else 0.0)
            else:
                none_cost.append(float(direct(ch, p, g[0], "__elsewhere__", "NONE_OF_THESE", self.kg) or 0.0)
                                 if isinstance(g[0], dict) else 0.0)
        return {"members": [list(g[2]) for g in groups], "none_cost": none_cost,
                "unknown_mass": round(unknown, 4)}

    def gate_refuses(self, ledger, ch: str, p: dict, visited: set) -> Optional[dict]:
        """The "can this decide?" step applied to one check (amendment 13): None when the check is admissible; else
        the refusal record, as recommend() records it. Only checks of decide_gate[0] hours or more are tested."""
        if self.decide_gate is None:
            return None
        eff = self.effort_h(ch, p, visited)
        if eff < self.decide_gate[0]:
            return None
        cont = self.contenders(ledger)
        p_none = ledger.posterior().get(NONE_KEY, 0.0)
        gain, groups, unknown, base = self._assess(ledger, cont, ch, p, visited, p_none)
        from .loop import can_decide
        cd = can_decide(ledger, self._answer_groups(groups, ch, p, base, unknown))
        if cd["p_two"] >= self.decide_gate[1]:
            return None
        return {"check": ch, "params": p, "effort_h": round(eff, 2), "p_one": cd["p_one"], "p_two": cd["p_two"]}

    def recommend(self, ledger, budget_left_h: float, done: list[dict], visited: set) -> Optional[dict]:
        self.last_groups = None
        self.last_refused = []
        cont = self.contenders(ledger)
        if len(cont) < 2:
            return None
        # a check that completed, or that cannot be made here (unavailable), is not proposed again
        stood = {(d["check"], tuple(sorted(d["params"].items()))) for d in done
                 if d.get("status") in ("done", "unavailable")}
        p_none = ledger.posterior().get(NONE_KEY, 0.0)
        failed = [(d["check"], tuple(sorted(d["params"].items()))) for d in done if d.get("status") == "failed"]
        cands: dict = {}
        for k, _ in cont:
            h = ledger.hypothesis(k)
            for c in h.causes:
                loc = c.location.element if c.location.kind == "element" else getattr(c.location, "span", None)
                if loc is None:
                    continue
                extra = self.extra_checks(self.kg, loc) if self.extra_checks is not None else []
                for ch, p in checks_for(self.kg, loc) + extra:
                    sig = (ch, tuple(sorted(p.items())))
                    if sig in stood or failed.count(sig) >= 2:
                        continue
                    cands[sig] = (ch, p)
        best = None
        rows = []
        for sig, (ch, p) in cands.items():
            eff = self.effort_h(ch, p, visited)
            if eff > budget_left_h:
                continue
            gain, groups, unknown, base = self._assess(ledger, cont, ch, p, visited, p_none)
            score = gain / max(eff, 1.0 / 60.0)
            rows.append({"check": ch, "params": p, "gain_bits": round(gain, 3), "effort_h": round(eff, 2),
                         "score": round(score, 3), "groups": len(groups)})
            if gain >= MIN_GAIN_BITS and (best is None or score > best["score"]):
                lg = self._answer_groups(groups, ch, p, base, unknown)
                if self.decide_gate is not None and eff >= self.decide_gate[0]:
                    from .loop import can_decide
                    cd = can_decide(ledger, lg)
                    rows[-1]["p_two"] = cd["p_two"]
                    if cd["p_two"] < self.decide_gate[1]:
                        rows[-1]["refused"] = "cannot decide"
                        self.refused.append({"check": ch, "params": p, "effort_h": round(eff, 2),
                                             "p_one": cd["p_one"], "p_two": cd["p_two"]})
                        self.last_refused.append(self.refused[-1])
                        continue
                best = rows[-1]
                self.last_groups = lg
        self.trace.append({"contenders": [(k, round(p, 4)) for k, p in cont], "evaluated": len(rows),
                           "best": best, "top": sorted(rows, key=lambda r: -r["score"])[:5]})
        return best
