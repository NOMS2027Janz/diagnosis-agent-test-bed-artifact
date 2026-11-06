"""Could an affordable check have told the contenders apart? (harness side; "The Diagnosis Agent Test Bed" §8.5, §10)

When the agent ends a case with "I don't know", the abstention is justified only if no check within the effort
budget left could have separated the causes still in contention. For each contender other than the truth, its
best-fitting parameters are found (as for the equivalence classes, with any other causes held fixed); then every
check the agent's own catalogue offers at the contenders' locations (tools.advisor.checks_for, read from the
graph) is performed, noise-free, in a world with the truth, a world with the contender and a world with no fault,
at the time it would complete (the agent's stop, plus travel to new sites and the check's minutes), after the
repairs made by the agent's own checks. A check separates the pair when the two answers are not alike by the one
standard the agent also uses (accuracy.alike: the documented accuracy of the instrument and of the twin, with the
no-fault answer setting the size of a predicted change). Its effort must fit the budget left.

The same machinery says whether checks the agent actually made separated the members of the truth's equivalence
class (unseparated), which decides whether a correct commitment was ambiguous.
"""
from __future__ import annotations

import copy
import math
from typing import Optional

from ..tools.advisor import checks_for
from ..accuracy import alike
from ..world.checks import check_sites, check_spec, perform, travel_hours
from ..world.simulator import WorldSim
from .equivalence import Signature, _events, _fit_with, _targets

def _same(a, b, base=None) -> bool:
    """Whether the agent's tools could not tell answer a from answer b (b in the role of the prediction): the one
    standard the agent itself uses (accuracy.alike: the instrument's and the twin's accuracy, as documented)."""
    return alike(a, b, base)


def _world(case, events):
    """A fresh world with the events. Each event is copied: checks that repair (inspect_clean, swap_unit) shorten
    the event they find, and the same event list is reused for many checks."""
    w0 = case.world
    w = WorldSim(w0.net, seed=w0.seed, deployment=w0.deployment, ladder=getattr(w0, "ladder", frozenset()))
    for e in events:
        w.inject(copy.copy(e))
    return w


def _faults(case):
    return getattr(case.truth, "faults", None) or [case.truth.fault]


def _truth_events(case, exclude: Optional[tuple] = None) -> list:
    """Every truth event, leaving out the targets of fault index exclude[0] listed in exclude[1]."""
    out = []
    for i, f in enumerate(_faults(case)):
        tg = [t for t in f.targets if not (exclude and i == exclude[0] and t in exclude[1])]
        out += _events(f.mode, tg, f.params, f.onset_s, f.duration_s)
    return out


def alternative_events(case, key: str, part: Optional[tuple] = None):
    """The events of a world in which cause `key` (fault-mode@location, at its best-fitting parameters) replaces
    part of the truth; part = (fault index, targets replaced), default the whole of the first fault. Returns None
    when the data already held tell the cause from the truth (no parameter value fits)."""
    w = case.world
    net, dep = w.net, w.deployment
    faults = _faults(case)
    part = part or (0, list(faults[0].targets))
    t_end = case.substrate.now
    target = Signature(net, dep, _truth_events(case), t_end)
    fixed = _truth_events(case, exclude=part)
    ref = faults[part[0]]
    mode, el = key.split("@", 1)
    m, params, dur = _fit_with(net, dep, target, fixed, mode, el, ref, t_end, return_params=True)
    if m == math.inf:
        return None
    return fixed + _events(mode, _targets(net, mode, el), params, ref.onset_s, dur)


REPAIRING = ("inspect_clean", "swap_unit")


def _answer(case, events, check, cp, t, prior=(), cache=None):
    """The check's answer in a world with these events at case time t, noise-free, after the repairing checks in
    `prior` ((check, params, t), as made by the agent) have repaired what they found. Whether two worlds' answers
    differ is judged by _same against the documented accuracy, not by one draw of the noise (and a remote test that
    might fail is judged by what it shows when it completes)."""
    prior = tuple((c, tuple(sorted(p.items())), tc) for c, p, tc in prior if c in REPAIRING and tc < t)
    k = (check, tuple(sorted(cp.items())), t, prior)
    if cache is not None and k in cache:
        return cache[k]
    w = _world(case, events)
    for c, p, tc in prior:
        perform(w, case.world.deployment, c, dict(p), tc, 0, noise=False, t_question=case.substrate.now)
    r = perform(w, case.world.deployment, check, cp, t, 0, noise=False, t_question=case.substrate.now)
    if cache is not None:
        cache[k] = r
    return r


