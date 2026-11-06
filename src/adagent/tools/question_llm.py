"""Reading a question as an operator puts it (amendment 16): the LLM reader and a coded reader.

Both map a free-text question (harness/rephrase.py) onto the structured question the agent starts from: its kind
(customer or operations), its anchor (the service or element identifier it is about) and the reported time in
simulation seconds. Both may look things up in the network's records through the same four lookups: services
between two sites, the end sites of a lightpath, the amplifiers of a section in order, and whether an identifier
exists. The clock is stated: t = 0 s is 14:00:00, and the time of asking is given.

The coded reader is written as an engineer would write a parser for such text: identifiers by pattern, clock times
by pattern, sites and rates by pattern, the role words of the rewriter's vocabulary by keyword. It does not see the
rewriter's code. Neither reader sees the original question.
"""
from __future__ import annotations

import json
import re
from typing import Literal, Optional

from pydantic import BaseModel

# The clock's origin, 14:00 on the day of the case. Stated here, not imported from the harness: the agent side never
# imports the harness (tests/test_equivalence.py). tests/test_amendment16.py checks it equals harness/rephrase.py's.
CLOCK0_MIN = 14 * 60

PROMPT_VERSION = "question-reader-0.1"
MAX_TURNS = 8

SYSTEM = """You read questions that network operations staff and customers put to a diagnosis system for an \
optical transport network, and turn each into the structured question the system starts from. You do not diagnose.
The structured question has: kind ("customer" when it comes from a customer about their service, "operations" \
otherwise); anchor (the identifier of the one service or network element the question is about: a service such as \
svc155, or an element such as trx.och203.z, the transponder at one end of a wavelength, where .a is the end at the \
lightpath's first site and .z at its last; or oms.S07-S03.amp1, the amplifiers of the section from S07 to S03 \
numbered from 0 in order from S07); and reported_at_s (when the problem was noticed or began, in seconds of the \
system clock: t = 0 s is 14:00:00 today; when the question gives no time, use the time of asking).
Use the lookups to resolve names to identifiers; never invent an identifier. Answer when you are sure."""

TOOLS = [
    {"type": "function", "function": {"name": "find_services", "description": "Services between two sites (either direction), optionally of one rate (for example 10GE, 100GE).",
        "parameters": {"type": "object", "properties": {"site_a": {"type": "string"}, "site_b": {"type": "string"}, "rate": {"type": "string"}}, "required": ["site_a", "site_b"]}}},
    {"type": "function", "function": {"name": "lightpath_ends", "description": "A lightpath's first and last site and its transponders.",
        "parameters": {"type": "object", "properties": {"lightpath": {"type": "string"}}, "required": ["lightpath"]}}},
    {"type": "function", "function": {"name": "section_amplifiers", "description": "The amplifiers of the section from one site to the next, in order from the first site.",
        "parameters": {"type": "object", "properties": {"site_from": {"type": "string"}, "site_to": {"type": "string"}}, "required": ["site_from", "site_to"]}}},
    {"type": "function", "function": {"name": "exists", "description": "Whether a service or element identifier exists, and what it is.",
        "parameters": {"type": "object", "properties": {"identifier": {"type": "string"}}, "required": ["identifier"]}}},
]


class ReadQuestion(BaseModel):
    kind: Literal["customer", "operations"]
    anchor: str
    reported_at_s: float
    note: str


