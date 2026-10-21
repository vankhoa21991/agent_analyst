"""LLM user simulator for BIRD-Interact Mini tasks.

Adapted from the official BIRD-Interact repo
(mini_interact/knowledge_based/mini_interact_agent/src/envs/user_simulator/
us_env.py + us_prompt.py), CC BY-SA 4.0, BIRD Team & Google Cloud.

The simulator answers the agent's clarification questions using the task's
annotated ambiguities. In the public data `sol_sql` is empty (GT is
non-public), so the reference SQL is only available when it is present;
the per-term sql snippets from `user_query_ambiguity` are always available
and carry the clarification content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from analyst.llm import LLMClient, ModelUnavailable

# Official non-chat user-simulator prompt template (verbatim).
USER_PROMPT_TEMPLATE = """Your task is to simulate a human user that interacts with an LLM assistant in a dialogue.
You would like the LLM assistant to help you with the a NL query to get the correct SQL query.

Your goal is to engage in the conversation with the LLM assistant so that it can get to a clarified SQL query.
You should make use of the following hidden information about annotated potential ambiguities and the key sql snippet as clarification to answer the LLM assistant's questions.
YOU SHOULD BEHAVE LIKE A END USER THAT NEEDS HELP FROM AN ASSISTANT, SO YOUR CLARIFICATION SHOULD BE CONCISE AND IN NATURAL LANGUAGE.
YOU SHOULD ONLY ANSWER QUESTIONS WITH INFORMATION PROVIDED IN THE HIDDEN INFORMATION AND THE REFERENCE SQL.
IF THE ANSWER CANNOT BE FOUND IN THE HIDDEN INFORMATION OR THE REFERENCE SQL, SAY YOU DON\u2019T KNOW.
DON'T EXPOSE THE REFERENCE SQL TO THE LLM ASSISTANT.

Your Query: {ambiguous_query}

Hidden Information (only visible to you):
> Reference SQL (the actual SQL query that you want to get):
{reference_sql}

> Potential ambiguities terms in your query and key SQL snippets as clarification:
{clarifications}
 
------------------------------
Here is the dialogue between you and the LLM assistant so far:
{dialogue_history}

------------------------------
| Now you serve as user, directly outputting to answer to the LLM assitant IN TWO SENTENCES. DO NOT SAY ANYTHING ELSE.

User (You): """

USER_RESPONSE_CHARACTER_LIMIT = 400


@dataclass
class MiniInteractUserSimulator:
    """Simulates the task's human user; answers via the LLM."""

    llm: LLMClient
    record: dict[str, Any]
    dialogue_history: list[dict[str, str]] = field(default_factory=list)
    responses: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ambiguous_query = str(self.record["amb_user_query"])
        sol = self.record.get("sol_sql") or []
        self.reference_sql = sol[0] if sol else "(not available)"
        self.clarifications: list[dict[str, Any]] = []
        amb = self.record.get("user_query_ambiguity") or {}
        for key in ("critical_ambiguity", "non_critical_ambiguity"):
            for item in amb.get(key) or []:
                self.clarifications.append(
                    {
                        "ambiguity_term": item.get("term"),
                        "sql_snippet": item.get("sql_snippet"),
                        "is_mask": item.get("is_mask"),
                    }
                )
        self.reset()

    def reset(self) -> None:
        self.dialogue_history = [
            {"role": "User (You)", "content": self.ambiguous_query}
        ]

    def _clarifications_str(self) -> str:
        return "\n".join(
            f"- '{c['ambiguity_term']}': {c['sql_snippet']}"
            for c in self.clarifications
        ) or "No specific clarifications provided."

    def _history_str(self) -> str:
        result = ""
        round_id = 1
        for i, d in enumerate(self.dialogue_history):
            if i % 2 == 0:
                result += f"--- Round {round_id} ---\n{d['role']}:\n{d['content']}\n\n"
                round_id += 1
            else:
                result += f"{d['role']}:\n{d['content']}\n\n"
        return result

    def _prompt(self) -> str:
        return USER_PROMPT_TEMPLATE.format(
            ambiguous_query=self.ambiguous_query,
            reference_sql=self.reference_sql,
            clarifications=self._clarifications_str(),
            dialogue_history=self._history_str(),
        )

    def ask(self, question: str) -> str:
        """Feed the agent's clarification question; return the user reply."""
        self.dialogue_history.append({"role": "assistant", "content": question})
        try:
            reply = self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "You are a helpful user responding to "
                        "clarification questions.",
                    },
                    {"role": "user", "content": self._prompt()},
                ]
            )
            answer = (reply.content or "").strip()
        except ModelUnavailable as e:
            answer = f"(user simulator unavailable: {e})"
        answer = answer[:USER_RESPONSE_CHARACTER_LIMIT]
        self.dialogue_history.append({"role": "User (You)", "content": answer})
        self.responses.append(answer)
        return answer


def load_task(path: str | Path, instance_id: str) -> dict[str, Any]:
    for line in Path(path).read_text().splitlines():
        rec = json.loads(line)
        if rec["instance_id"] == instance_id:
            return rec
    raise KeyError(f"task {instance_id} not found in {path}")


def load_kb(
    path: str | Path, deleted_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    """Load a KB jsonl, optionally hiding entries (the benchmark masks
    knowledge entries referenced by `deleted_knowledge` in the record)."""
    deleted = set(deleted_ids or [])
    entries = []
    for line in Path(path).read_text().splitlines():
        entry = json.loads(line)
        if entry.get("id") in deleted:
            continue
        entries.append(entry)
    return entries
