"""Checks the world answers on request (world side; "The Diagnosis Agent Test Bed" §9.1, Table 7).

Three classes of check: reading data already held (the substrate, not here), remotely triggered tests (through
the management system, nobody on site), and field tests needing dispatch. Each check here has an availability
rule, a time to result, any service impact, and, for field tests, the sites someone must travel to. The answer
comes from the world as it stands when the check is performed, with instrument noise; the knowledge of what each
check is, what it costs and what it can show lives on the agent side in knowledge/practice/checks.yaml.

A check returns a CheckResult with status "done", "unavailable" (the capability is not deployed there, or the
target does not exist) or "failed" (the test did not complete; about 3 per cent of remote tests). A failed or
unavailable check is not evidence about the network: the agent must treat what it would have shown as unknown.
Some field tests repair what they find (cleaning a dirty connector, replacing a failed card); the repair is
applied to the world and reported, so a later check sees the repaired network.

Effort figures are judgement from general operations practice (decision D3: set here, documented, and varied in
a sensitivity run); travel time to a site is drawn once per case and site, 1 to 4 hours with a median of 2.
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field
from typing import Optional

from .faults import effects_at
from .otn import _hop_dir
from .physics import FEC_BER, evaluate

REMOTE_FAIL_P = 0.03

# name: (class, minutes to result, service impact, description)
CHECKS = {
    "otdr_embedded":   ("remote", 8, "none", "embedded OTDR trace on a span (full-tier sites)"),
    "pm_detail":       ("remote", 2, "none", "1-minute power and errored-second registers of a receiver around the onset"),
    "coherent_diag":   ("remote", 2, "none", "on-demand coherent DSP readout of a receiver: OSNR, BER, CD, DGD, SOP rate"),
    "tx_power":        ("remote", 2, "none", "transmit power reported by a line interface"),
    "xc_audit":        ("remote", 15, "none", "cross-connects of an OTN switch compared with provisioning"),
    "tcm_segments":    ("remote", 10, "none", "tandem connection monitoring on each switch-to-switch segment of an ODU path"),
    "loopback":        ("remote", 10, "interrupts the service if it is up", "loop back a client port and test it"),
    "retune_spare":    ("remote", 30, "interrupts the channel", "move a channel's receiver to a spare port"),
    "otdr_portable":   ("dispatch", 30, "none", "portable OTDR trace on a span, at its upstream site"),
    "power_meter":     ("dispatch", 20, "none", "optical power at an OMS input, at its source site"),
    "inspect_clean":   ("dispatch", 20, "brief hit on the OMS", "inspect and clean the connectors at an OMS input"),
    "span_loss":       ("dispatch", 45, "none", "span loss measured from both ends"),
    "swap_unit":       ("dispatch", 60, "interrupts what the unit carries", "replace an amplifier, line card or client optic"),
}


def check_spec(check: str):
    """(class, minutes, impact, description) of a check: the catalogue's, or a capability ladder rung's
    (world/ladder.LADDER_CHECKS, unavailable unless the world has the rung); None if there is no such check."""
    if check in CHECKS:
        return CHECKS[check]
    from .ladder import LADDER_CHECKS
    return LADDER_CHECKS.get(check)


@dataclass
class CheckResult:
    check: str
    status: str                     # done | unavailable | failed
    value: Optional[dict] = None
    minutes: float = 0.0            # time to result, excluding travel
    dispatch_sites: list = field(default_factory=list)
    service_impact: str = "none"
    repaired: Optional[str] = None  # the fault this check repaired, if any
    t: Optional[float] = None       # when the reading was taken (seconds of case time)


class Exact:
    """A stand-in for a random stream with no randomness: the instrument reads true and the test never fails.
    The twin predicts a check's answer with it; its own calculation error is added separately."""
    def gauss(self, mu, sigma):
        return mu

    def random(self):
        return 1.0

    def randrange(self, n):
        return 0


def travel_hours(case_seed: int, site: str) -> float:
    """Travel time to a site for this case: 1 to 4 hours, median 2 (log-normal, clipped)."""
    r = random.Random(zlib.crc32(f"travel/{case_seed}/{site}".encode()))
    return max(1.0, min(4.0, r.lognormvariate(math.log(2.0), 0.35)))


def _snap(world, t):
    return evaluate(world.net, effects_at(world.events, t))


def check_sites(world, check: str, params: dict) -> list:
    """The sites a check sends a technician to (none for a remote check)."""
    net = world.net
    try:
        if check == "otdr_portable":
            return [net.oms[net.spans[params["span"]].oms].src]
        if check in ("power_meter", "inspect_clean"):
            return [net.oms[params["oms"]].src]
        if check == "span_loss":
            o = net.oms[net.spans[params["span"]].oms]
            return sorted({o.src, o.dst})
        if check == "swap_unit":
            unit = params["unit"]
            return [world.site_of(unit) or unit.rsplit(".", 1)[-1]]
    except KeyError:
        return []
    return []


