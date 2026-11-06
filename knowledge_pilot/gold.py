"""Gold standard for the pilot of an LLM-built graph: what every vendor wording denotes in the operator's vocabulary.

Written from the original vendor packages (knowledge/packages/{VA,VB}/*.yaml, which are in the operator's shared
vocabulary) and the prose manuals derived from them (knowledge_pilot/prose), wording by wording over the inventory
that step 2 extracted and step 3 lifted from (out/lifted*/<vendor>.wordings.json, "inventory"). Written on
28 October 2026 before any reconciliation output was read.

Each wording maps to the operator concepts it denotes (ids as in targets.concepts(): fault modes, effects, scopes,
locations, observables, tiers, resources, natures, severities, behaviours). A wording that bundles several meanings
maps to each. A wording with no operator counterpart maps to nothing (one-to-zero): alarm identifiers (the operator's
model has no alarm concepts), product codes' rates, negations ("other channels"), prose about the documentation itself.
Alarms carry a pseudo-target "alarm:<id>" used only between the two vendors.

A lifted concept denotes the union of what its label and synonyms denote. Gold pairs:
  vendor-operator: (vendor concept, operator concept) for every operator concept the vendor concept denotes;
  vendor-vendor:   (VA concept, VB concept) whenever what they denote overlaps (alarm pseudo-targets included).
"""
from __future__ import annotations

N = []          # no operator counterpart

