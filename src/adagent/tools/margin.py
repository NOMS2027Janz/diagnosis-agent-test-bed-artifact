"""Margin levels: an interpreter of data already held, for a slow loss of margin at a receiver (C4, amendment 8).

A slow loss of margin has no onset in the window the framer examines, so the twin, which judges changes at an
onset, cannot see it. What speaks to it is the level now: the pre-FEC bit error rate a receiver reports before the
onset, against the twin's prediction of it with no fault, built from the records. The operator states the twin's
accuracy on a lightpath's margin (knowledge/practice/twin.yaml, stated_accuracy.margin_db_1sigma); on log10 BER that
is margin_db_1sigma x BER_DECADES_PER_DB. A receiver whose rate is worse than predicted by more than HIGH_SIGMA of
that accuracy shows a loss of margin; one within NORMAL_SIGMA of it shows none.

As the health levels do for pump and laser ageing: a hypothesis of an ageing mode whose documented effect the
receiver's bit error rate sees is supported where the receiver shows a loss and contradicted where it shows none; a
hypothesis about that receiver (or an instrument of it) that does not account for a loss it shows is charged as the
twin charges a reading it leaves unexplained (half the square of the deviation in units of the stated accuracy,
capped at Z_CAP); and when some proposal accounts for a loss, a cause nobody proposed is charged UNPROPOSED_NATS. Receivers
with no loss and hypotheses elsewhere are not affected.
"""
from __future__ import annotations

from ..schemas import Hypothesis, LikelihoodPayload, LikelihoodValue
from .health import CONTRADICT_NATS, UNPROPOSED_NATS
from .twin import Z_CAP

HIGH_SIGMA, NORMAL_SIGMA = 3.0, 1.0
OBS = "pre_fec_ber"
# Refinement after the freeze (amendment 24, report only): judge each receiver against the fleet, as the health levels
# do. The median deviation over every receiver is taken as a shift common to the twin and removed before the
# thresholds apply, so a bias the twin carries everywhere does not read as lost margin everywhere. Off by default.
FLEET = __import__("os").environ.get("ADAGENT_MARGIN_FLEET", "0") == "1"


class MarginLevels:
    name, version = "margin.levels", "0.1"

    def __init__(self, twin, kg, prop, margin_db_1sigma: float, ber_decades_per_db: float):
        self.kg, self.prop = kg, prop
        self.sigma = margin_db_1sigma * ber_decades_per_db
        self.dev: dict[str, float] = {}
        for k in twin.keys:
            if k[0] != OBS:
                continue
            o, p = twin.obs.cont[k], twin.base.cont[k]
            if o.get("b") is None or p.get("b") is None:
                continue
            self.dev[str(k[1])] = float(o["b"] - p["b"])        # decades worse (+) than predicted, before onset
        self.offset = 0.0
        if FLEET and self.dev:
            import statistics
            self.offset = statistics.median(self.dev.values())
            self.dev = {r: d - self.offset for r, d in self.dev.items()}
        self.lost = {r for r, d in self.dev.items() if d > HIGH_SIGMA * self.sigma}

    def _accounts(self, mode: str, el: str) -> bool:
        """An ageing mode whose documented effect the receiver's bit error rate sees."""
        edge = self.prop.fault_edge(mode, el)
        if edge is None:                  # a mode documented only for another vendor's equipment of this class
            ty = self.kg.type_of(el)
            cls_ = self.kg.g.nodes[ty].get("element_class") if ty else None
            for t2, a in self.kg.g.nodes(data=True):
                if a.get("cls") == "EquipmentType" and a.get("element_class") == cls_:
                    edge = next((d for m, d in self.kg.out(t2, "hasFaultMode") if m == mode), None)
                    if edge:
                        break
        if not edge or edge.get("nature") != "incipient":
            return False
        effects = {e["effect"] for e in edge.get("effects", [])}
        return bool(self.prop.obs_sees.get(OBS, set()) & effects)

    def likelihood(self, hyps: list[Hypothesis]) -> LikelihoodPayload:
        vals, accounted = {}, False
        for h in hyps:
            ll = 0.0
            for c in h.causes:
                el = c.location.element if c.location.kind == "element" else None
                if el is None:
                    continue
                if self.kg.has(el) and self.kg.cls(el) == "Instrument":
                    el = self.kg.g.nodes[el].get("lake_element", el)
                if el not in self.dev:
                    continue
                d = self.dev[el]
                if self._accounts(c.fault_mode, el):
                    if el in self.lost:
                        accounted = True
                    elif d <= NORMAL_SIGMA * self.sigma:
                        ll -= CONTRADICT_NATS
                elif el in self.lost:
                    # the loss left unexplained is charged as the twin charges a reading it does not explain
                    z = d / self.sigma
                    ll -= 0.5 * min(z * z, Z_CAP ** 2)
            vals[h.key()] = LikelihoodValue(log_likelihood=ll)
        return LikelihoodPayload(values=vals, background_log_likelihood=-UNPROPOSED_NATS if accounted else 0.0)
