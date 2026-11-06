# Knowledge sources (agent side)

These files are the documentation an operator would hold: equipment description packages as vendors
supply them, extracts of the standards the equipment implements, and the operator's own practice notes
for the fibre plant. The knowledge graph's lifecycle processes load them when the corresponding
lifecycle event occurs (an equipment type's package when the type is first onboarded; standards and
practice at the start of the history). No tool reads these files directly, with one deliberate exception: in the
tool arm of the procedures comparison, the procedure tool holds the documents in procedures/ as its own data,
as an engineer holds a binder; in the graph arm the lifecycle publishes them into the graph instead.

## Provenance rule

Everything here is written from the ITU-T Recommendations named in each file and from the form and
content of typical vendor alarm and troubleshooting documentation, vendor-neutral. Nothing is derived
from the world simulator's code or parameters ("Reference Network, Data Sets and Knowledge Graph" §2).
Fault-mode identifiers come from the fault-mode vocabulary of "Diagnosis Agent Framework Specification"
v0.1, Appendix A, which is part of the ontology; what each document says about a fault mode is its own.

## Grain and gaps, by design

Documentation is coarser than physics and incomplete, as real documentation is. Known differences
between what these documents say and how the simulated world behaves (harness-side record; tools never
read this file):

- Effects are qualitative (power low, OSNR low, signal lost) with typical ranges, not equations. Whether a
  degradation produces errors depends on margin, which documents cannot state.
- VB's amplifier documentation does not list noise-figure degradation as a fault mode; its alarm
  documentation attributes OSNR loss to "unit degradation, contact support".
- VB's line-interface documentation does not document laser-bias monitoring or laser ageing.
- Documented failure rates come from datasheets and plant practice, not from the world's base rates; they
  differ.
- Some documented fault modes never occur in the simulated world (gain tilt, WSS port failure), and the
  world contains effects documents do not describe precisely (re-levelling headroom, nonlinear noise).
- Alarm names are common to both vendors, following the G.798 defect names. Vendor-specific alarm naming
  is a possible future axis.
