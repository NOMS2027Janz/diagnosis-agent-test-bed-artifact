"""Typed schemas implementing "Diagnosis Agent Framework Specification" v0.1.

Section numbers in comments refer to that specification.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

SPEC_VERSION = "0.1"


# ---------------------------------------------------------------- Section 3: vocabulary

class Nature(str, Enum):
    HARD = "hard"
    SOFT = "soft"
    TRANSIENT = "transient"
    INCIPIENT = "incipient"
    CONTEXT = "context"
    SUBSTRATE = "substrate"


class ParameterSpec(BaseModel):
    name: str
    unit: str
    low: float
    high: float
    prior: Literal["uniform", "log_uniform"] = "uniform"


class FaultMode(BaseModel):
    """One catalogue entry (Section 3.1, Table 1)."""
    id: str
    applies_to: list[str]
    nature: Nature
    parameters: list[ParameterSpec] = []
    temporal_profile: Literal["step", "ramp", "pulse", "recurring"] = "step"
    signature: dict[str, str] = {}      # observable -> qualitative prediction
    base_rate_per_year: float = 0.0
    simulable: bool = True


NOVEL = "NOVEL"
INSTRUMENT_MISREPORT = "INSTRUMENT_MISREPORT"


# ---------------------------------------------------------------- Section 4: hypotheses

class ElementLocation(BaseModel):
    kind: Literal["element"] = "element"
    element: str

    def key(self) -> str:
        return f"el:{self.element}"


class SpanLocation(BaseModel):
    kind: Literal["span"] = "span"
    span: str
    km_low: float
    km_high: float

    @model_validator(mode="after")
    def _ordered(self):
        if self.km_high < self.km_low:
            raise ValueError("km_high must not be below km_low")
        return self

    def key(self) -> str:
        return f"sp:{self.span}[{self.km_low:g},{self.km_high:g}]"


class SetLocation(BaseModel):
    kind: Literal["set"] = "set"
    elements: list[str]

    def key(self) -> str:
        return "set:{" + ",".join(sorted(self.elements)) + "}"


Location = Union[ElementLocation, SpanLocation, SetLocation]


class ParamValue(BaseModel):
    """A point value (low == high) or an interval still to be fitted."""
    low: float
    high: float

    @classmethod
    def point(cls, v: float) -> "ParamValue":
        return cls(low=v, high=v)

    def key(self) -> str:
        return f"{self.low:g}" if self.low == self.high else f"[{self.low:g},{self.high:g}]"


class Cause(BaseModel):
    fault_mode: str
    location: Location = Field(discriminator="kind")
    parameters: dict[str, ParamValue] = {}
    onset: Optional[float] = None
    end: Optional[float] = None
    novel_description: Optional[str] = None

    def key(self) -> str:
        params = ",".join(f"{k}={v.key()}" for k, v in sorted(self.parameters.items()))
        return f"{self.fault_mode}@{self.location.key()}({params})"


class HypothesisKind(str, Enum):
    SINGLE = "single"
    COMPOUND = "compound"
    NONE_OF_THESE = "none_of_these"
    INSTRUMENT_FAULT = "instrument_fault"


class Hypothesis(BaseModel):
    id: str = ""
    kind: HypothesisKind
    causes: list[Cause] = []
    parent: Optional[str] = None

    @model_validator(mode="after")
    def _shape(self):
        n = len(self.causes)
        if self.kind == HypothesisKind.SINGLE and n != 1:
            raise ValueError("single hypothesis needs exactly one cause")
        if self.kind == HypothesisKind.COMPOUND and n < 2:
            raise ValueError("compound hypothesis needs two or more causes")
        if self.kind == HypothesisKind.NONE_OF_THESE and n != 0:
            raise ValueError("none_of_these carries no causes")
        if self.kind == HypothesisKind.INSTRUMENT_FAULT and (
                n != 1 or self.causes[0].fault_mode != INSTRUMENT_MISREPORT):
            raise ValueError("instrument_fault carries one INSTRUMENT_MISREPORT cause")
        return self

    def key(self) -> str:
        if self.kind == HypothesisKind.NONE_OF_THESE:
            return "NONE_OF_THESE"
        return "+".join(sorted(c.key() for c in self.causes))


# ---------------------------------------------------------------- Section 5: evidence

class EvidenceItem(BaseModel):
    id: str
    source: Literal["alarm", "event", "pm_counter", "telemetry", "instrument", "context", "query"]
    observable: str
    element: str
    instrument: Optional[str] = None
    t_start: float
    t_end: Optional[float] = None
    value: Union[float, str, bool, list[float]]
    sigma: Optional[float] = None


# ---------------------------------------------------------------- Section 6: tool contracts

class Contract(str, Enum):
    GENERATOR = "generator"
    LIKELIHOOD = "likelihood"
    RANKER = "ranker"
    ADVISOR = "advisor"


class ToolDescriptor(BaseModel):
    name: str
    version: str
    contract: Contract
    description: str
    inputs_schema: dict = {}
    consumes: list[str] = []
    applicability: str = ""
    cost_model: str = ""
    validation: dict = {}          # e.g. {"reliability": 0.9, "scenario_set": "..."}


class Candidate(BaseModel):
    hypothesis: Hypothesis
    prior_mass: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class GeneratorPayload(BaseModel):
    candidates: list[Candidate]
    residual_mass: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _mass(self):
        total = sum(c.prior_mass for c in self.candidates) + self.residual_mass
        if total > 1.0 + 1e-9:
            raise ValueError(f"candidate masses plus residual exceed one ({total:.6f})")
        return self


class LikelihoodValue(BaseModel):
    log_likelihood: Optional[float] = None   # None when declined
    sigma: float = 0.0
    declined: bool = False
    decline_reason: str = ""
    fitted: dict[str, float] = {}


class LikelihoodPayload(BaseModel):
    values: dict[str, LikelihoodValue]        # hypothesis key -> value
    background_log_likelihood: Optional[float] = None   # for none-of-these (Section 6.3)


class RankerPayload(BaseModel):
    ordering: list[str]                        # hypothesis keys, best first
    k: int
    recall_at_k: float = Field(ge=0.0, le=1.0)  # validated, at this pool size
    scores: dict[str, float] = {}              # trace only, never used by the ledger


class AdvisorPayload(BaseModel):
    recommendations: list[dict]


class ToolStatus(str, Enum):
    OK = "ok"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


class ToolCost(BaseModel):
    wall_s: float = 0.0
    queries: int = 0
    simulations: int = 0
    acquisitions: int = 0


class ToolResult(BaseModel):
    call_id: str
    tool: str
    version: str
    contract: Contract
    status: ToolStatus
    reason: str = ""
    evidence_consumed: list[str] = []
    payload: Optional[Union[GeneratorPayload, LikelihoodPayload, RankerPayload, AdvisorPayload]] = None
    trace: list[str] = []
    cost: ToolCost = ToolCost()


# ---------------------------------------------------------------- Section 8: LLM roles

class CaseFrame(BaseModel):
    question_type: Literal["causal", "structural", "temporal_observational",
                           "predictive", "counterfactual", "analogical"]
    anchors: list[str]
    t_start: Optional[float] = None
    t_end: Optional[float] = None
    symptom_signature: list[dict[str, str]] = []
    scope: str
    within_capability: bool
    capability_reason: str = ""


class PlanStep(BaseModel):
    action: Literal["call_tool", "acquire", "stop", "regenerate", "escalate"]
    tool: Optional[str] = None
    inputs: dict = {}
    rationale: str
    expected: str = ""
    decision_after: str = ""


class PlanningChannel(BaseModel):
    situation: str
    need: str
    strategy: str
    closeness: float = Field(ge=0.0, le=1.0)
    decision: Literal["continue", "stop", "escalate"]


class ProposedMechanism(BaseModel):
    fault_mode: str                       # catalogue id, or NOVEL
    novel_description: Optional[str] = None
    rationale: str
    strength: Literal["strong", "plausible", "long_shot"]


class ProposerOutput(BaseModel):
    mechanisms: list[ProposedMechanism]


# ---------------------------------------------------------------- Case frame ("Reference Network, Data Sets
# and Knowledge Graph" §6.1): the shared starting point of every generation line.

class FrameSymptom(BaseModel):
    kind: Literal["alarm", "event", "change", "pm"]
    observable: str                      # alarm type, event type or observable name
    element: str                         # element (or lake element) it was observed on
    t: float                             # time of the alarm, event or change
    detail: str = ""                     # e.g. "critical", "w->p", "level -4.2 dB", "lost", "ES 120"
    magnitude: Optional[float] = None
    evidence: list[str] = []


class FrameService(BaseModel):
    service: str
    state: Literal["down", "hit", "errored", "degraded", "ok"]
    on_protection: Optional[bool] = None
    evidence: list[str] = []


class ScenarioFrame(BaseModel):
    builder: str                         # deterministic | llm:<model>
    question: str
    question_kind: Literal["customer", "operations"]
    anchors: list[str]
    reported_at: float
    onset_estimate: float
    window: list[float]                  # [t0, t1] examined
    symptoms: list[FrameSymptom] = []
    services: list[FrameService] = []    # affected services and unaffected neighbours
    affected_elements: list[str] = []
    shared_elements: list[str] = []      # elements every affected service's working path depends on
    quiet_instruments: list[str] = []    # instruments in scope that observed no change
    quiet_streams: list[str] = []        # "observable@lake element" examined and unchanged (a monitor per channel)
    silent_streams: list[str] = []       # recorded instruments that reported nothing
    unknown_sources: list[str] = []      # streams from sources the graph does not know
    maintenance: list[dict] = []
    scope: list[str] = []                # elements examined
    notes: str = ""
