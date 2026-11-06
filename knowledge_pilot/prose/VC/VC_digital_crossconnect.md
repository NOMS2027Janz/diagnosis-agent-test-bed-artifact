# VC Digital Cross-Connect and Tributaries

Service Handbook SH-DXC, edition 1

## 1. Equipment

- **VC-DXC**, the digital cross-connect: it switches ODUs between line modules and tributaries.
- **VC-TRIB-10**, **VC-TRIB-100** and **VC-TRIB-400**: tributary ports for 10GE, 100GE and 400GE customers, carried in
  ODU2, ODU4 and ODUflex respectively.

## 2. Behaviour

- The cross-connect passes an ODU AIS along on every ODU it switches.
- A tributary that receives an ODU AIS, or an ODU meant for someone else, drops the customer's service. If the service
  is dual-homed, with a primary and a standby ODU route and the selector at the receiving tributary, and the standby
  route is clean, the customer only sees a short interruption while the selector moves.

## 3. Telemetry

Every tributary keeps 15-minute counts of customer errored seconds and customer unavailable seconds (Base).

## 4. Alarm reference

**TRIB-FAIL** (urgent): the customer service on this tributary has failed. Where to look: any fault on the ODU route in
use; the tributary at either end; or, at a cross-connect along the ODU route, a mis-provisioned connection or a fabric
outage.

**AIS-IN** (information only): an ODU AIS is arriving.

**REMOTE-AIS** (information only): the other end says it is receiving ODU AIS.

**TRACE-MISMATCH** (important): the ODU arriving is not the expected one. Where to look: a mis-provisioned connection
at a cross-connect along the ODU route.

**ON-STANDBY** (advisory): the selector has moved to the standby route. Where to look: any fault on the primary route.

**STANDBY-LOST** (advisory): the standby route has failed. Where to look: any fault on the standby route.

**TRIB-HW** (urgent): tributary hardware fault, in this tributary.

**FABRIC-HW** (urgent): fabric hardware fault, in this cross-connect.

## 5. Fault catalogue

**Fabric outage.** The switching fabric dies, and every ODU switched through this cross-connect is cut. An outage.
About 0.2 per cent of cross-connects a year.

**Mis-provisioned connection.** A connection is set up wrongly, and one ODU ends up at the wrong destination. Nothing
shows on the optical side. An outage; it happens mostly straight after provisioning work. About 1 per cent of
cross-connects a year.

**Tributary failure.** The customer-facing optic or the tributary itself fails, and the customer service drops. An
outage. About 1 per cent of tributaries a year.