VA = {
    # ---- causes (fault names and probable causes)
    "Transmit laser failure": ["TX_LASER_FAIL"], "transmit laser failure": ["TX_LASER_FAIL"], "laser failed": ["TX_LASER_FAIL"],
    "Card failure": ["CARD_FAIL", "UNIT_DOWN"], "card failed": ["CARD_FAIL"], "card failure": ["CARD_FAIL"],
    "Receiver degradation": ["RX_DEGRADATION"], "receiver degradation": ["RX_DEGRADATION"],
    "Laser ageing": ["LASER_BIAS_DRIFT"], "laser ageing": ["LASER_BIAS_DRIFT"],
    "Margin erosion": ["MARGIN_EROSION"],
    "fibre cut": ["FIBRE_CUT"], "Fibre break": ["FIBRE_CUT"],
    "EDFA dead": ["AMP_PUMP_FAIL"], "previous EDFA dead": ["AMP_PUMP_FAIL"],
    "jumper pulled": ["CONNECTOR_PULLED"], "OMS input jumper pulled": ["CONNECTOR_PULLED"],
    "ROADM ingress port blocked": ["WSS_PORT_FAIL"], "WSS ingress port failure": ["WSS_PORT_FAIL"],
    "high span loss": ["SPAN_LOSS_INCREASE"],
    "EDFA noise figure degradation": ["AMP_NF_RISE"], "Noise figure degradation": ["AMP_NF_RISE"],
    "DGD increase": ["DGD_RISE"], "WSS passband offset": ["WSS_FILTER_SHIFT"], "fast SOP event": ["SOP_TRANSIENT"],
    "contaminated connector": ["CONNECTOR_DIRTY"], "Any failure": ["any_upstream_failure", "any_server_failure"],
    "Gain loss": ["AMP_GAIN_DROP"], "gain loss": ["AMP_GAIN_DROP"],
    "Pump ageing": ["PUMP_CURRENT_DRIFT"], "pump ageing": ["PUMP_CURRENT_DRIFT"], "Gain tilt": ["AMP_GAIN_TILT"],
    "Fabric failure": ["OTN_SWITCH_FAIL"], "fabric failure": ["OTN_SWITCH_FAIL"],
    "Misconnection": ["XC_MISCONNECT"], "misconnection": ["XC_MISCONNECT"],
    "Port failure": ["CLIENT_PORT_FAIL"], "port failure": ["CLIENT_PORT_FAIL"], "client port failure": ["CLIENT_PORT_FAIL"],
    "WSS attenuation drift": ["WSS_ATTEN_DRIFT"],
    # ---- natures
    "Hard": ["hard"], "Soft": ["soft"], "Incipient": ["incipient"],
    # ---- scopes
    "the channel": ["channel"], "the transmitted channel": ["channel"], "received traffic": ["this_receiver"],
    "monitoring": N, "traffic alarms": N, "this receiver only": ["this_receiver"], "other channels": N,
    "the optical path": N, "LBC": ["laser_bias_pct"], "the channel's margin": ["channel"],
    "all channels": ["all_channels"], "every channel downstream": ["all_channels"],
    "this card": ["unit", "this_unit"], "channels at one band edge": ["some_channels"],
    "All ODUs": ["every_odu_through"], "each": ["every_odu_through"], "One ODU": ["one_odu"],
    "Client optic": ["ClientPort"], "port": ["ClientPort", "this_port"], "client service": ["this_service"],
    "one channel": ["channel"], "that channel": ["channel"], "One WSS input port": ["port"],
    "all channels arriving on that degree": ["port"], "other degrees": N,
    # ---- locations
    "the far-end receiver": ["far_end_receiver"], "Far-end": ["far_end_receiver"], "locally": ["this_unit"],
    "the receiver": ["this_unit"], "Far-end transmitter": ["far_end_transmitter"],
    "Anywhere upstream on the channel's path": ["upstream_on_path"], "Upstream on the path": ["upstream_on_path"],
    "This card": ["this_unit"], "on the path to the far end": ["path_to_far_end"], "downstream": N,
    "one band edge": ["some_channels"], "on the incoming span": ["upstream_span"], "previous EDFA": ["upstream_amplifier"],
    "OMS input": ["oms_input"], "the incoming span": ["upstream_span"], "through the switch": ["every_odu_through"],
    "on the active ODU path": ["active_odu_path"], "at either end": ["either_port"],
    "at a switch on the ODU path": ["odu_path_switch"], "on the working ODU path": ["working_odu_path"],
    "on the protection ODU path": ["protection_odu_path"], "This port": ["this_port"], "This switch": ["this_unit"],
    "after the node": N, "at the node": N,
    # ---- conditions
    "No light": ["SIGNAL_LOST"], "dLOS": ["SIGNAL_LOST"], "Loss of OTU frame": ["OTU_FRAME_LOST"], "dEQ": ["UNIT_DOWN"],
    "traffic alarms masked": ["UNIT_DOWN"],
    "Degraded light: low power, low OSNR, high DGD, passband penalty, receiver penalty, slow margin decline":
        ["POWER_LOW", "OSNR_LOW", "DGD_HIGH", "FILTER_PENALTY", "RX_PENALTY", "MARGIN_DECLINE"],
    "Pre-FEC errors once the degradation is larger than the channel margin": ["OTU_ERRORED"],
    "loss of frame if much larger": ["OTU_FRAME_LOST"], "Fast SOP change beyond tracking speed": ["POL_TRANSIENT"],
    "Errored seconds": ["OTU_ERRORED"], "loss of OTU frame": ["OTU_FRAME_LOST"],
    "ODU-AIS is sent downstream": ["ODU_AIS"], "OTU-BDI is returned": N,
    "Laser off or failed": ["TX_LASER_FAIL"], "loss of signal": ["SIGNAL_LOST"], "received traffic lost": ["UNIT_DOWN"],
    "monitoring silent": ["UNIT_DOWN"], "traffic alarms masked by the equipment alarm": ["UNIT_DOWN"],
    "Errors": ["RX_PENALTY", "OTU_ERRORED"], "normal": N, "LBC rising": ["LASER_AGEING"],
    "precedes transmit laser failure": N,
    "Slow decline of the channel's margin, usually from ageing of components along the path": ["MARGIN_DECLINE"],
    "No light at the receiver": ["SIGNAL_LOST"], "OTU frame lost, light present": ["OTU_FRAME_LOST"],
    "Errors above the degrade threshold (margin under about 1 dB)": ["OTU_ERRORED"],
    "Far end reports a defect on this card's transmitted signal": N,
    "Transmit laser fault detected": ["TX_LASER_FAIL"], "Card hardware fault": ["CARD_FAIL", "UNIT_DOWN"],
    "LBC above 120 % of beginning of life (internal supervision)": ["LASER_AGEING"],
    "The EDFA cards run in automatic gain control (AGC).": N,
    "The gain target is provisioned at turn-up equal to the attenuation of the fibre span ahead of the card.": N,
    "Under AGC, a drop of input level": ["POWER_LOW"], "an equal drop of output level": ["POWER_LOW"],
    "the optical signal-to-noise ratio (OSNR) of all channels degrades with it": ["OSNR_LOW"],
    "A card with no input light": ["SIGNAL_LOST"], "has no output light": ["SIGNAL_LOST"],
    "Other conditions at the input": ["default"], "are passed through to the output": ["pass"],
    "Every hut terminates the optical supervisory channel (OSC) and launches its own OSC onto the next span": N,
    "an OSC failure is confined to the span on which it occurs": ["OSC_LOST"],
    "no output light": ["SIGNAL_LOST"], "all channels lost": ["SIGNAL_LOST"], "low output": ["POWER_LOW"],
    "OSNR down": ["OSNR_LOW"], "output power normal": N, "PUMP-I climbs to maintain gain": ["PUMP_AGEING"],
    "ends in the end-of-life alarm": N, "channels low": ["POWER_LOW"], "Loss of light at IN-PWR": ["SIGNAL_LOST"],
    "OSC not received": ["OSC_LOST"], "No light at OUT-PWR with input light present": ["SIGNAL_LOST"],
    "Gain more than 2 dB from target": N, "PUMP-I above 120 % of beginning of life": ["PUMP_AGEING"],
    "The switch forwards ODU-AIS on each ODU it cross-connects.": ["ODU_AIS"], "ODU-AIS": ["ODU_AIS"],
    "a misdirected ODU": ["ODU_MISROUTED"], "the client service down": ["CLIENT_DOWN"],
    "ODU-AIS or a misdirected ODU": ["ODU_AIS", "ODU_MISROUTED"], "SNCP is configured": N,
    "the selector has a defect-free path to choose": N, "the service takes a hit only": ["CLIENT_HIT"],
    "interrupted": ["ODU_AIS"], "ODU-AIS forwarded": ["ODU_AIS"], "cross-connected to the wrong destination": ["ODU_MISROUTED"],
    "no optical symptom": N, "failed": N, "down": ["CLIENT_DOWN"], "Switching is per channel": ["routing:per channel"],
    "A line-wide impairment on an ingress degree therefore continues as a per-channel impairment on each channel routed onward.":
        ["routing:per channel"],
    "An upstream power loss within that range": ["POWER_LOW"], "is levelled out at the node": N,
    "the OSNR penalty it caused stays": ["OSNR_LOW"], "a larger loss": ["POWER_LOW"], "is passed on as low power": ["POWER_LOW"],
    "passes through": ["pass"], "Attenuation creeps above its setting": ["WSS_ATTEN_DRIFT"], "low in power": ["POWER_LOW"],
    "Passband shifted": ["FILTER_PENALTY"], "Passband narrowed": ["FILTER_PENALTY"], "power normal": N,
    "signal quality down": ["FILTER_PENALTY"], "fails": N, "blocked": ["SIGNAL_LOST"], "unaffected": N,
    # ---- characteristics
    "Received optical power": ["rx_power", "rx_power_pm15"], "errored seconds": ["errored_seconds_pm15"],
    "OSNR": ["est_osnr"], "pre-FEC BER": ["pre_fec_ber"], "DGD": ["dgd"], "SOP rate of change": ["sop_rate"],
    "Transmit laser bias current, % of beginning of life": ["laser_bias_pct"], "Composite input power": ["amp_input_power"],
    "Composite output power": ["amp_output_power"], "Pump laser drive current, % of beginning of life": ["pump_current_pct"],
    "OSC receiver status": ["alarm_only"], "client ES": ["client_errored_seconds_pm15"],
    "client UAS": ["client_unavailable_seconds_pm15"], "Per-channel power": ["ocm_channel_power"],
    # ---- resources
    "VA-TPD-400": ["LineInterface", "kind:transponder"], "400G transponder": ["LineInterface", "kind:transponder"],
    "VA-MXP-200": ["LineInterface", "kind:muxponder"], "200G muxponder": ["LineInterface", "kind:muxponder"],
    "VA-MXP-400": ["LineInterface", "kind:muxponder"], "400G muxponder": ["LineInterface", "kind:muxponder"],
    "VA-OTN-LC-200": ["LineInterface", "kind:otn_line"], "200G OTN line card": ["LineInterface", "kind:otn_line"],
    "VA-OTN-LC-400": ["LineInterface", "kind:otn_line"], "400G OTN line card": ["LineInterface", "kind:otn_line"],
    "EDFA card": ["Amplifier"],
    "transmit-side EDFA (booster), fitted at the head end of each optical multiplex section (OMS)": ["Amplifier", "role:booster"],
    "optical line amplifier (OLA), fitted at in-line amplifier huts": ["Amplifier", "role:inline"],
    "receive-side EDFA (pre-amp), fitted at the tail end of each OMS": ["Amplifier", "role:preamp"],
    "OLA": ["Amplifier", "role:inline"], "Pre-amp": ["Amplifier", "role:preamp"], "pre-amp": ["Amplifier", "role:preamp"],
    "All": N, "OTN cross-connect switch": ["OTNSwitch"], "client interface port": ["ClientPort"],
    "Client port": ["ClientPort"], "Switch": ["OTNSwitch"], "colourless, directionless, contentionless ROADM": ["ROADM"],
    "one WSS module per degree": N,
    # ---- availability
    "Standard": ["baseline"], "Power monitoring licence": ["power"], "Advanced monitoring licence": ["coherent"],
    "15-minute bins (standard)": ["baseline"], "OCM licence": ["ocm"],
    # ---- severities
    "Critical": ["critical"], "Minor": ["minor"], "Not alarmed": ["not_alarmed"], "Major": ["major"],
    # ---- behaviours (whole notes)
    "On loss of OTU frame, G.798 consequent actions apply: ODU-AIS is sent downstream on every ODU in the OTU, and OTU-BDI is returned to the far-end transmitter.": N,
    "Every hut terminates the optical supervisory channel (OSC) and launches its own OSC onto the next span.": N,
    "Client services may be protected with SNCP: a working and a protection ODU path, with the selector at the receiving client port.": N,
    "Switching is per channel at each egress degree. A line-wide impairment on an ingress degree therefore continues as a per-channel impairment on each channel routed onward.":
        ["routing:per channel"],
}