def done_checks(case) -> list[tuple[str, dict, float]]:
    """The checks the agent made in the case that completed: (check, params, time taken) from the substrate's
    record."""
    out = []
    for a in case.substrate.acquisitions:
        if "check" in a and a.get("status") == "done":
            cp = {k: v for k, v in a.items() if k not in ("check", "status", "minutes", "dispatch_sites", "t")}
            item = (a["check"], cp, a.get("t", case.substrate.now))
            if item not in out:
                out.append(item)
    return out


def unseparated(case, members: list[str], checks: list[tuple[str, dict]], part: Optional[tuple] = None,
                case_seed: int = 0) -> list[str]:
    """The members (other than the truth's own keys) that none of the checks separates from the truth. A member
    the data held already separate (no fit) counts as separated."""
    faults = _faults(case)
    p = part or (0, list(faults[0].targets))
    own = {f"{faults[p[0]].mode}@{t}" for t in p[1]}
    truth_cache: dict = {}
    truth_cache_base: dict = {}
    truth_ev = _truth_events(case)
    left = []
    for key in members:
        if key in own:
            continue
        alt = alternative_events(case, key, p)
        if alt is None:
            continue
        sep = False
        for check, cp, t in checks:
            a = _answer(case, truth_ev, check, cp, t, checks, truth_cache)
            b = _answer(case, alt, check, cp, t, checks)
            base = _answer(case, [], check, cp, t, checks, truth_cache_base)
            if a.status == "done" and b.status == "done" and not _same(a.value, b.value,
                                                                       base.value if base.status == "done" else None):
                sep = True
                break
        if not sep:
            left.append(key)
    return left


def separating_checks(case, contenders: list[str], budget_hours: float = math.inf, visited: Optional[list] = None,
                      case_seed: int = 0, t_s: Optional[float] = None) -> dict:
    """{contender: [(check, params, hours)]} for every contender (fault-mode@location) that some affordable
    check separates from the truth; ("data_held", {}, 0) when the data already held separate it (no parameter
    value makes it look like the truth); [] when nothing within the budget separates it. The checks tried are
    those the agent's catalogue offers (tools.advisor.checks_for) at the contender's and the truth's locations,
    performed at t_s (default the question time; the harness passes the time the agent stopped)."""
    t_s = case.substrate.now if t_s is None else t_s
    kg = case.substrate.kg
    faults = _faults(case)
    truth_keys = {f"{f.mode}@{t}" for f in faults for t in f.targets}
    truth_ev = _truth_events(case)
    truth_cache: dict = {}
    base_cache: dict = {}
    prior = done_checks(case)                  # repairs the agent's checks made are carried forward
    visited = list(visited or [])
    out = {}
    for key in contenders:
        if key in truth_keys:
            continue
        el = key.split("@", 1)[1]
        alt = alternative_events(case, key)
        if alt is None:                    # the data already held tell this contender from the truth
            out[key] = [("data_held", {}, 0.0)]
            continue
        locs = sorted({el} | {t for f in faults for t in f.targets})
        found, seen = [], set()
        for loc in locs:
            # world and graph name elements alike; checks_for maps an instrument to the element it is on
            extra = []
            if "L6" in getattr(case.world, "ladder", ()):          # the capability ladder's cross-check
                from ..tools.ladder import xcheck_for
                extra = xcheck_for(kg, loc)
            for check, cp in checks_for(kg, loc) + extra:
                sig = (check, tuple(sorted(cp.items())))
                if sig in seen:
                    continue
                seen.add(sig)
                hours = check_spec(check)[1] / 60.0 + sum(travel_hours(case_seed, s)
                                                      for s in check_sites(case.world, check, cp) if s not in visited)
                if hours > budget_hours:
                    continue
                t = t_s + 3600.0 * hours           # read when it would complete
                a = _answer(case, truth_ev, check, cp, t, prior, truth_cache)
                b = _answer(case, alt, check, cp, t, prior)
                base = _answer(case, [], check, cp, t, prior, base_cache)
                if a.status != "done" or b.status != "done" or _same(
                        a.value, b.value, base.value if base.status == "done" else None):
                    continue
                found.append((check, cp, round(hours, 2)))
        out[key] = found
    return out
