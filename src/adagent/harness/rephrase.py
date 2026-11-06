"""Questions as operators put them (amendment 16, the question-interpretation test).

The test bed poses each case as a templated question: a kind (customer or operations), a text, an anchor (a
service or element identifier) and a reported time in simulation seconds. Operators do not write that way. This
module rewrites a case's question deterministically (seeded by the case) into one of several operator-style forms:
clock times instead of simulation seconds, a service named by its rate and end sites or by its identifier, an
element named by its role and place ("amplifier 2 counting from S07 on the S07 to S03 section", "the receiver at
the S15 end of wavelength och203"), the alarm or the reading described in words. The rewriting is written by hand,
not by a model, so the test of an LLM reading these questions is not circular. Every rewrite is resolvable to the
original anchor from the network's own records: where a description is not unique, it names the identifier.

The clock: t = 0 s is 14:00:00 on the day of the case.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

CLOCK0_MIN = 14 * 60


def clock(t_s: float, jitter_min: int = 0) -> str:
    m = int(round(t_s / 60.0)) + CLOCK0_MIN + jitter_min
    return f"{m // 60:02d}:{m % 60:02d}"


@dataclass
class Rewritten:
    text: str
    form: str            # which form was used
    now_clock: str       # the time of asking, on the clock


def _amp_words(amp: str, rng: random.Random) -> str:
    m = re.fullmatch(r"oms\.(S\d+)-(S\d+)\.amp(\d+)", amp)
    a, b, n = m.group(1), m.group(2), int(m.group(3))
    if n == 0 and rng.random() < 0.5:
        return f"the booster amplifier leaving {a} on the {a} to {b} section"
    return rng.choice([f"amplifier {n + 1} counting from {a} on the {a} to {b} section",
                       f"the {_ordinal(n + 1)} amplifier out of {a} towards {b}",
                       f"{amp}"])


def _ordinal(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
            9: "ninth"}.get(n, f"{n}th")


def _trx_words(trx: str, net, rng: random.Random) -> str:
    m = re.fullmatch(r"trx\.(och\d+)\.([az])", trx)
    if not m:
        return trx
    och, end = m.group(1), m.group(2)
    lp = net.lightpaths.get(och)
    if lp is None:
        return trx
    site = lp.nodes[0] if end == "a" else lp.nodes[-1]
    return rng.choice([f"the transponder at the {site} end of wavelength {och}",
                       f"{och}'s receiver at {site}",
                       f"{trx}"])


def element_words(el: str, net, rng: random.Random) -> str:
    if el.startswith("oms.") and ".amp" in el:
        return _amp_words(el, rng)
    if el.startswith("trx."):
        return _trx_words(el, net, rng)
    return el


def _service_words(sid: str, net, rng: random.Random) -> str:
    s = net.services[sid]
    same = [x for x in net.services.values() if x.rate == s.rate and {x.src, x.dst} == {s.src, s.dst}]
    if len(same) == 1 and rng.random() < 0.7:
        return rng.choice([f"their {s.rate} circuit between {s.src} and {s.dst}",
                           f"the {s.rate} service from {s.src} to {s.dst}"])
    return rng.choice([f"service {sid} ({s.rate}, {s.src} to {s.dst})", f"circuit {sid}"])


def rewrite(question, net, now_s: float, seed: int) -> Rewritten:
    rng = random.Random(seed * 7919 + 17)
    t = question.text
    now_c = clock(now_s)
    jit = rng.choice([-2, -1, 0, 1, 2])
    if question.kind == "customer":
        what = ("is down" if "loss of service" in t else
                "had a short outage" if "brief interruption" in t else "is taking errors")
        svc = _service_words(question.anchor, net, rng)
        form = rng.choice(["ticket", "call", "terse"])
        if form == "ticket":
            txt = (f"Ticket from the customer: {svc} {what}, started roughly {clock(question.reported_at_s, jit)}. "
                   "Can you find out why?")
        elif form == "call":
            txt = (f"Customer on the phone says {svc} {what} since about {clock(question.reported_at_s, jit)}. "
                   "What's going on?")
        else:
            txt = f"{svc} {what} since ~{clock(question.reported_at_s, jit)}, customer complaint. Cause?"
        return Rewritten(txt, "customer-" + form, now_c)
    m = re.match(r"Alarm (\S+) \((\w+)\) was raised on (\S+) at about t=(\d+) s", t)
    if m:
        alarm, sev, el = m.group(1), m.group(2), m.group(3)
        where = element_words(el, net, rng)
        form = rng.choice(["noc", "terse"])
        if form == "noc":
            txt = (f"We have a {sev} {alarm} on {where}, came in at about {clock(question.reported_at_s, jit)}. "
                   "What is causing it?")
        else:
            txt = f"{alarm} ({sev}) on {where} since {clock(question.reported_at_s, jit)} - root cause?"
        return Rewritten(txt, "alarm-" + form, now_c)
    m = re.match(r"Performance monitoring shows degraded signal quality on service (\S+)", t)
    if m:
        svc = _service_words(question.anchor, net, rng)
        txt = (f"PM is showing degraded signal quality on {svc} from about {clock(question.reported_at_s, jit)}, "
               "no customer complaint yet. Why?")
        return Rewritten(txt, "pm", now_c)
    m = re.match(r"Monitoring shows the (.+?) reported by (\S+) (changed by about ([+-]?[\d.]+) dB|unchanged)", t)
    if m:
        what, el = m.group(1), m.group(2)
        where = element_words(el, net, rng)
        if m.group(4):
            txt = (f"The {what} readings from {where} jumped by about {abs(float(m.group(4))):.0f} dB around "
                   f"{clock(question.reported_at_s, jit)}. No alarm. What happened?")
        else:
            txt = (f"The {what} readings from {where} have been flat since about {clock(question.reported_at_s, jit)} "
                   "while the readings around them move. Why?")
        return Rewritten(txt, "monitoring", now_c)
    m = re.match(r"Monitoring shows the (.+?) (?:of amplifier (\S+)|of (\S+)|on the channel received at (\S+)) rising "
                 r"over (.+?), with no alarm", t)
    if m:
        el = m.group(2) or m.group(3) or m.group(4)
        where = element_words(el, net, rng)
        quantity = m.group(1)
        txt = (f"Trend alert, no alarm, nothing affected yet: the {quantity} on {where} has been creeping up for "
               f"{m.group(5)}. What's happening, and do we need to act?")
        return Rewritten(txt, "drift", now_c)
    return Rewritten(t, "verbatim", now_c)
