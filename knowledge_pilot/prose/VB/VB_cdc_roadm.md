# VB CDC ROADM
## Alarm and Troubleshooting Reference

Document VB-ROADM-ATR, issue 2

### 1. Scope

This reference covers the VB-ROADM-CDC, a colourless, directionless and contentionless ROADM built from one
wavelength-selective switch (WSS) per degree.

### 2. Operating principles

- Each channel leaving a degree is switched separately. An impairment affecting the whole incoming line therefore
  continues, after the node, as an impairment of each channel routed onwards.
- Each degree's WSS equalises the channel powers to the launch target within its attenuation range, typically 8 dB.
  A power loss upstream of up to about 8 dB is re-levelled at the node and does not appear downstream as low power,
  although the loss of signal-to-noise ratio it caused remains. A larger loss passes on as low power.
- Other impairments pass through the node unchanged.

### 3. Monitoring

An optical channel monitor on each ingress degree reports the power and OSNR of every channel. It is available on
nodes with the channel monitoring option.

### 4. Alarms

The ROADM raises no alarms of its own for the faults below. They show in the channel monitor's readings and in the
alarms of the line interfaces downstream.

### 5. Faults

**WSS attenuation drift.** The WSS attenuation for one channel drifts above its set value, and that channel's power is
low after the node. A degradation; about 1 per cent of degrees per year.

**WSS passband shift.** The WSS passband for one channel is shifted or narrowed. The channel's power is normal, but its
signal quality is reduced. A degradation; about 0.5 per cent of degrees per year.

**WSS input port failure.** An input port of a WSS fails. Every channel arriving at the node on that degree is blocked
there; channels arriving on the other degrees are unaffected. Hard failure; about 0.2 per cent of degrees per year.

### 6. Troubleshooting notes

- A deviation in a channel's power at the channel monitors locates a single-channel fault at the node where the
  deviation first appears.
- A port fault lies at the input port of the degree it affects. A single-channel fault lies on that channel at the
  input port of the degree where it first appears.