def perform(world, deployment: dict, check: str, params: dict, t: float, case_seed: int, attempt: int = 0,
            noise: bool = True, t_question: Optional[float] = None) -> CheckResult:
    """Perform a check at case time t. `attempt` counts earlier requests of the same check in the case, so a retried
    test is a fresh trial (it may succeed where the first failed, with fresh instrument noise). With noise False the
    instrument reads true and a remote test never fails: the twin's prediction. The performance-monitoring register
    read by pm_detail holds one-minute bins from 20 minutes before the question (t_question, default t) to t."""
    if check_spec(check) is None:
        return CheckResult(check, "unavailable", {"reason": "no such check"})
    cls_, minutes, impact, _ = check_spec(check)
    rng = random.Random(zlib.crc32(f"check/{case_seed}/{check}/{sorted(params.items())}/{attempt}".encode())) \
        if noise else Exact()
    net = world.net
    res = CheckResult(check, "done", minutes=minutes, service_impact=impact, t=t)
    if cls_ == "remote" and rng.random() < REMOTE_FAIL_P:
        res.status, res.value = "failed", {"reason": "the test did not complete"}
        return res
    try:
        value = _answer(world, deployment, check, params, t, rng, res, t if t_question is None else t_question)
    except KeyError as e:
        return CheckResult(check, "unavailable", {"reason": f"unknown target {e}"}, t=t)
    if value is None:
        return CheckResult(check, "unavailable", {"reason": "capability not deployed there"}, t=t)
    res.value = value
    return res


