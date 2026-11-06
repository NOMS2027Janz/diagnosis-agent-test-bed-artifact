# VC Wavelength Router

Service Handbook SH-WR, edition 3

## 1. Equipment

The VC-WR-X is a fully flexible wavelength router (colourless, directionless and contentionless). Each direction of
the router has its own selector module.

## 2. Behaviour

The router steers every wavelength on its own as it leaves a direction. A problem that hits all the light entering on
one direction therefore reappears, after the router, as a problem on each wavelength steered onwards from it.

Each selector module brings the wavelength levels back to the launch level, as long as its attenuation range allows,
normally 8 dB. A drop in level of up to about 8 dB before the router is evened out at the router; the noise penalty the
drop caused does not go away. A bigger drop leaves the router as a weak wavelength.

Everything else crosses the router as it arrived.

## 3. Telemetry

With the Spectrum pack, a spectrum monitor on every incoming direction reports, per wavelength, the level and the
OSNR.

## 4. Alarms

The router has no alarms for the faults below. They are seen in the spectrum monitor's readings and in the alarms of
the terminals further along.

## 5. Fault catalogue

**Over-attenuation.** The selector module attenuates one wavelength more than it should, so that wavelength leaves the
router weak. A degradation. About 1 per cent of directions a year.

**Passband misalignment.** The selector module's passband for one wavelength has moved off centre or become too
narrow. The wavelength's level is fine but its quality suffers. A degradation. About 0.5 per cent of directions a year.

**Direction input fault.** An input of a selector module dies. All the light coming in on that direction stops at the
router, while light entering on the other directions carries on. An outage. About 0.2 per cent of directions a year.

## 6. Locating faults

- A single wavelength whose level jumps in the spectrum monitor readings is at fault at the first router where the jump
  is seen.
- An input fault is at the input of the direction it hits; a single-wavelength fault is on that wavelength at that
  input.