class Lookups:
    def __init__(self, net):
        self.net = net

    def find_services(self, site_a, site_b, rate=None):
        out = [{"service": s.id, "rate": s.rate, "src": s.src, "dst": s.dst, "lightpath": s.lightpath}
               for s in self.net.services.values() if {s.src, s.dst} == {site_a, site_b}
               and (not rate or s.rate.lower() == str(rate).lower())]
        return {"services": out}

    def lightpath_ends(self, lightpath):
        lp = self.net.lightpaths.get(lightpath)
        if lp is None:
            return {"error": "no such lightpath"}
        return {"first_site": lp.nodes[0], "last_site": lp.nodes[-1], "a_end": lp.src_trx, "z_end": lp.dst_trx}

    def section_amplifiers(self, site_from, site_to):
        oms = f"oms.{site_from}-{site_to}"
        amps = sorted((a for a in self.net.amps if a.startswith(oms + ".amp")), key=lambda a: int(a.rsplit("amp", 1)[1]))
        return {"section": oms, "amplifiers_in_order": amps} if amps else {"error": "no such section"}

    def exists(self, identifier):
        n = self.net
        if identifier in n.services:
            return {"exists": True, "is": "service"}
        if identifier in n.amps:
            return {"exists": True, "is": "amplifier"}
        if identifier in n.transponders:
            return {"exists": True, "is": "transponder"}
        if identifier in n.lightpaths:
            return {"exists": True, "is": "lightpath"}
        return {"exists": identifier in getattr(n, "oms", {}) or identifier in getattr(n, "spans", {}),
                "is": "section or span" if identifier in getattr(n, "oms", {}) or identifier in getattr(n, "spans", {}) else None}

    def call(self, name, args):
        return getattr(self, name)(**args)


class LLMQuestionReader:
    def __init__(self, client, net):
        self.client, self.look = client, Lookups(net)
        self.trace: list = []

    def read(self, text: str, now_s: float, now_clock: str) -> ReadQuestion:
        session = self.client.tool_session("question_reader", PROMPT_VERSION, SYSTEM, TOOLS)
        calls = session.start(json.dumps({"question": text, "time_of_asking": now_clock, "time_of_asking_s": now_s}))
        for turn in range(MAX_TURNS):
            if not calls:
                break
            res = {}
            for c in calls:
                try:
                    args = json.loads(c.arguments or "{}")
                    r = self.look.call(c.name, args)
                except Exception as e:  # noqa: BLE001
                    args, r = {}, {"error": repr(e)[:200]}
                self.trace.append({"turn": turn, "tool": c.name, "args": args})
                res[c.id] = json.dumps(r)
            calls = session.submit(res)
        return session.finish("Now give the structured question.", ReadQuestion)


# ------------------------------------------------------------------------------------------- the coded reader
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
_SITE = re.compile(r"\bS\d{2}\b")
_RATE = re.compile(r"\b(\d+GE|\d+G|OTU\d\w*|STM-\d+)\b", re.I)


def coded_read(text: str, net, now_s: float) -> dict:
    """Returns {"kind", "anchor", "reported_at_s"}; anchor None when the text cannot be resolved."""
    look = Lookups(net)
    low = text.lower()
    kind = "customer" if "customer" in low and "no customer complaint" not in low else "operations"
    m = _CLOCK.search(text)
    t = (int(m.group(1)) * 60 + int(m.group(2)) - CLOCK0_MIN) * 60.0 if m else now_s
    anchor = None
    for pat in (r"\b(svc\d+)\b", r"\b(trx\.och\d+\.[az])\b", r"\b(oms\.S\d+-S\d+\.amp\d+)\b"):
        x = re.search(pat, text)
        if x:
            anchor = x.group(1)
            break
    if anchor is None:
        sites = _SITE.findall(text)
        och = re.search(r"\b(och\d+)\b", text)
        if och and sites:
            e = look.lightpath_ends(och.group(1))
            if "error" not in e:
                anchor = e["a_end"] if sites[0] == e["first_site"] else e["z_end"] if sites[0] == e["last_site"] else None
        elif re.search(r"amplifier|booster", low) and len(sites) >= 2:
            amps = look.section_amplifiers(sites[0], sites[1]).get("amplifiers_in_order", [])
            n = 0
            k = re.search(r"amplifier (\d+) counting", low)
            o = re.search(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth) amplifier", low)
            if k:
                n = int(k.group(1)) - 1
            elif o:
                n = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth"].index(o.group(1))
            anchor = amps[n] if 0 <= n < len(amps) else None
        elif len(sites) >= 2:
            r = _RATE.search(text)
            svcs = look.find_services(sites[0], sites[1], r.group(1) if r else None)["services"]
            anchor = svcs[0]["service"] if len(svcs) == 1 else None
    return {"kind": kind, "anchor": anchor, "reported_at_s": t}
