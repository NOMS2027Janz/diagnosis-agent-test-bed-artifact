"""One client for every LLM role (specification §8), following the conventions of the
ad-hoc-semantic-reconciliation harness: the OpenAI client's structured-output parse with
Pydantic schemas, and a fallback to JSON mode for providers (such as DeepSeek) that reject
strict schemas.

Keys are read at run time from secrets/<provider>.env under the project root, or from the
environment. They are never printed, logged or written into run records; error messages are
scrubbed of anything that looks like a key.

Model selection: OPENAI_MODEL (default "gpt-5.6", the strong model) or DEEPSEEK_MODEL.
Other grades and families are used only when the LLM is the independent variable.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

PROVIDERS = {
    "openai": {"env": "OPENAI_API_KEY", "base_url": None, "model_env": "OPENAI_MODEL", "default": "gpt-5.6"},
    "deepseek": {"env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
                 "model_env": "DEEPSEEK_MODEL", "default": "deepseek-chat"},
}

_KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def project_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _load_key(provider: str) -> str:
    spec = PROVIDERS[provider]
    if os.environ.get(spec["env"]):
        return os.environ[spec["env"]]
    f = project_root() / "secrets" / f"{provider}.env"
    if f.exists():
        for line in f.read_text().splitlines():
            if line.startswith(spec["env"] + "="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(f"No key for {provider}: expected {spec['env']} in the environment or in secrets/{provider}.env")


def scrub(text: str) -> str:
    return _KEY_PATTERN.sub("sk-***", text)


@dataclass
class CallRecord:
    role: str
    provider: str
    model: str
    prompt_version: str
    latency_s: float
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    reasoning_tokens: Optional[int]
    ok: bool
    error: str = ""
    output: Optional[dict] = None


@dataclass
class LLMClient:
    provider: str = "openai"
    model: Optional[str] = None
    log: list[CallRecord] = field(default_factory=list)

    def __post_init__(self):
        from openai import OpenAI          # imported here so the rest of the package needs no openai
        spec = PROVIDERS[self.provider]
        self.model = self.model or os.environ.get(spec["model_env"], spec["default"])
        kwargs = {"api_key": _load_key(self.provider)}
        if spec["base_url"]:
            kwargs["base_url"] = spec["base_url"]
        self._client = OpenAI(**kwargs)

    def parse(self, role: str, prompt_version: str, system: str, user: str, schema: Type[T]) -> T:
        """One structured call. Raises on failure after recording it; never repairs output silently.
        OpenAI models are called through the Responses API (as tool sessions are); others through chat
        completions."""
        if self.provider == "openai":
            t0 = time.time()
            try:
                r = self._client.responses.parse(model=self.model, instructions=system,
                                                 input=[{"role": "user", "content": user}], text_format=schema)
                parsed = r.output_parsed
                if parsed is None:
                    raise ValueError("model returned no parsable output (refusal or empty)")
                self._record(role, prompt_version, t0, getattr(r, "usage", None), output=parsed.model_dump())
                return parsed
            except Exception as e:  # noqa: BLE001
                self._record(role, prompt_version, t0, None, ok=False, error=scrub(repr(e))[:500])
                raise RuntimeError(scrub(f"{role} call failed: {e!r}")) from None
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t0 = time.time()
        try:
            comp = self._parse_compat(messages, schema)
            parsed = comp.choices[0].message.parsed
            if parsed is None:
                raise ValueError("model returned no parsable output (refusal or empty)")
            u = getattr(comp, "usage", None)
            details = getattr(u, "completion_tokens_details", None) if u else None
            self.log.append(CallRecord(role, self.provider, self.model, prompt_version, time.time() - t0,
                                       getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None),
                                       getattr(details, "reasoning_tokens", None) if details else None,
                                       True, output=parsed.model_dump()))
            return parsed
        except Exception as e:  # noqa: BLE001
            self.log.append(CallRecord(role, self.provider, self.model, prompt_version, time.time() - t0,
                                       None, None, None, False, error=scrub(repr(e))[:500]))
            raise RuntimeError(scrub(f"{role} call failed: {e!r}")) from None

    def parse_messages(self, role: str, prompt_version: str, messages: list, schema: Type[T]) -> T:
        """A structured call at the end of a conversation (same recording as parse)."""
        t0 = time.time()
        try:
            comp = self._parse_compat(messages, schema)
            parsed = comp.choices[0].message.parsed
            if parsed is None:
                raise ValueError("model returned no parsable output (refusal or empty)")
            u = getattr(comp, "usage", None)
            details = getattr(u, "completion_tokens_details", None) if u else None
            self.log.append(CallRecord(role, self.provider, self.model, prompt_version, time.time() - t0,
                                       getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None),
                                       getattr(details, "reasoning_tokens", None) if details else None,
                                       True, output=parsed.model_dump()))
            return parsed
        except Exception as e:  # noqa: BLE001
            self.log.append(CallRecord(role, self.provider, self.model, prompt_version, time.time() - t0,
                                       None, None, None, False, error=scrub(repr(e))[:500]))
            raise RuntimeError(scrub(f"{role} call failed: {e!r}")) from None

    def _parse_compat(self, messages, schema):
        try:
            return self._client.chat.completions.parse(model=self.model, messages=messages, response_format=schema)
        except Exception as e:  # noqa: BLE001
            s = str(e).lower()
            if not ("response_format" in s or "json_schema" in s or "response format" in s):
                raise
            aug = [dict(m) for m in messages]
            aug[-1]["content"] += ("\n\nReturn ONLY a single JSON object (no prose, no markdown fences) that "
                                   "conforms to this JSON schema:\n" + json.dumps(schema.model_json_schema()))
            comp = self._client.chat.completions.create(model=self.model, messages=aug,
                                                        response_format={"type": "json_object"})
            text = comp.choices[0].message.content or ""
            i, j = text.find("{"), text.rfind("}")
            comp.choices[0].message.parsed = schema.model_validate_json(text[i:j + 1])
            return comp

    def tool_session(self, role: str, prompt_version: str, system: str, tools: list) -> "ToolSession":
        """A tool-using conversation. OpenAI reasoning models take function tools only through the
        Responses API; other providers use chat completions."""
        if self.provider == "openai":
            return _ResponsesSession(self, role, prompt_version, system, tools)
        return _ChatSession(self, role, prompt_version, system, tools)

    def _record(self, role, prompt_version, t0, usage, ok=True, error="", output=None):
        u = usage
        if u is None:
            self.log.append(CallRecord(role, self.provider, self.model, prompt_version, time.time() - t0,
                                       None, None, None, ok, error=error, output=output))
            return
        tin = getattr(u, "input_tokens", None) or getattr(u, "prompt_tokens", None)
        tout = getattr(u, "output_tokens", None) or getattr(u, "completion_tokens", None)
        det = getattr(u, "output_tokens_details", None) or getattr(u, "completion_tokens_details", None)
        self.log.append(CallRecord(role, self.provider, self.model, prompt_version, time.time() - t0, tin, tout,
                                   getattr(det, "reasoning_tokens", None) if det else None, ok, error=error,
                                   output=output))

    def records(self) -> list[dict]:
        return [r.__dict__ for r in self.log]


# ---------------------------------------------------------------------- tool-using conversations

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


class ToolSession:
    """start(user) -> calls; submit({call_id: result_json}) -> calls; finish(instruction, schema) -> parsed."""


def _tools_for_responses(tools: list) -> list:
    return [{"type": "function", "name": t["function"]["name"], "description": t["function"]["description"],
             "parameters": t["function"]["parameters"]} for t in tools]


class _ResponsesSession(ToolSession):
    def __init__(self, client: "LLMClient", role, pv, system, tools):
        self.c, self.role, self.pv, self.system = client, role, pv, system
        self.tools = _tools_for_responses(tools)
        self.prev: Optional[str] = None

    def _go(self, input_items):
        t0 = time.time()
        try:
            r = self.c._client.responses.create(model=self.c.model, instructions=self.system, input=input_items,
                                                tools=self.tools, previous_response_id=self.prev)
            self.prev = r.id
            calls = [ToolCall(o.call_id, o.name, o.arguments) for o in r.output if getattr(o, "type", "") == "function_call"]
            self.c._record(self.role, self.pv, t0, getattr(r, "usage", None), output={"tool_calls": len(calls)})
            return calls
        except Exception as e:  # noqa: BLE001
            self.c._record(self.role, self.pv, t0, None, ok=False, error=scrub(repr(e))[:500])
            raise RuntimeError(scrub(f"{self.role} tool turn failed: {e!r}")) from None

    def start(self, user: str) -> list:
        return self._go([{"role": "user", "content": user}])

    def submit(self, results: dict) -> list:
        return self._go([{"type": "function_call_output", "call_id": k, "output": v} for k, v in results.items()])

    def finish(self, instruction: str, schema):
        t0 = time.time()
        try:
            r = self.c._client.responses.parse(model=self.c.model, instructions=self.system,
                                               input=[{"role": "user", "content": instruction}],
                                               previous_response_id=self.prev, text_format=schema)
            parsed = r.output_parsed
            if parsed is None:
                raise ValueError("model returned no parsable output")
            self.c._record(self.role, self.pv, t0, getattr(r, "usage", None), output=parsed.model_dump())
            return parsed
        except Exception as e:  # noqa: BLE001
            self.c._record(self.role, self.pv, t0, None, ok=False, error=scrub(repr(e))[:500])
            raise RuntimeError(scrub(f"{self.role} final call failed: {e!r}")) from None


class _ChatSession(ToolSession):
    def __init__(self, client: "LLMClient", role, pv, system, tools):
        self.c, self.role, self.pv, self.tools = client, role, pv, tools
        self.messages = [{"role": "system", "content": system}]

    def _go(self):
        t0 = time.time()
        try:
            comp = self.c._client.chat.completions.create(model=self.c.model, messages=self.messages,
                                                          tools=self.tools, tool_choice="auto")
            msg = comp.choices[0].message
            calls = [ToolCall(tc.id, tc.function.name, tc.function.arguments) for tc in (msg.tool_calls or [])]
            self.messages.append({"role": "assistant", "content": msg.content or "",
                                  **({"tool_calls": [{"id": c.id, "type": "function",
                                                      "function": {"name": c.name, "arguments": c.arguments}}
                                                     for c in calls]} if calls else {})})
            self.c._record(self.role, self.pv, t0, getattr(comp, "usage", None), output={"tool_calls": len(calls)})
            return calls
        except Exception as e:  # noqa: BLE001
            self.c._record(self.role, self.pv, t0, None, ok=False, error=scrub(repr(e))[:500])
            raise RuntimeError(scrub(f"{self.role} tool turn failed: {e!r}")) from None

    def start(self, user: str) -> list:
        self.messages.append({"role": "user", "content": user})
        return self._go()

    def submit(self, results: dict) -> list:
        for k, v in results.items():
            self.messages.append({"role": "tool", "tool_call_id": k, "content": v})
        return self._go()

    def finish(self, instruction: str, schema):
        self.messages.append({"role": "user", "content": instruction})
        return self.c.parse_messages(self.role, self.pv, self.messages, schema)
