# VA Coherent Transponder and Line Card Family: Fault Management Guide

Publication VA-TN-0418, revision D

## Applicability

- **VA-TPD-400**: 400G transponder.
- **VA-MXP-200**, **VA-MXP-400**: 200G and 400G muxponders.
- **VA-OTN-LC-200**, **VA-OTN-LC-400**: 200G and 400G OTN line cards.

All cards comply with ITU-T G.798 for OTN equipment functions.

## 1 Optical specifications

| Item | Value |
|---|---|
| 400G mode | DP-16QAM, 64 GBd, OTUC4 |
| 200G mode | DP-QPSK, 64 GBd, OTUC2 |
| Receiver LOS threshold | -25 dBm |
| FEC | Soft-decision; pre-FEC BER threshold approximately 2E-2 |
| DGD tolerance | 30 ps |
| SOP tracking speed | approximately 1 Mrad/s; faster SOP changes produce errors |

## 2 Performance monitoring

| Parameter | Description | Licence |
|---|---|---|
| OPR-15, ES-15 | Received optical power and errored seconds, 15-minute bins | Standard |
| OPR | Received optical power | Power monitoring licence |
| OSNR-EST, PREFEC-BER, DGD, SOP-RATE | DSP estimates of OSNR, pre-FEC BER, DGD and SOP rate of change, 1-second reporting | Advanced monitoring licence |
| LBC | Transmit laser bias current, % of beginning of life | Advanced monitoring licence |

## 3 Receiver response

| Condition at the receiver | Defect declared | Result |
|---|---|---|
| No light | dLOS | Loss of OTU frame |
| Card failure | dEQ | Loss of OTU frame; traffic alarms masked |
| Degraded light: low power, low OSNR, high DGD, passband penalty, receiver penalty, slow margin decline | (none until errors) | Pre-FEC errors once the degradation is larger than the channel margin; loss of frame if much larger |
| Fast SOP change beyond tracking speed | (none) | Errored seconds |

On loss of OTU frame, G.798 consequent actions apply: ODU-AIS is sent downstream on every ODU in the OTU, and OTU-BDI
is returned to the far-end transmitter.

## 4 Alarm list

| Alarm | Severity | Raised when | Probable cause and location |
|---|---|---|---|
| OCH_LOS | Critical | No light at the receiver | Far-end transmitter: laser failed or card failed. Anywhere upstream on the channel's path: fibre cut, EDFA dead, jumper pulled, ROADM ingress port blocked |
| OTU_LOF | Critical | OTU frame lost, light present | Upstream on the path: high span loss, EDFA noise figure degradation, DGD increase, WSS passband offset, fast SOP event. This card: receiver degradation |
| OTU_SD | Minor | Errors above the degrade threshold (margin under about 1 dB) | Upstream on the path: high span loss, EDFA noise figure degradation, contaminated connector, WSS passband offset, DGD increase. This card: receiver degradation |
| OTU_BDI | Not alarmed | Far end reports a defect on this card's transmitted signal | Any failure on the path to the far end |
| EQPT_TX_FAIL | Critical | Transmit laser fault detected | This card: transmit laser failure |
| EQPT_CARD_FAIL | Critical | Card hardware fault | This card: card failure |
| LASER_BIAS_HIGH | Minor | LBC above 120 % of beginning of life (internal supervision) | This card: laser ageing |

## 5 Failure modes

| Failure mode | Symptom | Class | Rate |
|---|---|---|---|
| Transmit laser failure | Laser off or failed; loss of signal at the far-end receiver of the channel | Hard | 0.5 % per card per year |
| Card failure | Far-end loss of signal on the transmitted channel; received traffic lost locally, monitoring silent, traffic alarms masked by the equipment alarm | Hard | 0.5 % per card per year |
| Receiver degradation | Errors at this receiver only; other channels and the optical path normal | Soft | 1 % per card per year |
| Laser ageing | LBC rising; precedes transmit laser failure | Incipient | 1 % per card per year |
| Margin erosion | Slow decline of the channel's margin, usually from ageing of components along the path; seen at the receiver | Incipient | 1 % per channel per year |