def _answer(world, deployment, check, params, t, rng, res: CheckResult, t_q: float):
    net = world.net
    if check in ("otdr_embedded", "otdr_portable"):
        span = params["span"]
        site = net.oms[net.spans[span].oms].src
        if check == "otdr_embedded" and deployment.get(site) != "full":
            return None
        if check == "otdr_portable":
            res.dispatch_sites = check_sites(world, check, params)
        return world.acquire_otdr(span, t, rng_seed=rng.randrange(1 << 30), noise=not isinstance(rng, Exact))
    if check == "pm_detail":
        trx = params["trx"]
        rows = []
        for s in world.segments(max(0.0, min(t, t_q) - 1200.0), t):
            for (lp, d), r in s.snap.rx.items():
                if r.trx == trx:
                    rows.append((s.t0, s.t1, r.rx_power_dbm, r.pre_fec_ber))
        if not rows:
            raise KeyError(trx)
        bins = []
        b = rows[0][0] - rows[0][0] % 60
        while b < t:
            inb = [(max(a, b), min(z, b + 60), p, ber) for a, z, p, ber in rows if z > b and a < b + 60]
            if inb:
                es = sum(math.ceil(z - a) for a, z, p, ber in inb if ber >= FEC_BER or p == -math.inf)
                pw = [p for _, _, p, _ in inb]
                cal = net.calibration_db.get(trx, 0.0)
                bins.append({"t": b, "rx_min_dbm": round(min(pw) + cal + rng.gauss(0, 0.1), 2) if min(pw) > -math.inf
                             else None, "errored_s": es})
            b += 60
        return {"trx": trx, "bins": bins}
    if check == "coherent_diag":
        trx = params["trx"]
        snap = _snap(world, t)
        r = next((r for r in snap.rx.values() if r.trx == trx), None)
        if r is None:
            raise KeyError(trx)
        if not math.isfinite(r.rx_power_dbm):
            return {"trx": trx, "signal": "lost"}
        return {"trx": trx, "est_osnr_db": round(r.osnr_db + net.calibration_db.get(trx, 0.0) + rng.gauss(0, 0.3), 2),
                "pre_fec_ber": r.pre_fec_ber * 10 ** rng.gauss(0, 0.1), "cd_ps_nm": round(r.cd_ps_nm),
                "dgd_ps": round(r.dgd_ps + rng.gauss(0, 0.3), 1),
                "sop_rate_rad_s": round(r.sop_rate_rad_s * (1.0 + rng.gauss(0, 0.1))), "margin_db": round(r.margin_db + rng.gauss(0, 0.2), 2)}
    if check == "tx_power":
        trx = params["trx"]
        if trx not in net.transponders:
            raise KeyError(trx)
        fx = effects_at(world.events, t)
        dead = trx in fx.trx_tx_failed or trx in fx.card_failed
        return {"trx": trx, "tx_power_dbm": None if dead else round(net.launch_dbm_per_ch + net.calibration_db.get(trx, 0.0)
                                                                   + rng.gauss(0, 0.1), 2)}
    if check == "xc_audit":
        sw = params["switch"]
        if sw not in net.otn_switches:
            raise KeyError(sw)
        fx = effects_at(world.events, t)
        if sw in fx.otn_switch_failed:
            return {"switch": sw, "reachable": False}
        return {"switch": sw, "reachable": True, "mismatches": sorted(p for s, p in fx.xc_fault if s == sw)}
    if check == "tcm_segments":
        pid = params["path"]
        path = net.odu_paths[pid]
        fx = effects_at(world.events, t)
        snap = _snap(world, t)
        segs = []
        for h in path.hops:
            r = snap.rx.get((h.lightpath, _hop_dir(net, h.lightpath, h.a)))
            bad_line = r is None or not math.isfinite(r.rx_power_dbm) or r.pre_fec_ber >= FEC_BER
            bad_sw = f"otn.{h.z}" in fx.otn_switch_failed or (f"otn.{h.z}", pid) in fx.xc_fault
            segs.append({"from": h.a, "to": h.z, "status": "failed" if (bad_line or bad_sw) else "ok"})
        return {"path": pid, "segments": segs}
    if check == "loopback":
        port = params["port"]
        fx = effects_at(world.events, t)
        return {"port": port, "result": "fail" if port in fx.client_port_failed else "pass"}
    if check == "retune_spare":
        lp = params["lightpath"]
        trx = net.lightpaths[lp].dst_trx if params.get("end", "z") == "z" else net.lightpaths[lp].src_trx
        fx = effects_at(world.events, t)
        local = trx in fx.trx_rx_penalty_db or trx in fx.card_failed
        return {"lightpath": lp, "receiver": trx, "errors_on_spare": "clear" if local else "persist"}
    if check == "power_meter":
        oms = params["oms"]
        res.dispatch_sites = check_sites(world, check, params)
        fx = effects_at(world.events, t)
        loss = fx.oms_input_loss_db.get(oms, 0.0)
        return {"oms": oms, "input_dbm_per_ch": None if loss == math.inf
                else round(net.launch_dbm_per_ch - loss + rng.gauss(0, 0.1), 2)}
    if check == "inspect_clean":
        oms = params["oms"]
        res.dispatch_sites = check_sites(world, check, params)
        dirty = [e for e in world.events if e.target == oms and e.fault_mode == "CONNECTOR_DIRTY" and e.level(t) > 0]
        pulled = [e for e in world.events if e.target == oms and e.fault_mode == "CONNECTOR_PULLED" and e.level(t) > 0]
        for e in dirty + pulled:                       # cleaning or reseating repairs it from now on
            e.duration = max(0.0, t - e.onset)
            res.repaired = f"{e.fault_mode}@{oms}"
        return {"oms": oms, "found": "dirty connector, cleaned" if dirty else "connector unseated, reseated" if pulled
                else "connectors clean"}
    if check == "span_loss":
        span = params["span"]
        sp = net.spans[span]
        o = net.oms[sp.oms]
        res.dispatch_sites = check_sites(world, check, params)
        fx = effects_at(world.events, t)
        extra = fx.span_extra_loss_db.get(span, 0.0)
        return {"span": span, "loss_db": None if extra == math.inf else round(sp.actual_loss_db + extra + rng.gauss(0, 0.1), 2),
                "recorded_loss_db": round(sp.nominal_loss_db, 2)}
    if check == "xcheck_reading":
        from .ladder import xcheck
        return xcheck(world, params, t, rng, t_q)
    if check == "swap_unit":
        unit = params["unit"]
        res.dispatch_sites = check_sites(world, check, params)
        hit = [e for e in world.events if e.target == unit and e.level(t) > 0 and e.fault_mode in (
            "AMP_PUMP_FAIL", "AMP_GAIN_DROP", "AMP_NF_RISE", "AMP_GAIN_TILT", "PUMP_CURRENT_DRIFT", "TX_LASER_FAIL",
            "CARD_FAIL", "RX_DEGRADATION", "LASER_BIAS_DRIFT", "CLIENT_PORT_FAIL")]
        for e in hit:
            e.duration = max(0.0, t - e.onset)
            res.repaired = f"{e.fault_mode}@{unit}"
        return {"unit": unit, "symptoms_after_swap": "cleared" if hit else "unchanged"}
    raise KeyError(check)


def effort(results: list, case_seed: int) -> dict:
    """Total effort of a sequence of checks: hours to result (travel once per site visit, tests at a site during
    one visit sharing it), number of site visits, and service-affecting tests."""
    sites = []
    hours = 0.0
    for r in results:
        hours += r.minutes / 60.0
        for s in r.dispatch_sites:
            if s not in sites:
                sites.append(s)
                hours += travel_hours(case_seed, s)
    return {"hours": round(hours, 2), "site_visits": len(sites),
            "service_affecting": sum(1 for r in results if r.service_impact != "none" and r.status == "done")}