VB = {
    # ---- causes
    "WSS attenuation drift": ["WSS_ATTEN_DRIFT"], "WSS passband shift": ["WSS_FILTER_SHIFT"],
    "WSS input port failure": ["WSS_PORT_FAIL"], "Transmit laser failure": ["TX_LASER_FAIL"],
    "Card failure": ["CARD_FAIL"], "Receiver degradation": ["RX_DEGRADATION"],
    "the far-end transmitter's laser has failed": ["TX_LASER_FAIL"], "the far-end card has failed": ["CARD_FAIL"],
    "a broken fibre": ["FIBRE_CUT"], "a failed amplifier": ["AMP_PUMP_FAIL"], "a disconnected patch cord": ["CONNECTOR_PULLED"],
    "a blocked ROADM port": ["WSS_PORT_FAIL"], "a rise in fibre loss": ["SPAN_LOSS_INCREASE"],
    "a rise in an amplifier's noise": ["AMP_NF_RISE"], "a rise in differential group delay": ["DGD_RISE"],
    "a misaligned ROADM passband": ["WSS_FILTER_SHIFT"], "a fast polarisation transient": ["SOP_TRANSIENT"],
    "degradation of this unit's own receiver": ["RX_DEGRADATION"], "a dirty connector": ["CONNECTOR_DIRTY"],
    "any failure": ["any_upstream_failure", "any_server_failure"], "transmit laser failure": ["TX_LASER_FAIL"],
    "failure": ["CARD_FAIL", "CLIENT_PORT_FAIL", "OTN_SWITCH_FAIL"],
    "Pump or unit failure": ["AMP_PUMP_FAIL"], "Gain loss": ["AMP_GAIN_DROP"], "Pump ageing": ["PUMP_CURRENT_DRIFT"],
    "Gain tilt": ["AMP_GAIN_TILT"], "the fibre section feeding this unit is broken": ["FIBRE_CUT"],
    "its loss has risen sharply": ["SPAN_LOSS_INCREASE"], "the preceding amplifier has failed and emits no light": ["AMP_PUMP_FAIL"],
    "the patch cord may have been disconnected": ["CONNECTOR_PULLED"], "failure of this unit's pump laser": ["AMP_PUMP_FAIL"],
    "failure of the unit itself": ["AMP_PUMP_FAIL"], "gain loss": ["AMP_GAIN_DROP"], "pump ageing": ["PUMP_CURRENT_DRIFT"],
    "Switch fabric failure": ["OTN_SWITCH_FAIL"], "Wrong cross-connection": ["XC_MISCONNECT"],
    "Client port failure": ["CLIENT_PORT_FAIL"], "a failure of the client port": ["CLIENT_PORT_FAIL"],
    "a wrong cross-connection": ["XC_MISCONNECT"], "a fabric failure": ["OTN_SWITCH_FAIL"],
    # ---- natures
    "A degradation": ["soft"], "Hard failure": ["hard"], "A degradation, not an outage": ["soft"], "hard failure": ["hard"],
    "gradual or step degradation rather than an outage": ["soft"], "slow, incipient condition": ["incipient"],
    "degradation rather than an outage": ["soft"], "mostly just after provisioning changes": N,
    # ---- scopes
    "that channel": ["channel"], "The channel": ["channel"], "Every channel arriving at the node on that degree": ["port"],
    "channels arriving on the other degrees": N, "this channel": ["channel"], "The channel it transmits": ["channel"],
    "the traffic it receives": ["this_receiver"], "its monitoring": N, "its traffic alarms": N,
    "this receiver only": ["this_receiver"], "other channels": N, "the optical path": N, "the unit": ["unit"],
    "Every channel carried by the unit": ["all_channels"], "every channel": ["all_channels"], "Traffic": N, "the trend": N,
    "channels at one edge of the band": ["some_channels"], "Some channels, not all": ["some_channels"],
    "Every ODU cross-connected through the switch": ["every_odu_through"], "each": ["every_odu_through"],
    "one ODU": ["one_odu"], "The client optic": ["ClientPort"], "the port": ["ClientPort", "this_port"],
    "the client service": ["this_service"],
    # ---- locations
    "after the node": N, "there": N, "The receiver at the far end": ["far_end_receiver"], "at the far end": ["far_end_receiver"],
    "here": ["this_unit"], "this receiver": ["this_unit"], "the far-end transmitter": ["far_end_transmitter"],
    "the far end": ["far_end_transmitter"], "along the channel's path": ["upstream_on_path"],
    "the line receiver": ["this_unit"], "this unit's own receiver": ["this_unit"],
    "on the path to the far end": ["path_to_far_end"], "the signal this unit transmits": N, "on this unit": ["this_unit"],
    "this unit": ["this_unit"], "this card": ["this_unit"], "the output": N, "downstream": N,
    "the fibre section feeding this unit": ["upstream_span"], "the preceding amplifier": ["upstream_amplifier"],
    "at the input of the multiplex section": ["oms_input"],
    "the fibre section feeding the unit, not against the unit itself": ["upstream_span"],
    "through the switch": ["every_odu_through"], "the wrong destination": N,
    "on the ODU path currently in use": ["active_odu_path"], "at either end": ["either_port"],
    "in an OTN switch along the ODU path": ["odu_path_switch"], "client port": ["ClientPort", "this_port"],
    "on the working ODU path": ["working_odu_path"], "on the protection ODU path": ["protection_odu_path"],
    "this port": ["this_port"], "this switch's fabric": ["this_unit"], "OTN switch": ["OTNSwitch", "this_unit"],
    # ---- characteristics
    "the power of every channel": ["ocm_channel_power"], "the OSNR of every channel": ["ocm_osnr"],
    "Received power": ["rx_power_pm15", "rx_power"], "errored seconds, in 15-minute bins": ["errored_seconds_pm15"],
    "Optical power": ["rx_power"], "Estimated OSNR": ["est_osnr"], "pre-FEC bit error ratio": ["pre_fec_ber"],
    "differential group delay": ["dgd"], "polarisation change rate": ["sop_rate"],
    "Total optical power at the unit's input": ["amp_input_power"], "Total optical power at the unit's output": ["amp_output_power"],
    "Drive current of the pump laser, as a percentage of its beginning-of-life value": ["pump_current_pct"],
    "an indicator of pump health": ["pump_current_pct"], "Presence of the supervisory channel at the input": ["alarm_only"],
    "client errored seconds": ["client_errored_seconds_pm15"], "client unavailable seconds": ["client_unavailable_seconds_pm15"],
    # ---- resources
    "ROADM": ["ROADM"], "colourless, directionless and contentionless ROADM": ["ROADM"],
    "Transponder": ["LineInterface", "kind:transponder"], "Muxponder": ["LineInterface", "kind:muxponder"],
    "OTN line card": ["LineInterface", "kind:otn_line"], "All units": ["baseline"],
    "Booster amplifier (BA)": ["Amplifier", "role:booster"],
    "At the transmitting end of a multiplex section, after the ROADM": ["role:booster"],
    "Line amplifier (LA)": ["Amplifier", "role:inline"], "At intermediate amplifier sites": ["role:inline"],
    "Preamplifier (PA)": ["Amplifier", "role:preamp"],
    "At the receiving end of a multiplex section, before the ROADM": ["role:preamp"],
    "ODU cross-connect fabric": ["OTNSwitch"], "Client port, 10GE": ["ClientPort"], "Client port, 100GE": ["ClientPort"],
    "Client port, 400GE": ["ClientPort"],
    # ---- availability
    "available on nodes with the channel monitoring option": ["ocm"], "Units with the power monitoring option": ["power"],
    "Units with the advanced monitoring option": ["coherent"],
    "Each client port reports client errored seconds in 15-minute bins.": ["baseline"],
    "Each client port reports client unavailable seconds in 15-minute bins.": ["baseline"],
    # ---- severities
    "critical": ["critical"], "minor": ["minor"], "not alarmed": ["not_alarmed"], "major": ["major"],
    # ---- behaviours
    "Each channel leaving a degree is switched separately.": ["routing:per channel"],
    "When frame alignment is lost, the unit applies the G.798 consequent actions: an ODU alarm indication signal is inserted downstream on every ODU the OTU carries, and a backward defect indication is sent to the far-end transmitter.": N,
    "The OTN switch carries an ODU alarm indication signal onwards on every ODU cross-connected through it.": ["ODU_AIS"],
    # ---- conditions
    "An impairment affecting the whole incoming line": N,
    "continues, after the node, as an impairment of each channel routed onwards": ["routing:per channel"],
    "A power loss upstream of up to about 8 dB": ["POWER_LOW"], "is re-levelled at the node": N,
    "does not appear downstream as low power": N, "the loss of signal-to-noise ratio it caused remains": ["OSNR_LOW"],
    "A larger loss": ["POWER_LOW"], "passes on as low power": ["POWER_LOW"], "pass through the node unchanged": ["pass"],
    "power is low": ["POWER_LOW"], "power is normal": N, "signal quality is reduced": ["FILTER_PENALTY"],
    "is blocked": ["SIGNAL_LOST"], "are unaffected": N, "no light reaches the receiver": ["SIGNAL_LOST"],
    "declares loss of signal (G.798 dLOS)": ["SIGNAL_LOST"], "loses OTU frame alignment": ["OTU_FRAME_LOST"],
    "the unit itself has failed": ["UNIT_DOWN"], "declares an equipment defect (dEQ)": ["UNIT_DOWN"],
    "frame alignment is lost": ["OTU_FRAME_LOST"], "the unit's traffic alarms are masked by the equipment alarm": ["UNIT_DOWN"],
    "the signal arrives but is impaired": ["POWER_LOW", "OSNR_LOW", "DGD_HIGH", "FILTER_PENALTY", "RX_PENALTY", "MARGIN_DECLINE"],
    "lower power, a lower signal-to-noise ratio, a raised differential group delay, a filtering penalty from a misaligned or narrowed passband, a receiver-side penalty, or a slow loss of margin":
        ["POWER_LOW", "OSNR_LOW", "DGD_HIGH", "FILTER_PENALTY", "RX_PENALTY", "MARGIN_DECLINE"],
    "pre-FEC errors appear": ["OTU_ERRORED"], "Polarisation changes faster than the tracking range": ["POL_TRANSIENT"],
    "produce errored seconds": ["OTU_ERRORED"], "an ODU alarm indication signal is inserted downstream": ["ODU_AIS"],
    "a backward defect indication is sent": N, "loses the signal": ["SIGNAL_LOST"], "is lost": ["SIGNAL_LOST"],
    "goes silent": ["UNIT_DOWN"], "are masked by the equipment alarm": ["UNIT_DOWN"], "Errors appear": ["RX_PENALTY", "OTU_ERRORED"],
    "are normal": N, "is normal": N, "No light at the line receiver.": ["SIGNAL_LOST"],
    "OTU frame alignment lost while light is present.": ["OTU_FRAME_LOST"],
    "Signal degraded: pre-FEC errors above threshold": ["OTU_ERRORED"],
    "The far end reports a defect on the signal this unit transmits.": N,
    "The unit has detected a fault in its transmit laser.": ["TX_LASER_FAIL"], "Card hardware fault.": ["CARD_FAIL", "UNIT_DOWN"],
    "All three units operate": N, "constant-gain mode": N, "less light arrives at the input": ["POWER_LOW"],
    "less light leaves the output": ["POWER_LOW"], "the optical signal-to-noise ratio of every channel falls as well": ["OSNR_LOW"],
    "Loss of the OSC on one section": ["OSC_LOST"], "does not propagate to the next": N,
    "no light arrives": ["SIGNAL_LOST"], "no light leaves": ["SIGNAL_LOST"], "the input is weak": ["POWER_LOW"],
    "the output is weak": ["POWER_LOW"], "the output is noisier": ["OSNR_LOW"], "the output goes dark": ["SIGNAL_LOST"],
    "Output power is low": ["POWER_LOW"], "the signal-to-noise ratio falls": ["OSNR_LOW"],
    "raises its drive current to hold the gain": ["PUMP_AGEING"], "Traffic is unaffected until the drive current reaches its limit": N,
    "gives early warning": N, "leads eventually to the end-of-life alarm (AMP_PUMP_EOL)": N,
    "arrive weaker than the others": ["POWER_LOW"], "are affected": N, "no light arrives at its input": ["SIGNAL_LOST"],
    "no light arrives at the input of the receiving end of the multiplex section": ["SIGNAL_LOST"],
    "the supervisory channel is not received": ["OSC_LOST"], "light is present at its input but none leaves its output": ["SIGNAL_LOST"],
    "the gain departs from its set point by more than 2 dB": N,
    "the pump drive current exceeds 120 per cent of its beginning-of-life value": ["PUMP_AGEING"],
    "an ODU alarm indication signal": ["ODU_AIS"],
    "carries an ODU alarm indication signal onwards on every ODU cross-connected through it": ["ODU_AIS"],
    "an ODU delivered from the wrong source": ["ODU_MISROUTED"], "takes the client service down": ["CLIENT_DOWN"],
    "the service is protected": N, "the selector can choose a path free of defects": N,
    "the service suffers only a brief hit while the selector switches": ["CLIENT_HIT"], "is interrupted": ["ODU_AIS"],
    "an alarm indication signal is carried onwards": ["ODU_AIS"], "is delivered to the wrong destination": ["ODU_MISROUTED"],
    "There is no optical symptom.": N, "fails": N, "The client service has failed.": ["CLIENT_DOWN"],
    "An ODU alarm indication signal is received.": ["ODU_AIS"],
    "The far end reports that it is receiving an alarm indication signal.": N,
    "Trail trace identifier mismatch: the ODU received is not the one expected.": ["ODU_MISROUTED"],
    "The selector is on the protection path.": N, "The protection path has failed.": N,
    "Port hardware fault.": ["CLIENT_PORT_FAIL"], "Fabric fault.": ["OTN_SWITCH_FAIL"],
}

