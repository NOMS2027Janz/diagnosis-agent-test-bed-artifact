"""The operator's side of the reconciliation: the shared vocabulary as a semantic model.

Every concept is typed by an RFC 9940 class where one applies ("Some Key Terms for Network Fault and Problem
Management"), which serves as the common upper layer: a vendor concept is reconciled only with operator concepts of
the same class. Glosses come from the operator's own documents: the shared vocabulary (knowledge/vocabulary.yaml),
the framework's fault-mode catalogue ("Diagnosis Agent Framework Specification" v0.1, Appendix A) and the operator's
practice notes. No vendor package is read here.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# RFC 9940 classes used as the upper layer (definitions abridged from RFC 9940, Section 3)
RFC9940 = {
    "Cause": "The Events (Detected or otherwise) that gave rise to a Fault/Problem.",
    "Condition": "An interpretation of the Values of a set of one or more Characteristics of a Resource.",
    "Characteristic": "An observable or measurable aspect or behavior associated with a Resource.",
    "Resource": "An element of a network system.",
    "Alarm": "An undesirable State in a Resource that requires corrective action (as in RFC 8632).",
}
# the pilot's further classes, for terms RFC 9940 does not type
LOCAL = {
    "Scope": "How much of a Resource's traffic a Condition affects.",
    "Location": "Where a Cause lies relative to the Resource that raised an Alarm or saw a Change.",
    "Availability": "The monitoring option or tier under which a Characteristic is reported.",
    "Nature": "The temporal character of a Cause: abrupt outage, degradation, slow incipient trend, brief event.",
    "Severity": "The severity an Alarm is raised with.",
    "Behaviour": "How a Resource passes on or transforms a Condition arriving at it.",
}

# The framework's fault modes (Appendix A, version 0, and the modes added with the OTN layer and monitoring),
# glossed from the catalogue's applies-to, nature and free parameters.
FAULT_MODES = {
    "FIBRE_CUT": "A fibre span is cut: no light passes it. Hard; parameter: position (km).",
    "SPAN_LOSS_INCREASE": "A fibre span's loss rises (a bend, a splice, a stressed cable). Soft; parameters: added loss (dB), position.",
    "CONNECTOR_PULLED": "A connector or patch cord is disconnected at a port or patch panel. Hard.",
    "CONNECTOR_DIRTY": "A connector at a port or patch panel is contaminated and adds loss. Soft; parameter: added loss (dB).",
    "AMP_PUMP_FAIL": "An amplifier's pump laser, or the amplifier, fails: no output. Hard.",
    "AMP_GAIN_DROP": "An amplifier's gain falls below its set point. Soft; parameter: gain drop (dB).",
    "AMP_NF_RISE": "An amplifier's noise figure increases. Soft; parameter: noise-figure increase (dB).",
    "AMP_GAIN_TILT": "An amplifier's gain is no longer flat across the band. Soft; parameter: tilt (dB across the band).",
    "PUMP_CURRENT_DRIFT": "An amplifier's pump drive current drifts upward as the pump ages. Incipient; parameter: drift rate (per cent per day).",
    "TX_LASER_FAIL": "A coherent transponder's transmit laser fails. Hard.",
    "CARD_FAIL": "A card fails. Hard.",
    "RX_DEGRADATION": "A coherent transponder's receiver degrades, adding a penalty. Soft; parameter: penalty (dB).",
    "LASER_BIAS_DRIFT": "A coherent transponder's laser bias current drifts upward as the laser ages. Incipient; parameter: drift rate (per cent per day).",
    "MARGIN_EROSION": "An optical channel's margin declines slowly. Incipient; parameter: rate (dB per day).",
    "WSS_ATTEN_DRIFT": "A WSS's attenuation for one channel changes. Soft; parameters: channel, attenuation change (dB).",
    "WSS_FILTER_SHIFT": "A WSS's passband for one channel is offset in frequency. Soft; parameters: channel, centre-frequency offset (GHz).",
    "WSS_PORT_FAIL": "A WSS port fails. Hard; parameter: port.",
    "DGD_RISE": "A fibre span's mean differential group delay increases. Soft; parameter: DGD increase (ps).",
    "SOP_TRANSIENT": "A fast change of the state of polarisation on a fibre span (typically aerial). Transient.",
    "MECHANICAL_DISTURBANCE": "A brief mechanical disturbance of a fibre span or patch panel causes a loss excursion. Transient.",
    "CHANNEL_LOADING_TRANSIENT": "A brief power excursion on an OMS as channels are added or removed. Transient.",
    "PROTECTION_SWITCH_HIT": "A protection switch interrupts a protected service briefly. Transient.",
    "OTN_SWITCH_FAIL": "An OTN switch's fabric fails. Hard.",
    "XC_MISCONNECT": "An OTN cross-connect is set wrongly, delivering an ODU to the wrong destination. Hard.",
    "CLIENT_PORT_FAIL": "A client port or its optic fails. Hard.",
    "INSTRUMENT_MISREPORT": "An instrument reports a wrong value. Monitoring fault.",
    "TELEMETRY_STALE": "An instrument keeps reporting a frozen value. Monitoring fault.",
    "any_upstream_failure": "Any failure on the path to the far end (used where documentation names no specific cause).",
    "any_server_failure": "Any failure of the server layers an ODU path depends on (used where documentation names no specific cause).",
}

SCOPES = {
    "all_channels": "Every channel of the whole multiplexed signal (the OMS or OTS).",
    "channel": "One optical channel.",
    "some_channels": "Some channels of the multiplexed signal, not all (e.g. at one band edge).",
    "port": "Every channel arriving at a ROADM on one degree's input port.",
    "unit": "The unit itself, not its traffic (e.g. a health indicator).",
    "this_receiver": "This receiver only.",
    "every_odu_through": "Every ODU cross-connected through the switch.",
    "one_odu": "One ODU.",
    "this_service": "This client service.",
}

TIERS = {
    "baseline": "Monitoring every unit has: alarms and 15-minute performance counters.",
    "power": "Optical power monitors (input, output, received power), fitted with the power monitoring option.",
    "ocm": "Optical channel monitors at ROADM degrees, per channel power and OSNR.",
    "coherent": "Advanced telemetry: coherent DSP quantities and equipment health currents, reported every second.",
}

RESOURCES = {
    "Amplifier": "An optical amplifier (EDFA) of the line.",
    "role:booster": "Amplifier role: at the transmitting end of an OMS.",
    "role:inline": "Amplifier role: at an intermediate amplifier site.",
    "role:preamp": "Amplifier role: at the receiving end of an OMS.",
    "LineInterface": "A coherent line interface terminating an optical channel.",
    "kind:transponder": "Line interface kind: transponder.",
    "kind:muxponder": "Line interface kind: muxponder.",
    "kind:otn_line": "Line interface kind: OTN line card.",
    "ROADM": "A reconfigurable optical add-drop multiplexer, one WSS per degree.",
    "OTNSwitch": "An OTN switch with an ODU cross-connect fabric.",
    "ClientPort": "A client port mapping a client signal into an ODU.",
}

NATURES = {
    "hard": "An abrupt outage: traffic lost.",
    "soft": "A degradation: traffic carried with reduced quality.",
    "incipient": "A slow trend that precedes failure; traffic not yet affected.",
    "transient": "A brief event, seconds or less.",
}

SEVERITIES = {"critical": "Critical alarm.", "major": "Major alarm.", "minor": "Minor alarm.",
              "not_alarmed": "Reported as a condition, not alarmed."}

BEHAVIOURS = {
    "default": "Any condition arriving at the unit not covered by another rule.",
    "pass": "Passes the arriving condition on unchanged.",
    "routing:per channel": "Switches each channel separately, so a line-wide condition continues per routed channel.",
}

OBS_GLOSS = {
    "amp_input_power": "Total optical power at an amplifier's input.",
    "amp_output_power": "Total optical power at an amplifier's output.",
    "pump_current_pct": "An amplifier's pump drive current, per cent of beginning of life.",
    "laser_bias_pct": "A transmitter's laser bias current, per cent of beginning of life.",
    "ocm_channel_power": "Per-channel power at a ROADM's channel monitor.",
    "ocm_osnr": "Per-channel OSNR at a ROADM's channel monitor.",
    "rx_power": "Optical power at a line receiver.",
    "rx_power_pm15": "Received power, 15-minute performance bins.",
    "errored_seconds_pm15": "Line errored seconds, 15-minute performance bins.",
    "est_osnr": "OSNR estimated by the coherent receiver's DSP.",
    "pre_fec_ber": "Pre-FEC bit error ratio at the coherent receiver.",
    "dgd": "Differential group delay estimated by the coherent receiver's DSP.",
    "sop_rate": "Rate of change of the state of polarisation estimated by the coherent receiver's DSP.",
    "client_errored_seconds_pm15": "Client errored seconds, 15-minute performance bins.",
    "client_unavailable_seconds_pm15": "Client unavailable seconds, 15-minute performance bins.",
    "alarm_only": "A monitor used only to raise alarms, not reported as values (e.g. an OSC receiver).",
}


def concepts() -> dict:
    """{rfc class: [concept dict in the study's semantic-model shape]}"""
    vocab = yaml.safe_load((ROOT / "knowledge" / "vocabulary.yaml").read_text())
    out = {k: [] for k in list(RFC9940) + list(LOCAL)}

    def add(cls, cid, label, gloss, example=""):
        out[cls].append({"id": cid, "label": label, "kind": cls, "gloss": gloss, "example": example,
                         "synonyms": [], "ref": None, "relations": [], "instances": []})

    for m, g in FAULT_MODES.items():
        add("Cause", m, m.replace("_", " ").lower(), g)
    for e, d in vocab["effects"].items():
        add("Condition", e, e.replace("_", " ").lower(), d.get("meaning", ""))
    for s, g in SCOPES.items():
        add("Scope", s, s.replace("_", " "), g)
    for loc, d in vocab["locations"].items():
        add("Location", loc, loc.replace("_", " "), d.get("meaning", ""))
    for o, g in OBS_GLOSS.items():
        add("Characteristic", o, o.replace("_", " "), g)
    for t, g in TIERS.items():
        add("Availability", t, t, g)
    for r, g in RESOURCES.items():
        add("Resource", r, r.split(":")[-1], g)
    for n, g in NATURES.items():
        add("Nature", n, n, g)
    for s, g in SEVERITIES.items():
        add("Severity", s, s.replace("_", " "), g)
    for b, g in BEHAVIOURS.items():
        add("Behaviour", b, b.split(":")[-1], g)
    return {k: v for k, v in out.items() if v}
