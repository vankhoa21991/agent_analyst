"""The agent loop: model call, tool calls, results, repeat.

No framework. Budgets: max_steps (model calls), max_total_tokens, and a
character-based context estimate (chars / 3) checked before every call.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Callable

from .config import AgentConfig, load_config
from .db import Database
from .llm import LLMClient, LLMReply, ModelUnavailable
from .tools import ToolRegistry, ToolResult
from .tracing import Tracer

SYSTEM_PROMPT = """You are a data analyst agent for the Olist marketplace \
database (SQLite, Brazilian e-commerce, 2017-2018). Answer the user's \
business question using the tools.

Rules:
1. Explore before querying: use list_tables and describe_table to learn the \
schema before writing SQL.
2. Use run_sql for read-only queries. Data-changing SQL is rejected there.
3. To change data, use execute_write. It only runs after the user approves \
it. If the user rejects it, say so and do not retry.
4. When you are done, reply with a concise plain-text answer. Always show \
the SQL you used to get the numbers.
5. If a tool returns an error, read the error and fix your approach.
"""

CLARIFY_RULE = """6. If the question has more than one reasonable meaning \
(for example an undefined business term), call ask_user exactly once before \
running any SQL, then use the confirmed meaning.
"""


@dataclass
class AgentResult:
    answer: str
    sql: str | None
    steps: int
    prompt_tokens: int
    completion_tokens: int
    clarifications: int
    stop_reason: str  # answered | max_steps | token_budget | model_unavailable | context_overflow


class Agent:
    def __init__(
        self,
        cfg: AgentConfig,
        llm: LLMClient,
        tools: ToolRegistry,
        tracer: Tracer,
        ask_user: Callable[[str], str],
        approve: Callable[[str], bool],
    ):
        self._cfg = cfg
        self._llm = llm
        self._tools = tools
        self._tracer = tracer
        self._ask_user = ask_user
        self._approve = approve
        tools.ask_user = ask_user

    def _system_prompt(self) -> str:
        return SYSTEM_PROMPT + (CLARIFY_RULE if self._cfg.clarify else "")

    @staticmethod
    def _estimate_tokens(messages: list[dict]) -> int:
        return sum(len(str(m.get("content") or "")) for m in messages) // 3

    def run(self, question: str) -> AgentResult:
        cfg = self._cfg
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": question},
        ]
        specs = self._tools.specs()
        steps = 0
        prompt_tokens = 0
        completion_tokens = 0
        clarifications = 0
        last_sql: str | None = None

        def finish(answer: str, stop_reason: str) -> AgentResult:
            return AgentResult(
                answer=answer,
                sql=last_sql,
                steps=steps,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                clarifications=clarifications,
                stop_reason=stop_reason,
            )

        with self._tracer.run("agent run", input=question):
            while steps < cfg.max_steps:
                if self._estimate_tokens(messages) > cfg.context_window:
                    return finish(
                        "Stopped: the conversation exceeded the context "
                        f"window ({cfg.context_window} tokens estimated).",
                        "context_overflow",
                    )
                if prompt_tokens + completion_tokens >= cfg.max_total_tokens:
                    return finish(
                        "Stopped: the token budget "
                        f"({cfg.max_total_tokens} tokens) was used up.",
                        "token_budget",
                    )
                steps += 1
                try:
                    with self._tracer.span(
                        "llm call", step=steps,
                        prompt_tokens=self._estimate_tokens(messages),
                    ):
                        reply = self._llm.chat(messages, tools=specs)
                except ModelUnavailable as e:
                    return finish(
                        f"Stopped: the model server is unavailable ({e}).",
                        "model_unavailable",
                    )
                prompt_tokens += reply.prompt_tokens
                completion_tokens += reply.completion_tokens

                assistant_msg: dict = {"role": "assistant", "content": reply.content}
                if reply.reasoning:
                    assistant_msg["reasoning_content"] = reply.reasoning
                if reply.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.raw_arguments,
                            },
                        }
                        for tc in reply.tool_calls
                    ]
                messages.append(assistant_msg)

                if not reply.tool_calls:
                    return finish(reply.content or "", "answered")

                for tc in reply.tool_calls:
                    result = self._execute_tool_call(tc.name, tc.raw_arguments)
                    if tc.name == "ask_user" and result.ok:
                        clarifications += 1
                    if tc.name == "run_sql" and result.ok:
                        last_sql = json.loads(tc.raw_arguments).get("sql")
                    if result.needs_approval:
                        approved = self._approve(result.pending_sql or "")
                        if approved:
                            result = self._tools.run_approved_write(
                                result.pending_sql
                            )
                        else:
                            result = ToolResult(
                                "The user rejected the write; it was not "
                                "executed. Tell the user and do not retry."
                            )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result.text,
                        }
                    )
            return finish(
                f"Stopped after {cfg.max_steps} steps without a final answer.",
                "max_steps",
            )

    def _execute_tool_call(self, name: str, raw_arguments: str) -> ToolResult:
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            return ToolResult(
                f"Invalid JSON arguments: {e}. Retry the tool call with "
                "valid JSON.",
                ok=False,
            )
        return self._tools.call(name, arguments)


def build_agent(
    cfg: AgentConfig,
    ask_user: Callable[[str], str],
    approve: Callable[[str], bool],
) -> tuple[Agent, Database, Tracer]:
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    db.reset_scratch()
    tools = ToolRegistry(db, cfg)
    tracer = Tracer()
    return Agent(cfg, LLMClient(cfg), tools, tracer, ask_user, approve), db, tracer


def main() -> None:
    cfg_path = "agent.yaml"
    args = [a for a in sys.argv[1:]]
    question = " ".join(args)
    if not question:
        question = input("Question: ")
    cfg = load_config(cfg_path)
    db = Database(cfg.db_path, cfg.scratch_path, cfg.row_limit, cfg.timeout_s)
    db.reset_scratch()

    def ask_user(q: str) -> str:
        print(f"\nAGENT asks: {q}")
        return input("You: ")

    def approve(sql: str) -> bool:
        print("\nPending write (runs on the scratch copy only):")
        print(sql)
        return input("Approve? [y/N] ").strip().lower() == "y"

    agent, _, tracer = build_agent(cfg, ask_user, approve)
    res = agent.run(question)
    print(f"\nAnswer: {res.answer}")
    if res.sql:
        print(f"SQL: {res.sql}")
    print(
        f"[{res.stop_reason}] steps={res.steps} "
        f"tokens={res.prompt_tokens}+{res.completion_tokens} "
        f"clarifications={res.clarifications}"
    )
    tracer.close()


if __name__ == "__main__":
    main()