# ---- Revision 1 (28 October, after a first scoring): meanings the two vendors share that the operator's vocabulary
# has no concept for were left without a target, so every vendor-vendor pair between them scored wrong. They get
# pseudo-targets "x:<meaning>" (used only between the vendors), assigned over the whole list of wordings without a
# target in both inventories. One gold error is corrected: VB "is lost" also describes the card failure's lost received
# traffic (UNIT_DOWN), as VA "received traffic lost" does.
X = {
    "VA": {"normal": "unaffected", "unaffected": "unaffected", "power normal": "power_normal",
           "output power normal": "power_normal", "failed": "fails", "fails": "fails", "monitoring": "monitoring",
           "traffic alarms": "traffic_alarms", "other channels": "other_channels", "the optical path": "optical_path",
           "other degrees": "other_degrees", "downstream": "downstream", "after the node": "after_node",
           "at the node": "at_node", "is levelled out at the node": "relevel", "OTU-BDI is returned": "bdi_sent",
           "Far end reports a defect on this card's transmitted signal": "bdi_condition",
           "On loss of OTU frame, G.798 consequent actions apply: ODU-AIS is sent downstream on every ODU in the OTU, and OTU-BDI is returned to the far-end transmitter.": "g798_consequent",
           "SNCP is configured": "protected",
           "Client services may be protected with SNCP: a working and a protection ODU path, with the selector at the receiving client port.": "protected",
           "the selector has a defect-free path to choose": "selector_free", "no optical symptom": "no_optical_symptom",
           "Gain more than 2 dB from target": "gain_oor", "ends in the end-of-life alarm": "leads_to_eol",
           "The EDFA cards run in automatic gain control (AGC).": "constant_gain",
           "an OSC failure is confined to the span on which it occurs": "osc_confined",
           "A line-wide impairment on an ingress degree therefore continues as a per-channel impairment on each channel routed onward.": "line_wide_impairment",
           "Switching is per channel at each egress degree. A line-wide impairment on an ingress degree therefore continues as a per-channel impairment on each channel routed onward.": "line_wide_impairment"},
    "VB": {"are normal": "unaffected", "is normal": "unaffected", "are unaffected": "unaffected",
           "power is normal": "power_normal", "fails": "fails", "its monitoring": "monitoring",
           "its traffic alarms": "traffic_alarms", "other channels": "other_channels", "the optical path": "optical_path",
           "channels arriving on the other degrees": "other_degrees", "downstream": "downstream",
           "after the node": "after_node", "there": "at_node", "is re-levelled at the node": "relevel",
           "does not appear downstream as low power": "relevel", "a backward defect indication is sent": "bdi_sent",
           "The far end reports a defect on the signal this unit transmits.": "bdi_condition",
           "When frame alignment is lost, the unit applies the G.798 consequent actions: an ODU alarm indication signal is inserted downstream on every ODU the OTU carries, and a backward defect indication is sent to the far-end transmitter.": "g798_consequent",
           "the service is protected": "protected", "the selector can choose a path free of defects": "selector_free",
           "There is no optical symptom.": "no_optical_symptom",
           "the gain departs from its set point by more than 2 dB": "gain_oor",
           "leads eventually to the end-of-life alarm (AMP_PUMP_EOL)": "leads_to_eol", "constant-gain mode": "constant_gain",
           "does not propagate to the next": "osc_confined",
           "An impairment affecting the whole incoming line": "line_wide_impairment"},
}
for _v, _m in X.items():
    _g = VA if _v == "VA" else VB
    for _w, _x in _m.items():
        assert _w in _g, _w
        _g[_w] = _g[_w] + [f"x:{_x}"]
