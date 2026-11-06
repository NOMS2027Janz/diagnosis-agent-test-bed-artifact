# VA ROADM (CDC): Fault Management Guide

Publication VA-TN-0431, revision A

## Applicability

**VA-ROADM-CDC**: colourless, directionless, contentionless ROADM, one WSS module per degree.

## 1 Behaviour

- Switching is per channel at each egress degree. A line-wide impairment on an ingress degree therefore continues as a
  per-channel impairment on each channel routed onward.
- The WSS module of each degree performs channel power equalisation to the launch target, within an attenuation range of
  typically 8 dB. An upstream power loss within that range is levelled out at the node (the OSNR penalty it caused
  stays); a larger loss is passed on as low power.
- Everything else passes through.

## 2 Performance monitoring

Per-channel power and OSNR at each ingress degree, from the optical channel monitor (OCM licence).

## 3 Alarm list

None for the failure modes below. These faults are seen through OCM readings and through alarms on the downstream
transponders.

## 4 Failure modes

| Failure mode | Symptom | Class | Rate |
|---|---|---|---|
| WSS attenuation drift | Attenuation of one channel creeps above its setting; that channel is low in power after the node | Soft | 1 % per degree per year |
| WSS passband offset | Passband of one channel shifted or narrowed; power normal, signal quality down | Soft | 0.5 % per degree per year |
| WSS ingress port failure | One WSS input port fails; all channels arriving on that degree are blocked at the node; other degrees unaffected | Hard | 0.2 % per degree per year |

## 5 Fault localisation

- A single-channel power deviation on the OCM points to the node where the deviation first appears.
- A port failure is located at the ingress port of the affected degree; a single-channel fault at that channel on the
  ingress port of the degree where it first appears.
