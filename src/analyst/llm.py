"""Thin OpenAI-compatible client for the local llama-server."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import openai
from openai import OpenAI

from .config import AgentConfig


class ModelUnavailable(Exception):
    """The model server could not be reached or did not answer in time."""


@dataclass
class ToolCall:
    id: str
    name: str
    raw_arguments: str


@dataclass
class LLMReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    reasoning: str | None = None
    # "stop" | "tool_calls" | "length" | ...; "length" means the output hit
    # the token limit and any tool call in it may have truncated arguments.
    finish_reason: str | None = None


class LLMClient:
    def __init__(self, cfg: AgentConfig, client: OpenAI | None = None):
        self._cfg = cfg
        self._client = client or OpenAI(
            base_url=cfg.base_url, api_key=cfg.api_key or "unused"
        )

    def chat(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMReply:
        t0 = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=self._cfg.model, messages=messages, tools=tools
            )
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise ModelUnavailable(f"model server unreachable: {e}") from e
        seconds = time.perf_counter() - t0

        msg = resp.choices[0].message
        tool_calls = [
            ToolCall(
                id=tc.id or f"call_{i}",
                name=tc.function.name,
                raw_arguments=tc.function.arguments or "",
            )
            for i, tc in enumerate(msg.tool_calls or [])
        ]
        usage = resp.usage
        return LLMReply(
            content=msg.content,
            tool_calls=tool_calls,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            seconds=seconds,
            reasoning=getattr(msg, "reasoning_content", None),
            finish_reason=getattr(resp.choices[0], "finish_reason", None),
        )