VB["is lost"] = VB["is lost"] + ["UNIT_DOWN"]

ALARMS = ("OCH_LOS", "OTU_LOF", "OTU_SD", "OTU_BDI", "EQPT_TX_FAIL", "EQPT_CARD_FAIL", "LASER_BIAS_HIGH",
          "AMP_INPUT_LOS", "OMS_LOS_P", "OSC_LOS", "AMP_FAIL", "AMP_GAIN_OOR", "AMP_PUMP_EOL", "CLIENT_SF", "ODU_AIS",
          "ODU_BDI", "ODU_TIM", "PROT_ACTIVE", "PROT_UNAVAIL", "EQPT_PORT_FAIL", "OTN_FABRIC_FAIL")
for _a in ALARMS:
    VA.setdefault(_a, [])
    VB.setdefault(_a, [])
    VA[_a] = VA[_a] + [f"alarm:{_a}"]
    VB[_a] = VB[_a] + [f"alarm:{_a}"]
VB.pop("LASER_BIAS_HIGH")            # VA only (VB documents no laser ageing)

GOLD = {"VA": VA, "VB": VB}


# ---- The held-out vendor VC (written 28 October, 07:30-08:30 Madrid, after surface v3 was fixed and before VC was
# extracted, lifted or reconciled). VC's manuals (prose/VC) were written from the VA packages in a third house style
# with its own names and its own alarm identifiers. This is the answer key at the level of the terms the manuals use;
# the wording-level gold for VC is made from it over the extracted inventory, before any VC reconciliation is read.
VC_TERMS = {
    # causes
    "Amplifier outage": ["AMP_PUMP_FAIL"], "the OFA before that fibre length may be out of service": ["AMP_PUMP_FAIL"],
    "the module is out of service": ["AMP_PUMP_FAIL"], "Gain sag": ["AMP_GAIN_DROP"], "Excess noise": ["AMP_NF_RISE"],
    "excess amplifier noise": ["AMP_NF_RISE"], "Pump wear-out": ["PUMP_CURRENT_DRIFT"], "Spectral tilt": ["AMP_GAIN_TILT"],
    "severed fibre": ["FIBRE_CUT"], "attenuation has jumped": ["SPAN_LOSS_INCREASE"],
    "fibre attenuation that has jumped": ["SPAN_LOSS_INCREASE"], "unplugged patch lead": ["CONNECTOR_PULLED"],
    "Transmitter dark": ["TX_LASER_FAIL"], "Module failure": ["CARD_FAIL"], "Receiver impairment": ["RX_DEGRADATION"],
    "Transmitter wear-out": ["LASER_BIAS_DRIFT"], "Margin creep": ["MARGIN_EROSION"], "DGD growth": ["DGD_RISE"],
    "polarisation swing": ["SOP_TRANSIENT"], "soiled connector end-face": ["CONNECTOR_DIRTY"],
    "misaligned router passband": ["WSS_FILTER_SHIFT"], "router input that has failed": ["WSS_PORT_FAIL"],
    "Over-attenuation": ["WSS_ATTEN_DRIFT"], "Passband misalignment": ["WSS_FILTER_SHIFT"],
    "Direction input fault": ["WSS_PORT_FAIL"], "Fabric outage": ["OTN_SWITCH_FAIL"],
    "Mis-provisioned connection": ["XC_MISCONNECT"], "Tributary failure": ["CLIENT_PORT_FAIL"],
    "any fault (on the route / between here and the other end)": ["any_upstream_failure", "any_server_failure"],
    # conditions
    "no light / darkness / dark input / output disappears / stops at the router": ["SIGNAL_LOST"],
    "weaker / weak / drop in level": ["POWER_LOW"], "noisier / noise penalty": ["OSNR_LOW"],
    "supervisory outage / supervisory link not received": ["OSC_LOST"], "too much DGD": ["DGD_HIGH"],
    "clipped by a filter / quality suffers": ["FILTER_PENALTY"], "spoiled by the receiver itself / errors at this receiver alone": ["RX_PENALTY"],
    "module dies / equipment defect (dEQ) / telemetry stops / traffic alarms hidden": ["UNIT_DOWN"],
    "OTU frame lost": ["OTU_FRAME_LOST"], "pre-FEC errors / errored seconds / degrade": ["OTU_ERRORED"],
    "ODU AIS": ["ODU_AIS"], "ODU meant for someone else / wrong destination / not the expected one": ["ODU_MISROUTED"],
    "customer service drops / failed": ["CLIENT_DOWN"], "short interruption while the selector moves": ["CLIENT_HIT"],
    "I-PUMP creeps up / pump drive above 120 per cent": ["PUMP_AGEING"], "laser bias climbs / above 120 per cent": ["LASER_AGEING"],
    "margin shrinks / worn away": ["MARGIN_DECLINE"], "polarisation swings faster than the tracker": ["POL_TRANSIENT"],
    # scopes
    "every wavelength (carried / downstream)": ["all_channels"], "one wavelength / the wavelength": ["channel"],
    "wavelengths at one end of the band": ["some_channels"], "all the light coming in on that direction": ["port"],
    "every ODU switched through": ["every_odu_through"], "one ODU": ["one_odu"], "the customer service": ["this_service"],
    "this receiver alone": ["this_receiver"], "the module (wear-out)": ["unit"],
    # locations
    "the fibre length feeding the module": ["upstream_span"], "the OFA before that fibre length": ["upstream_amplifier"],
    "the patch lead at the head of the wavelength section": ["oms_input"], "this module / this terminal / this cross-connect": ["this_unit"],
    "this tributary": ["this_port"], "the transmitter at the other end": ["far_end_transmitter"],
    "the terminal at the other end (receiving)": ["far_end_receiver"], "earlier on the (wavelength's) route": ["upstream_on_path"],
    "between here and the other end, the other end included": ["path_to_far_end"], "the tributary at either end": ["either_port"],
    "a cross-connect along the ODU route": ["odu_path_switch"], "the ODU route in use": ["active_odu_path"],
    "the primary route": ["working_odu_path"], "the standby route": ["protection_odu_path"],
    # characteristics
    "P-IN / aggregate power entering": ["amp_input_power"], "P-OUT / aggregate power leaving": ["amp_output_power"],
    "I-PUMP / pump drive": ["pump_current_pct"], "SV-RX": ["alarm_only"], "received light level": ["rx_power", "rx_power_pm15"],
    "errored seconds (15-minute)": ["errored_seconds_pm15"], "estimated OSNR": ["est_osnr"], "pre-FEC error ratio": ["pre_fec_ber"],
    "DGD": ["dgd"], "polarisation drift rate": ["sop_rate"], "transmit laser bias": ["laser_bias_pct"],
    "spectrum monitor level": ["ocm_channel_power"], "spectrum monitor OSNR": ["ocm_osnr"],
    "customer errored seconds": ["client_errored_seconds_pm15"], "customer unavailable seconds": ["client_unavailable_seconds_pm15"],
    # availability
    "Base": ["baseline"], "Optical Power pack": ["power"], "Insight pack": ["coherent"], "Spectrum pack": ["ocm"],
    # resources
    "OFA module": ["Amplifier"], "launch OFA (VC-OFA-T)": ["Amplifier", "role:booster"],
    "mid-span OFA (VC-OFA-M)": ["Amplifier", "role:inline"], "landing OFA (VC-OFA-R)": ["Amplifier", "role:preamp"],
    "single-client wavelength terminal": ["LineInterface", "kind:transponder"],
    "aggregating wavelength terminal": ["LineInterface", "kind:muxponder"],
    "digital-switching line module": ["LineInterface", "kind:otn_line"], "wavelength router (VC-WR-X)": ["ROADM"],
    "digital cross-connect (VC-DXC)": ["OTNSwitch"], "tributary (port)": ["ClientPort"],
    # natures, severities, behaviours
    "An outage": ["hard"], "A degradation": ["soft"], "Slow": ["incipient"],
    "Urgent": ["critical"], "Important": ["major"], "Advisory": ["minor"], "Information only": ["not_alarmed"],
    "steers every wavelength on its own": ["routing:per channel"], "Anything else ... sends on as it is / crosses as it arrived": ["pass"],
    # alarms: VC's identifiers, with the package alarm each corresponds to (for vendor-to-vendor scoring)
    "IN-DARK": ["alarm:AMP_INPUT_LOS"], "SEC-IN-DARK": ["alarm:OMS_LOS_P"], "SV-LOSS": ["alarm:OSC_LOS"],
    "OUT-DARK": ["alarm:AMP_FAIL"], "GAIN-DEV": ["alarm:AMP_GAIN_OOR"], "PUMP-EOL": ["alarm:AMP_PUMP_EOL"],
    "RX-DARK": ["alarm:OCH_LOS"], "FRAME-LOSS": ["alarm:OTU_LOF"], "DEGRADE": ["alarm:OTU_SD"],
    "REMOTE-DEFECT": ["alarm:OTU_BDI"], "TX-HW": ["alarm:EQPT_TX_FAIL"], "MOD-HW": ["alarm:EQPT_CARD_FAIL"],
    "TX-BIAS": ["alarm:LASER_BIAS_HIGH"], "TRIB-FAIL": ["alarm:CLIENT_SF"], "AIS-IN": ["alarm:ODU_AIS"],
    "REMOTE-AIS": ["alarm:ODU_BDI"], "TRACE-MISMATCH": ["alarm:ODU_TIM"], "ON-STANDBY": ["alarm:PROT_ACTIVE"],
    "STANDBY-LOST": ["alarm:PROT_UNAVAIL"], "TRIB-HW": ["alarm:EQPT_PORT_FAIL"], "FABRIC-HW": ["alarm:OTN_FABRIC_FAIL"],
}
