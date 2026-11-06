"""Baseline B: guided manual troubleshooting ("The Diagnosis Agent Test Bed" §11).

The operator's alarm-clearing procedures (knowledge/procedures/alarm_clearing.yaml) executed as written, the way a
NOC engineer follows them: start from the alarm the selection order names, answer each step from the monitoring
data, and where a step calls for a test, have it done (a remotely triggered test, or a dispatch) and follow the
branch its result selects. The procedures name their tests in words; TEST_CHECKS reads each one against the check
catalogue (knowledge/practice/checks.yaml), as an engineer would: which check, on which unit, and which answer
means "yes". A test the catalogue has no check for is answered from the data held, as the procedure's own text
says (the pump current or laser bias history), or left with both branches open.

The baseline ends where the procedure ends. Its diagnosis is the leaf's causes, grounded on the network; it commits
when one cause carries at least COMMIT of the mass, and otherwise reports the set. Its effort is that of the checks
it had done. The agent must beat this on accuracy, effort or both.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .procedures import ProcedureTool
from .walks import single

COMMIT = 0.8


def _yes_end_of_fibre(v):
    return any(e.get("type") == "end_of_fibre" for e in (v or {}).get("events", []))


def _yes_no_light(v):
    return (v or {}).get("tx_power_dbm") is None


def _yes_clear(v):
    return (v or {}).get("errors_on_spare") == "clear"


# (procedure, step) -> (check, unit role for its parameter, parameter name, predicate for "yes")
TEST_CHECKS = {
    ("P-EQPT-CARD", "s1"): ("swap_unit", "this_unit", "unit", lambda v: v.get("symptoms_after_swap") == "cleared"),
    ("P-EQPT-TX", "s1"): ("tx_power", "this_unit", "trx", _yes_no_light),
    ("P-OTN-SWITCH", "s1"): ("xc_audit", "this_unit", "switch", lambda v: v.get("reachable") is False),
    ("P-EQPT-PORT", "s1"): ("loopback", "this_port", "port", lambda v: v.get("result") == "fail"),
    ("P-AMP-INPUT-LOS", "s4b"): ("otdr", "upstream_span", "span", _yes_end_of_fibre),
    ("P-AMP-INPUT-LOS", "s4c"): ("span_loss", "upstream_span", "span", lambda v: True),
    ("P-AMP-INPUT-LOS", "s5"): ("inspect_clean", "oms_input", "oms", lambda v: "connectors clean" not in v.get("found", "")),
    ("P-OCH-LOS", "s6"): ("tx_power", "far_end_transmitter", "trx", _yes_no_light),
    ("P-SIGNAL-DEGRADED", "s8"): ("retune_spare", "this_unit", "lightpath", _yes_clear),
    ("P-ODU-TIM", "s1"): ("xc_audit", "odu_path_switch", "switch", lambda v: bool(v.get("mismatches"))),
    ("P-CLIENT-SF", "s5"): ("loopback", "either_port", "port", lambda v: v.get("result") == "fail"),
}


class ManualTroubleshooting(ProcedureTool):
    name, version = "baseline.manual", "0.1"

    def __init__(self, substrate, library, grounding=None):
        super().__init__(substrate, library, grounding)
        self.done: list = []

    def _do(self, check: str, params: dict) -> dict:
        r = self.sub.run_check(check, params)
        self.done.append({"check": check, "params": params, "status": r.get("status"), "value": r.get("value"),
                          "minutes": r.get("minutes", 0), "dispatch_sites": r.get("dispatch_sites") or [],
                          "repaired": r.get("repaired")})
        return r

    def _test(self, pid: str, node: str, unit: str) -> Optional[bool]:
        spec = TEST_CHECKS.get((pid, node))
        if spec is None:
            return None
        check, role, pname, yes = spec
        units = self._resolve(role, unit) or [unit]
        kg = self.kg
        answers = []
        for u in units[:4]:
            base = u.split("|")[0]
            if pname == "switch":
                base = base if base.startswith("otn.") else u
                params = {"switch": base.split("|")[0]}
            elif pname == "lightpath":
                lp = next((l for l, _ in kg.inn(base, "endsAt")), None)
                if lp is None:
                    continue
                params = {"lightpath": lp, "end": "z" if kg.lightpath_ends(lp).get("z") == base else "a"}
            else:
                params = {pname: base}
            if check == "otdr":                    # the embedded OTDR where the site has one, else a dispatch
                oms = kg.g.nodes[base].get("oms")
                src = kg.g.nodes[oms]["src"] if oms else None
                full = src and kg.g.nodes.get(f"site.{src}", {}).get("tier") == "full"
                r = self._do("otdr_embedded" if full else "otdr_portable", params)
                if r.get("status") != "done" and full:
                    r = self._do("otdr_portable", params)
            else:
                r = self._do(check, params)
            if r.get("status") == "done":
                answers.append(bool(yes(r.get("value") or {})))
        if not answers:
            return None
        return any(answers)

    def _step(self, frame, pid, node, unit, found, depth, path, out, tests) -> None:
        p = self.lib.procedures[pid]
        if node in p["leaves"]:
            return super()._step(frame, pid, node, unit, found, depth, path, out, tests)
        st = p["steps"][node]
        if "test" not in st["ask"]:
            return super()._step(frame, pid, node, unit, found, depth, path, out, tests)
        answer = self._test(pid, node, unit)
        tests.append({"procedure": pid, "step": node, "test": st["ask"]["test"], "unit": unit, "answer": answer})
        step_path = path + [f"{node}={answer}"]
        if answer is None:
            if "unknown" in st:
                self._step(frame, pid, st["unknown"], unit, found, depth + 1, step_path, out, tests)
            else:
                for o in ("yes", "no"):
                    self._step(frame, pid, st[o], unit, found, depth + 1, step_path, out, tests)
        else:
            self._step(frame, pid, st["yes" if answer else "no"], unit, found, depth + 1, step_path, out, tests)

    def diagnose(self, frame) -> dict:
        payload = self.run(frame)
        mass = defaultdict(float)
        for c in payload.candidates:
            mass[c.hypothesis.key()] += c.prior_mass
        total = sum(mass.values()) + payload.residual_mass
        ranked = sorted(mass.items(), key=lambda kv: -kv[1])
        out = {"trace": {k: v for k, v in self.trace.items() if k != "leaves"}, "checks": self.done,
               "candidates": [(k, round(v / total, 4)) for k, v in ranked[:10]]}
        if ranked and ranked[0][1] / total >= COMMIT:
            out.update(outcome="commit", hypothesis=ranked[0][0])
        elif ranked:
            out.update(outcome="set", members=[k for k, _ in ranked[:5]])
        else:
            out.update(outcome="no_procedure" if self.trace.get("trigger") is None else "set", members=[])
        out["hypotheses"] = {k: single(*_split(k)) for k, _ in ranked[:10]}
        return out


def _split(key: str):
    """'MODE@el:loc()' -> (MODE, loc)."""
    mode, rest = key.split("@", 1)
    loc = rest.split(":", 1)[1].rsplit("(", 1)[0]
    return mode, loc
