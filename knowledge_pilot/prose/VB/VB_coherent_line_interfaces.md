# VB Coherent Line Interfaces
## Alarm and Troubleshooting Reference

Document VB-LI-ATR, issue 5

### 1. Scope

This reference covers VB's coherent line interfaces:

| Product code | Unit | Line rate |
|---|---|---|
| VB-TPD-400 | Transponder | 400G |
| VB-MXP-200 | Muxponder | 200G |
| VB-MXP-400 | Muxponder | 400G |
| VB-OTN-LC-200 | OTN line card | 200G |
| VB-OTN-LC-400 | OTN line card | 400G |

All units implement the OTN equipment functions of ITU-T G.798.

### 2. Characteristics

- 400G: DP-16QAM at 64 GBd, OTUC4 framing. 200G: DP-QPSK at 64 GBd, OTUC2 framing.
- Receiver loss-of-signal threshold: -25 dBm.
- Soft-decision FEC; the pre-FEC bit error ratio limit is about 2e-2.
- Differential group delay tolerance: 30 ps.
- Polarisation tracking: about 1 Mrad/s. Faster changes of the state of polarisation cause errors.

### 3. Monitoring

| Monitor | What it reports | Availability |
|---|---|---|
| Receive performance counters | Received power and errored seconds, in 15-minute bins | All units |
| Received power | Optical power at the line receiver | Units with the power monitoring option |
| Coherent DSP monitors | Estimated OSNR, pre-FEC bit error ratio, differential group delay and polarisation change rate, reported every second | Units with the advanced monitoring option |

### 4. How the receiver responds to line impairments

- If no light reaches the receiver, the unit declares loss of signal (G.798 dLOS) and loses OTU frame alignment.
- If the unit itself has failed, it declares an equipment defect (dEQ); frame alignment is lost and the unit's
  traffic alarms are masked by the equipment alarm.
- If the signal arrives but is impaired (lower power, a lower signal-to-noise ratio, a raised differential group
  delay, a filtering penalty from a misaligned or narrowed passband, a receiver-side penalty, or a slow loss of margin),
  pre-FEC errors appear once the impairment exceeds the channel's margin, and frame alignment is lost once it exceeds
  the margin by a wide amount.
- Polarisation changes faster than the tracking range produce errored seconds.
- When frame alignment is lost, the unit applies the G.798 consequent actions: an ODU alarm indication signal is
  inserted downstream on every ODU the OTU carries, and a backward defect indication is sent to the far-end
  transmitter.

### 5. Alarms

**OCH_LOS** (critical). No light at the line receiver.
*Probable causes:* the far-end transmitter's laser has failed, or the far-end card has failed; or something along the
channel's path has cut the light: a broken fibre, a failed amplifier, a disconnected patch cord, or a blocked ROADM
port.

**OTU_LOF** (critical). OTU frame alignment lost while light is present.
*Probable causes:* along the channel's path, a rise in fibre loss, a rise in an amplifier's noise, a rise in
differential group delay, a misaligned ROADM passband, or a fast polarisation transient; or degradation of this unit's
own receiver.

**OTU_SD** (minor). Signal degraded: pre-FEC errors above threshold, raised when the margin falls below about 1 dB.
*Probable causes:* along the channel's path, a rise in fibre loss, a rise in an amplifier's noise, a dirty connector, a
misaligned ROADM passband or a rise in differential group delay; or degradation of this unit's own receiver.

**OTU_BDI** (not alarmed). The far end reports a defect on the signal this unit transmits.
*Probable cause:* any failure on the path to the far end.

**EQPT_TX_FAIL** (critical). The unit has detected a fault in its transmit laser.
*Probable cause:* transmit laser failure on this unit.

**EQPT_CARD_FAIL** (critical). Card hardware fault.
*Probable cause:* failure of this card.

### 6. Faults

**Transmit laser failure.** The transmit laser is off or has failed. The receiver at the far end of this channel loses
the signal. Hard failure; about 0.5 per cent of units per year.

**Card failure.** The card fails. The channel it transmits is lost at the far end; the traffic it receives is lost
here, its monitoring goes silent, and its traffic alarms are masked by the equipment alarm. Hard failure; about 0.5 per
cent of units per year.

**Receiver degradation.** The receiver degrades. Errors appear at this receiver only; other channels and the optical
path are normal. A degradation, not an outage; about 1 per cent of units per year.

### 7. Troubleshooting notes

- Errors on one channel only, with the optical path and the neighbouring channels normal, point to the receiver.
- Errors on every channel of a line point to the line, not to the interfaces.
