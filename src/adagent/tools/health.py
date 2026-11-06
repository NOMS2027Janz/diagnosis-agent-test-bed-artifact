"""Health levels: an instrument interpreter of data already held, for slow ageing ("The Diagnosis Agent Test Bed" §4, §9.2).

A drift has no onset in the window the framer examines, and the twin does not model equipment health. What
speaks to it is the level of the health telemetry now: a pump drive current or a laser bias current well above
nominal. For each hypothesis, the documented effects of its fault mode are matched to the observables that see
them (the vocabulary's "sees"): if the hypothesis is an ageing mode whose effect a health stream at its element
sees, the level supports it when high and contradicts it when normal. A hypothesis about an element whose health
level is high, of a mode that does not account for that level, leaves the level unexplained and is marked down a
little. Hypotheses about elements with no health telemetry are not affected. When some proposal accounts for a high
level, a cause nobody proposed is charged UNPROPOSED_NATS, as the twin charges its background an allowance.
"""
from __future__ import annotations

from ..schemas import Hypothesis, LikelihoodPayload, LikelihoodValue

HEALTH_OBS = ("pump_current_pct", "laser_bias_pct")
HIGH, NORMAL = 103.0, 101.5          # per cent of nominal
CONTRADICT_NATS, UNEXPLAINED_NATS = 4.0, 2.0
UNPROPOSED_NATS = 3.0                # a cause nobody proposed, charged when a proposal accounts for a high level
# C4: a high level left unexplained is charged as the twin charges a reading it does not explain, 0.5*min(z^2, Z_CAP^2),
# with z the level's distance from the fleet's median in units of the fleet's own scatter (robust: 1.4826 * MAD over
# every element reporting the stream; most units are healthy). Before C4 it was a flat UNEXPLAINED_NATS.
HEALTH_Z = __import__("os").environ.get("ADAGENT_HEALTH_Z", "1") == "1"
Z_CAP = 4.0
MIN_SCATTER = 0.1                    # per cent: a floor on the fleet scatter


class HealthLevels:
    name, version = "health.levels", "0.1"

    def __init__(self, substrate, prop):
        self.sub, self.kg, self.prop = substrate, substrate.kg, prop
        self.levels: dict = {}
        for ob in HEALTH_OBS:
            for el in substrate.elements_reporting(ob):
                x = substrate.latest(ob, el)
                if x is not None:
                    self.levels[(ob, el)] = float(x.value)
        self.centre, self.scatter = {}, {}
        for ob in HEALTH_OBS:
            xs = sorted(v for (o, _), v in self.levels.items() if o == ob)
            if len(xs) >= 5:
                med = xs[len(xs) // 2]
                mad = sorted(abs(x - med) for x in xs)[len(xs) // 2]
                self.centre[ob], self.scatter[ob] = med, max(1.4826 * mad, MIN_SCATTER)

    def _sees(self, mode: str, el: str) -> list[str]:
        edge = self.prop.fault_edge(mode, el)
        if edge is None:                  # a mode documented only for another vendor's equipment of this class
            ty = self.kg.type_of(el)
            cls_ = self.kg.g.nodes[ty].get("element_class") if ty else None
            for t2, a in self.kg.g.nodes(data=True):
                if a.get("cls") == "EquipmentType" and a.get("element_class") == cls_:
                    edge = next((d for m, d in self.kg.out(t2, "hasFaultMode") if m == mode), None)
                    if edge:
                        break
        effects = {e["effect"] for e in (edge or {}).get("effects", [])}
        return [ob for ob in HEALTH_OBS if self.prop.obs_sees.get(ob, set()) & effects]

    def likelihood(self, hyps: list[Hypothesis]) -> LikelihoodPayload:
        vals = {}
        accounted = False
        for h in hyps:
            ll = 0.0
            for c in h.causes:
                el = c.location.element if c.location.kind == "element" else None
                if el is None:
                    continue
                el = self.kg.g.nodes[el].get("lake_element", el) if self.kg.has(el) and \
                    self.kg.cls(el) == "Instrument" else el
                mine = {ob: v for (ob, e), v in self.levels.items() if e == el}
                if not mine:
                    continue
                seen = self._sees(c.fault_mode, el)
                for ob, v in mine.items():
                    if ob in seen:
                        if v <= NORMAL:
                            ll -= CONTRADICT_NATS
                        elif v >= HIGH:
                            accounted = True
                    elif v >= HIGH:
                        if HEALTH_Z and ob in self.scatter:
                            z = (v - self.centre[ob]) / self.scatter[ob]
                            ll -= 0.5 * min(z * z, Z_CAP ** 2)
                        else:
                            ll -= UNEXPLAINED_NATS
            vals[h.key()] = LikelihoodValue(log_likelihood=ll)
        return LikelihoodPayload(values=vals, background_log_likelihood=-UNPROPOSED_NATS if accounted else 0.0)
