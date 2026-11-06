# VB OTN Switch, Client Ports and Protection
## Alarm and Troubleshooting Reference

Document VB-OTN-ATR, issue 4

### 1. Scope

| Product code | Unit | Notes |
|---|---|---|
| VB-OTNSW | OTN switch | ODU cross-connect fabric |
| VB-CP-10GE | Client port, 10GE | Mapped into ODU2 |
| VB-CP-100GE | Client port, 100GE | Mapped into ODU4 |
| VB-CP-400GE | Client port, 400GE | Mapped into ODUflex |

All units implement the OTN equipment functions of ITU-T G.798. Client services may be protected by subnetwork
connection protection (SNCP), with a working and a protection ODU path and a selector at the receiving client port.

### 2. Monitoring

Each client port reports client errored seconds and client unavailable seconds in 15-minute bins.

### 3. Signal behaviour

- The OTN switch carries an ODU alarm indication signal onwards on every ODU cross-connected through it.
- At a client port, an ODU alarm indication signal, or an ODU delivered from the wrong source, takes the client
  service down. If the service is protected and the selector can choose a path free of defects, the service suffers
  only a brief hit while the selector switches.

### 4. Alarms

**CLIENT_SF** (critical, client port). The client service has failed.
*Probable causes:* any failure on the ODU path currently in use; a failure of the client port at either end; a wrong
cross-connection, or a fabric failure, in an OTN switch along the ODU path.

**ODU_AIS** (not alarmed, client port). An ODU alarm indication signal is received.

**ODU_BDI** (not alarmed, client port). The far end reports that it is receiving an alarm indication signal.

**ODU_TIM** (major, client port). Trail trace identifier mismatch: the ODU received is not the one expected.
*Probable cause:* a wrong cross-connection in an OTN switch along the ODU path.

**PROT_ACTIVE** (minor, client port). The selector is on the protection path.
*Probable cause:* any failure on the working ODU path.

**PROT_UNAVAIL** (minor, client port). The protection path has failed.
*Probable cause:* any failure on the protection ODU path.

**EQPT_PORT_FAIL** (critical, client port). Port hardware fault.
*Probable cause:* failure of this port.

**OTN_FABRIC_FAIL** (critical, OTN switch). Fabric fault.
*Probable cause:* failure of this switch's fabric.

### 5. Faults

**Switch fabric failure** (OTN switch). Every ODU cross-connected through the switch is interrupted: an alarm
indication signal is carried onwards on each. Hard failure; about 0.2 per cent of switches per year.

**Wrong cross-connection** (OTN switch). A cross-connection has been provisioned wrongly, so one ODU is delivered to
the wrong destination. There is no optical symptom. About 1 per cent of switches per year, mostly just after
provisioning changes.

**Client port failure** (client port). The client optic or the port fails, and the client service is lost. Hard
failure; about 1 per cent of ports per year.
