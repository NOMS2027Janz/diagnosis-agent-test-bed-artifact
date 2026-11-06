"""Baseline B-ac: today's practice as an operator runs it, alarm correlation handed to manual troubleshooting.

The management system's correlation engine (AlarmCorrelation, the AC arm) reports a root alarm and its documented
probable causes. The engineer takes that hand-off: the alarm-clearing procedure for the root alarm is started at the
root alarm's unit (not the alarm the procedures' own order of precedence would pick, as in arm B), and followed as
written, every test made through the check interface with its effort and travel counted, exactly as in arm B.

When the library has no procedure for the root alarm, the engineer has only correlation's list: the outcome is that
list (a commitment when it names one cause, otherwise a set), with no effort. When correlation has no root (no alarm),
the procedures' own starting points apply (a degraded receiver, an errored client port, the question's service), as
in arm B. The correlation's probable causes are recorded beside the procedure's result in the trace.
"""
from __future__ import annotations

from typing import Optional

from .baseline import ManualTroubleshooting
from .correlation import AlarmCorrelation
from .procedures import graph_element


class ManualFromCorrelation(ManualTroubleshooting):
    name, version = "baseline.manual_from_correlation", "0.1"

    def __init__(self, substrate, library, grounding=None):
        super().__init__(substrate, library, grounding)
        self.ac = AlarmCorrelation(substrate.kg)
        self.handoff: dict = {}

    def select(self, frame) -> Optional[tuple]:
        pay = self.ac.run(frame)
        root = self.ac.trace.get("root")
        self.handoff = {"root": list(root) if root else None,
                        "probable_causes": [c.hypothesis.key() for c in pay.candidates]}
        if root is None:                                   # no alarm: the procedures' own starting points
            self.handoff["start"] = "procedures' order (no alarm)"
            return self._fallback(frame)
        alarm, el = root[0], root[1]
        pid = self.lib.for_alarm(alarm)
        if pid is None:
            self.handoff["start"] = "no procedure for the root alarm"
            return None
        self.handoff["start"] = f"{alarm} at {el}"
        return pid, graph_element(el), f"{alarm} at {el} (correlation's root)"

    def _fallback(self, frame):
        sel = [s for s in self.lib.selection if "alarm" not in s]
        saved, self.lib.selection = self.lib.selection, sel
        try:
            return super().select(frame)
        finally:
            self.lib.selection = saved

    def diagnose(self, frame) -> dict:
        out = super().diagnose(frame)
        out["trace"]["handoff"] = self.handoff
        if out["outcome"] == "no_procedure" and self.handoff.get("probable_causes"):
            pcs = self.handoff["probable_causes"]
            from .baseline import _split
            from .walks import single
            out["hypotheses"] = {k: single(*_split(k)) for k in pcs}
            out["candidates"] = [(k, round(1 / len(pcs), 4)) for k in pcs]
            if len(pcs) == 1:
                out.update(outcome="commit", hypothesis=pcs[0])
            else:
                out.update(outcome="set", members=pcs)
        return out
