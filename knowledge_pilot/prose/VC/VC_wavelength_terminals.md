# VC Wavelength Terminals

Service Handbook SH-WT, edition 2

## 1. Models covered

| Model | What it is |
|---|---|
| VC-WT-400T | single-client wavelength terminal, 400G |
| VC-WT-200A | aggregating wavelength terminal, 200G (several clients on one wavelength) |
| VC-WT-400A | aggregating wavelength terminal, 400G |
| VC-WT-200D | digital-switching line module, 200G |
| VC-WT-400D | digital-switching line module, 400G |

400G models use DP-16QAM at 64 GBd; 200G models use DP-QPSK at 64 GBd. The receiver declares darkness below -25 dBm.
Soft-decision FEC corrects up to a pre-FEC error ratio of about 2e-2. DGD tolerance is 30 ps. The polarisation tracker
follows changes up to about 1 Mrad/s.

## 2. How a terminal reacts to what reaches it

- **Darkness at the receiver**: the terminal declares a loss-of-signal defect (dLOS in G.798 terms) and can no longer
  find its OTU frame.
- **Module failure**: the terminal declares an equipment defect (dEQ) and the OTU frame is lost.
- **Light that arrives in poor shape** (weak, noisy, with too much differential group delay, clipped by a filter,
  spoiled by the receiver itself, or with its margin slowly worn away): pre-FEC errors begin once the damage exceeds the
  wavelength's margin, and the frame is lost once it exceeds the margin by far.
- **Polarisation swings faster than the tracker can follow**: errored seconds.
- **Once the OTU frame is gone**: the terminal follows G.798. It inserts ODU AIS towards every ODU it carries
  downstream and returns a backward defect indication to the terminal at the other end.

## 3. Telemetry

- Received light level and errored seconds, in 15-minute counters: Base.
- Received light level, live: Optical Power pack.
- From the coherent receiver, once a second: estimated OSNR, pre-FEC error ratio, DGD, polarisation drift rate (Insight
  pack).
- Transmit laser bias, as a share of its value at commissioning: Insight pack.

## 4. Alarm reference

| Alarm | Level | Raised when | Where to look |
|---|---|---|---|
| RX-DARK | Urgent | The receiver sees no light | The transmitter at the other end has gone dark or its module has failed; or, anywhere earlier on the wavelength's route, a severed fibre, an amplifier out of service, an unplugged patch lead or a router input that has failed |
| FRAME-LOSS | Urgent | The OTU frame is lost though light arrives | Earlier on the route: fibre attenuation that has jumped, excess amplifier noise, DGD growth, a misaligned router passband or a polarisation swing; or this terminal's own receiver impairment |
| DEGRADE | Advisory | Pre-FEC errors exceed the degrade level (margin below about 1 dB) | Earlier on the route: fibre attenuation that has jumped, excess amplifier noise, a soiled connector end-face, a misaligned router passband or DGD growth; or this terminal's own receiver impairment |
| REMOTE-DEFECT | Information only | The other end reports a defect in what this terminal sends | Any fault between here and the other end, the other end included |
| TX-HW | Urgent | The terminal detects a transmit laser fault | This terminal's transmitter |
| MOD-HW | Urgent | Module hardware fault | This module |
| TX-BIAS | Advisory | Laser bias above 120 per cent of its commissioning value | Transmitter wear-out in this terminal |

## 5. Fault catalogue

**Transmitter dark.** The transmit laser stops or fails, and the terminal at the other end of the wavelength sees no
light. An outage. About 0.5 per cent of terminals a year.

**Module failure.** The module dies. The terminal at the other end loses the wavelength this module sends; on this
side, what the module receives is lost, its telemetry stops, and its traffic alarms are hidden behind the hardware
alarm. An outage. About 0.5 per cent a year.

**Receiver impairment.** The receiver itself has deteriorated: errors appear at this receiver alone while the other
wavelengths and the route stay healthy. A degradation. About 1 per cent a year.

**Transmitter wear-out.** The laser bias climbs as the laser ages, ahead of an eventual dark transmitter. Slow. About 1
per cent a year.

**Margin creep.** The wavelength's margin shrinks slowly, as parts along its route age; it is seen at the receiver.
Slow. About 1 per cent of wavelengths a year.
