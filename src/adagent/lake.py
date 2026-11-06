"""The data lake: every observation of the network, indexed for query ("Reference Network, Data Sets
and Knowledge Graph" §4).

It is shared infrastructure: the world simulator writes it, and the agent reads it only through the
substrate interface. It contains observations, never truth.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Observation:
    id: str
    source: str           # alarm | event | pm_counter | telemetry | instrument | context
    observable: str
    element: str
    instrument: Optional[str]
    t_start: float
    t_end: Optional[float]
    value: object
    sigma: Optional[float] = None
    period: Optional[float] = None   # for a chunk of a fast stream: sample period, value is a list
    seq: Optional[object] = None     # capability ladder L1: the stream's sample counter (a list for a chunk)


@dataclass
class Lake:
    """Observations with indexes by observable, by (observable, element) and by source."""
    observations: list[Observation] = field(default_factory=list)
    _by_obs: dict = field(default_factory=dict, repr=False)
    _by_obs_el: dict = field(default_factory=dict, repr=False)
    _by_src: dict = field(default_factory=dict, repr=False)

    def add(self, **kw) -> Observation:
        o = Observation(id=f"ob{len(self.observations)}", **kw)
        self.observations.append(o)
        self._by_obs.setdefault(o.observable, []).append(o)
        self._by_obs_el.setdefault((o.observable, o.element), []).append(o)
        self._by_src.setdefault(o.source, []).append(o)
        return o

    def query(self, observable: Optional[str] = None, element: Optional[str] = None,
              t0: float = -math.inf, t1: float = math.inf, source: Optional[str] = None) -> list[Observation]:
        if observable is not None and element is not None:
            pool = self._by_obs_el.get((observable, element), [])
        elif observable is not None:
            pool = self._by_obs.get(observable, [])
        elif source is not None:
            pool = self._by_src.get(source, [])
        else:
            pool = self.observations
        return [o for o in pool
                if (observable is None or o.observable == observable)
                and (element is None or o.element == element)
                and (source is None or o.source == source)
                and o.t_start < t1 and (o.t_end if o.t_end is not None else o.t_start) >= t0]

    def elements(self, observable: str) -> set[str]:
        return {el for (ob, el) in self._by_obs_el if ob == observable}


