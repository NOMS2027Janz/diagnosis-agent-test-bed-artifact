# VA EDFA Family: Fault Management Guide

Publication VA-TN-0412, revision C

## Applicability

This guide applies to the following erbium-doped fibre amplifier (EDFA) cards:

- **VA-OA-BST**, transmit-side EDFA (booster), fitted at the head end of each optical multiplex section (OMS);
- **VA-OA-ILA**, optical line amplifier (OLA), fitted at in-line amplifier huts;
- **VA-OA-PRE**, receive-side EDFA (pre-amp), fitted at the tail end of each OMS.

## 1 Amplifier behaviour

The EDFA cards run in automatic gain control (AGC). The gain target is provisioned at turn-up equal to the attenuation
of the fibre span ahead of the card. Under AGC, a drop of input level gives an equal drop of output level, and the
optical signal-to-noise ratio (OSNR) of all channels degrades with it.

Noise figure: 5.5 dB nominal, 6.5 dB worst case. Input loss-of-light threshold: -30 dBm per channel.

Every hut terminates the optical supervisory channel (OSC) and launches its own OSC onto the next span, so an OSC
failure is confined to the span on which it occurs. A card with no input light has no output light. Other conditions
at the input are passed through to the output.

## 2 Performance monitoring

| Parameter | Description | Licence |
|---|---|---|
| IN-PWR | Composite input power | Power monitoring licence |
| OUT-PWR | Composite output power | Power monitoring licence |
| PUMP-I | Pump laser drive current, % of beginning of life | Advanced monitoring licence |
| OSC-RX | OSC receiver status (alarm generation only, no PM) | Standard |

## 3 Alarm list

| Alarm | Severity | Raised by | Raised when | Probable cause and location |
|---|---|---|---|---|
| AMP_INPUT_LOS | Critical | OLA | Loss of light at IN-PWR | Fibre break or high span loss on the incoming span; previous EDFA dead |
| OMS_LOS_P | Critical | Pre-amp | Loss of light at IN-PWR | Fibre break or high span loss on the incoming span; previous EDFA dead; OMS input jumper pulled |
| OSC_LOS | Major | OLA, pre-amp | OSC not received | Fibre break or high span loss on the incoming span (alarm is reported against the incoming span) |
| AMP_FAIL | Critical | All | No light at OUT-PWR with input light present | EDFA dead (this card) |
| AMP_GAIN_OOR | Minor | OLA, pre-amp | Gain more than 2 dB from target | This card: EDFA dead, gain loss, or pump ageing |
| AMP_PUMP_EOL | Minor | All | PUMP-I above 120 % of beginning of life | This card: pump ageing |

Alarm suppression: AMP_INPUT_LOS and OMS_LOS_P are raised by the first card on the OMS to lose input light only.
Downstream cards are held off by the forward defect indication on the OSC.

## 4 Failure modes

| Failure mode | Symptom | Class | Rate (per card per year) |
|---|---|---|---|
| EDFA dead | Pump laser or card failure; no output light, all channels lost | Hard | 1 % |
| Gain loss | Gain under target; low output on all channels, OSNR down on all channels | Soft | 1 % |
| Noise figure degradation | Amplifier stage noisier than specified; output power normal, OSNR down on every channel downstream | Soft | 1 % |
| Pump ageing | PUMP-I climbs to maintain gain; ends in the end-of-life alarm | Incipient | 2 % |
| Gain tilt | Gain not flat across the C-band; channels at one band edge low | Soft | 0.5 % |
