# VB Optical Amplifier Family
## Alarm and Troubleshooting Reference

Document VB-OA-ATR, issue 3

### 1. Scope

This reference covers the three units of the VB optical amplifier family:

| Product code | Unit | Position in the line |
|---|---|---|
| VB-OA-BST | Booster amplifier (BA) | At the transmitting end of a multiplex section, after the ROADM |
| VB-OA-ILA | Line amplifier (LA) | At intermediate amplifier sites |
| VB-OA-PRE | Preamplifier (PA) | At the receiving end of a multiplex section, before the ROADM |

It describes what each unit monitors, the alarms it raises, the faults that can affect it, and how to
troubleshoot them. It is intended for operations and field staff.

### 2. Operating principles

All three units operate in constant-gain mode. The gain is set at commissioning to match the loss of the fibre
section that precedes the unit, and the unit holds that gain thereafter. A consequence that field staff should keep in
mind: if less light arrives at the input, less light leaves the output, and because the amplifier adds the same noise
regardless, the optical signal-to-noise ratio of every channel falls as well.

Typical performance:
- noise figure 5.5 dB typical, 6.5 dB maximum;
- input loss-of-signal threshold -30 dBm per channel.

The optical supervisory channel (OSC) is terminated at every amplifier site and a fresh OSC is launched on the next
fibre section. Loss of the OSC on one section therefore does not propagate to the next.

Apart from the OSC, the unit passes on what it receives: if no light arrives, no light leaves; if the input is weak,
the output is weak and noisier, as described above.

### 3. Monitoring

Each unit reports the following to the management system:

| Monitor | What it measures | Availability |
|---|---|---|
| Input power | Total optical power at the unit's input | Units with the power monitoring option |
| Output power | Total optical power at the unit's output | Units with the power monitoring option |
| Pump drive current | Drive current of the pump laser, as a percentage of its beginning-of-life value; an indicator of pump health | Units with the advanced monitoring option |
| OSC receiver | Presence of the supervisory channel at the input; used only to raise alarms and not reported as a value | All units |

### 4. Alarms

**AMP_INPUT_LOS** (critical). Raised by a line amplifier when no light arrives at its input. Only the first unit
along the multiplex section that loses its input raises this alarm; units further downstream suppress it on receiving
the forward defect indication carried by the OSC.
*Probable causes:* the fibre section feeding this unit is broken, or its loss has risen sharply; or the preceding
amplifier has failed and emits no light.

**OMS_LOS_P** (critical). The preamplifier's equivalent of AMP_INPUT_LOS, raised when no light arrives at the input
of the receiving end of the multiplex section. Suppressed in the same way.
*Probable causes:* as for AMP_INPUT_LOS; in addition, the patch cord at the input of the multiplex section may have
been disconnected.

**OSC_LOS** (major). Raised by a line amplifier or preamplifier when the supervisory channel is not received. The
alarm is reported against the fibre section feeding the unit, not against the unit itself.
*Probable causes:* the fibre section feeding this unit is broken, or its loss has risen sharply.

**AMP_FAIL** (critical). Raised by any unit when light is present at its input but none leaves its output.
*Probable cause:* failure of this unit's pump laser or of the unit itself.

**AMP_GAIN_OOR** (minor). Raised by a line amplifier or preamplifier when the gain departs from its set point by more
than 2 dB.
*Probable cause:* gain loss in this unit.

**AMP_PUMP_EOL** (minor). Raised by any unit when the pump drive current exceeds 120 per cent of its
beginning-of-life value.
*Probable cause:* pump ageing in this unit.

### 5. Faults

**Pump or unit failure.** The pump laser or the unit fails and the output goes dark. Every channel carried by the
unit is lost downstream. This is a hard failure. Field return rate about 1 per cent of units per year.

**Gain loss.** The unit's gain falls below its set point. Output power is low on every channel, and, since the noise
added is unchanged, the signal-to-noise ratio of every channel falls too. This is a gradual or step degradation rather
than an outage. About 1 per cent of units per year.

**Pump ageing.** As the pump laser ages, the unit raises its drive current to hold the gain. Traffic is unaffected
until the drive current reaches its limit; the trend gives early warning and leads eventually to the end-of-life alarm
(AMP_PUMP_EOL). This is a slow, incipient condition. About 2 per cent of units per year.

**Gain tilt.** The gain is no longer flat across the band, so channels at one edge of the band arrive weaker than the
others. Some channels are affected, not all. This is a degradation rather than an outage. About 0.5 per cent of units
per year.

### 6. Troubleshooting notes

- If the signal-to-noise ratio of the channels has fallen but the output power is normal, the unit may be degraded.
  Contact VB support.
