"""OTN service layer of the world simulator: ODU path state, SNCP selection and ODU-layer
defects, in the manner of ITU-T G.709 and G.798 at a vendor-neutral level of detail.

A client service is carried on an ODU path that crosses one or more lightpaths, cross-connected
at the OTN switches of intermediate hub sites. Each direction of a service is evaluated
separately. A hop is good when its lightpath delivers a signal below the FEC limit to an
operational line port; the path is good when every hop is good and every intermediate switch
and cross-connect is sound. SNCP selects, at the tail end, the working path if it is good, else
the protection path if that is good.

Client ports are named cp.<service>.<site>. Defects raised here:

- CLIENT_SF (critical) at the egress client port when the service is down in that direction;
- ODU_AIS (not alarmed) at the egress client port when the active path carries AIS, and ODU_BDI
  (not alarmed) at the ingress client port;
- PROT_ACTIVE (minor) at the tail-end port while traffic runs on the protection path, and
  PROT_UNAVAIL (minor) while the protection path is down and the working path is carrying;
- ODU_TIM (major) at the egress port when a cross-connect delivers the wrong ODU;
- OTN_FABRIC_FAIL (critical) at a failed switch; EQPT_PORT_FAIL (critical) at a failed client port.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .network import Network, ODUPath
from .physics import FEC_BER, OCH_LOS_DBM, Effects, Snapshot


def client_port(svc_id: str, site: str) -> str:
    return f"cp.{svc_id}.{site}"


@dataclass
class DirState:
    up: bool
    active: Optional[str]          # "w" | "p" | None
    w_ok: bool
    p_ok: Optional[bool]           # None when unprotected
    tim: bool                      # trail trace mismatch on the active path
    errored: bool                  # active path has a hop above the FEC limit (but not lost)


def _hop_dir(net: Network, lp_id: str, a: str) -> str:
    return "fwd" if net.lightpaths[lp_id].nodes[0] == a else "rev"


def path_ok(net: Network, snap: Snapshot, fx: Effects, path: ODUPath, reverse: bool) -> tuple[bool, bool, bool]:
    """(good, trail-trace mismatch, errored) for one direction of an ODU path."""
    hops = path.hops if not reverse else [type(h)(h.lightpath, h.z, h.a) for h in reversed(path.hops)]
    errored = False
    for i, h in enumerate(hops):
        r = snap.rx[(h.lightpath, _hop_dir(net, h.lightpath, h.a))]
        if r.rx_power_dbm < OCH_LOS_DBM or r.pre_fec_ber >= FEC_BER:
            return False, False, False
        if i > 0 and f"otn.{h.a}" in fx.otn_switch_failed:
            return False, False, False
    tim = any((f"otn.{h.a}", path.id) in fx.xc_fault for h in hops[1:])
    if tim:
        return False, True, False
    return True, False, errored


def service_states(net: Network, snap: Snapshot, fx: Effects) -> dict[tuple[str, str], DirState]:
    """State of every service in each direction, keyed (service id, egress site)."""
    out = {}
    for sid, svc in net.services.items():
        if svc.working is None:
            continue
        for reverse, egress, ingress in ((False, svc.dst, svc.src), (True, svc.src, svc.dst)):
            ports_ok = (client_port(sid, ingress) not in fx.client_port_failed
                        and client_port(sid, egress) not in fx.client_port_failed)
            w_ok, w_tim, _ = path_ok(net, snap, fx, net.odu_paths[svc.working], reverse)
            p_ok, p_tim = None, False
            if svc.protect:
                p_ok, p_tim, _ = path_ok(net, snap, fx, net.odu_paths[svc.protect], reverse)
            if w_ok:
                active = "w"
            elif p_ok:
                active = "p"
            else:
                active = None
            tim = (w_tim if active in ("w", None) else p_tim)
            out[(sid, egress)] = DirState(up=ports_ok and active is not None, active=active, w_ok=w_ok,
                                          p_ok=p_ok, tim=tim, errored=False)
    return out


def otn_defects(net: Network, states: dict, fx: Effects):
    from .alarms import Defect
    out = set()
    for (sid, egress), st in states.items():
        svc = net.services[sid]
        ingress = svc.src if egress == svc.dst else svc.dst
        ep, ip = client_port(sid, egress), client_port(sid, ingress)
        if not st.up:
            out.add(Defect("CLIENT_SF", ep))
            if st.tim:
                out.add(Defect("ODU_TIM", ep))
            elif st.active is None:
                out.add(Defect("ODU_AIS", ep))
                out.add(Defect("ODU_BDI", ip))
        if svc.protect:
            if st.active == "p":
                out.add(Defect("PROT_ACTIVE", ep))
            elif st.active == "w" and st.p_ok is False:
                out.add(Defect("PROT_UNAVAIL", ep))
    for sw in fx.otn_switch_failed:
        out.add(Defect("OTN_FABRIC_FAIL", sw))
    for cp in fx.client_port_failed:
        out.add(Defect("EQPT_PORT_FAIL", cp))
    return out
