# VC OFA Series Optical Fibre Amplifiers

Service Handbook SH-OFA, edition 4

## 1. The OFA family

Three variants of the OFA module are in service:

- **VC-OFA-T**, the launch OFA: it sits where a wavelength section leaves the wavelength router;
- **VC-OFA-M**, the mid-span OFA: it sits in repeater shelters between terminals;
- **VC-OFA-R**, the landing OFA: it sits where a wavelength section arrives at the next wavelength router.

## 2. How an OFA handles the light it receives

An OFA holds a fixed gain. At installation the gain is set to cancel the attenuation of the fibre length that feeds
the module, and the module keeps that gain from then on. Consequences for the field engineer:

- When the incoming light weakens, the outgoing light weakens by the same amount, and every wavelength carried gets
  noisier.
- When the incoming light disappears, the outgoing light disappears too.
- Anything else the module receives, it sends on as it is.
- The supervisory link is local to each fibre length. Every shelter ends the supervisory link it receives and starts
  a fresh one on the next fibre length, so a supervisory outage never spreads beyond the fibre length where it occurs.

Rated noise figure is 5.5 dB, 6.5 dB at worst. The module declares its input dark below -30 dBm per wavelength.

## 3. Telemetry

| Reading | What it reports | Telemetry pack |
|---|---|---|
| P-IN | Aggregate optical power entering the module | Optical Power pack |
| P-OUT | Aggregate optical power leaving the module | Optical Power pack |
| I-PUMP | Pump drive, as a share of the value at commissioning | Insight pack |
| SV-RX | Whether the supervisory link is being received; feeds alarms only | Base |

## 4. Alarm reference

**IN-DARK** (urgent), raised by a mid-span OFA when P-IN shows no light. Look first at the fibre length feeding the
module: it may be severed, or its attenuation may have jumped. Otherwise the OFA before that fibre length may be out of
service. Only the first module along the section to lose its input raises IN-DARK; the modules after it are muted by a
downstream defect flag sent on the supervisory link.

**SEC-IN-DARK** (urgent), raised by a landing OFA when P-IN shows no light. The same three origins as IN-DARK apply, and
one more: the patch lead at the head of the wavelength section may have been unplugged. Muted in the same way as
IN-DARK.

**SV-LOSS** (important), raised by mid-span and landing OFAs when SV-RX receives nothing. The fibre length feeding the
module is severed or its attenuation has jumped. SV-LOSS is logged against that fibre length rather than against the
module.

**OUT-DARK** (urgent), raised by any OFA when P-OUT shows no light although light is entering. The module itself is out
of service.

**GAIN-DEV** (advisory), raised by mid-span and landing OFAs when the gain strays more than 2 dB from its setting.
Origin, always in this module: the module is out of service, its gain has sagged, or its pump is wearing out.

**PUMP-EOL** (advisory), raised by any OFA when I-PUMP passes 120 per cent of its commissioning value. Origin: pump
wear-out in this module.

## 5. Fault catalogue

**Amplifier outage.** The pump or the whole module dies: nothing leaves the module and every wavelength it carried is
gone. An outage. Expect it in about 1 per cent of modules a year.

**Gain sag.** The gain falls short of its setting: every wavelength leaves the module weaker and noisier. A
degradation. About 1 per cent of modules a year.

**Excess noise.** The amplifying stage adds more noise than its rating. Output levels look normal, but every wavelength
downstream is noisier. A degradation. About 1 per cent of modules a year.

**Pump wear-out.** I-PUMP creeps up as the module works harder to hold its gain, and the trend ends in PUMP-EOL. Slow;
no service effect until late. About 2 per cent of modules a year.

**Spectral tilt.** The gain is no longer even across the band, so the wavelengths at one end of the band come out
weaker. A degradation. About 0.5 per cent of modules a year.
