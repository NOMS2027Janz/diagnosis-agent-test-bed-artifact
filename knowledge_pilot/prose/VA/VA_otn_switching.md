# VA OTN Switching and Client Interfaces: Fault Management Guide

Publication VA-TN-0425, revision B

## Applicability

- **VA-OTNSW**: OTN cross-connect switch.
- **VA-CP-10GE** (ODU2), **VA-CP-100GE** (ODU4), **VA-CP-400GE** (ODUflex): client interface ports.

G.798 compliant. Client services may be protected with SNCP: a working and a protection ODU path, with the selector at
the receiving client port.

## 1 Performance monitoring

Client ports report client ES and client UAS in 15-minute bins (standard).

## 2 Behaviour

- The switch forwards ODU-AIS on each ODU it cross-connects.
- At a client port, ODU-AIS or a misdirected ODU brings the client service down. Where SNCP is configured and the
  selector has a defect-free path to choose, the service takes a hit only.

## 3 Alarm list

| Alarm | Raised by | Severity | Raised when | Probable cause and location |
|---|---|---|---|---|
| CLIENT_SF | Client port | Critical | Client service failed | Any failure on the active ODU path; client port failure at either end; misconnection or fabric failure at a switch on the ODU path |
| ODU_AIS | Client port | Not alarmed | ODU-AIS received | |
| ODU_BDI | Client port | Not alarmed | Far end reports AIS | |
| ODU_TIM | Client port | Major | Trace identifier mismatch | Misconnection at a switch on the ODU path |
| PROT_ACTIVE | Client port | Minor | Selector on the protection path | Any failure on the working ODU path |
| PROT_UNAVAIL | Client port | Minor | Protection path failed | Any failure on the protection ODU path |
| EQPT_PORT_FAIL | Client port | Critical | Port hardware fault | This port: port failure |
| OTN_FABRIC_FAIL | Switch | Critical | Fabric fault | This switch: fabric failure |

## 4 Failure modes

| Failure mode | Applies to | Symptom | Class | Rate |
|---|---|---|---|---|
| Fabric failure | Switch | All ODUs through the switch interrupted; ODU-AIS forwarded on each | Hard | 0.2 % per switch per year |
| Misconnection | Switch | One ODU cross-connected to the wrong destination; no optical symptom | Hard | 1 % per switch per year, mostly following provisioning activity |
| Port failure | Client port | Client optic or port failed; client service down | Hard | 1 % per port per year |
