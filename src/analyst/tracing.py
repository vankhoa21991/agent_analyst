"""Langfuse tracing. No-op when Langfuse keys are absent.

A trace reads like a transcript of the run: a root ``agent run`` with the
question, the final answer and stop reason; one ``generation`` per model
step (input messages, output text + requested tool calls, token usage);
one ``tool`` observation per tool call (arguments, result). Every Langfuse
call is guarded: tracing failures never break a run.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .config import _find_dotenv, _load_dotenv

log = logging.getLogger(__name__)

TRACE_TEXT_LIMIT = 2000


def truncate(text: str, limit: int = TRACE_TEXT_LIMIT) -> str:
    """Cut text for the trace only (the agent still sees the full value)."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [truncated {len(text) - limit} chars]"


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.debug("tracing call failed", exc_info=True)
        return None


class _Obs:
    """A started observation; every method is safe on a disabled tracer."""

    def __init__(self, obs=None):
        self._obs = obs

    @property
    def trace_id(self) -> str | None:
        return getattr(self._obs, "trace_id", None) if self._obs else None

    def _update(self, **kw) -> None:
        if self._obs is not None:
            _guard(self._obs.update, **kw)

    def _finish(self) -> None:
        if self._obs is not None and hasattr(self._obs, "end"):
            _guard(self._obs.end)


class RunObs(_Obs):
    def end(self, output: str, stop_reason: str) -> None:
        self._update(output=output, metadata={"stop_reason": stop_reason})


class GenObs(_Obs):
    def end(self, output: dict, usage: dict, finish_reason: str | None) -> None:
        self._update(
            output=output,
            usage_details=usage,
            metadata={"finish_reason": finish_reason},
        )


class ToolObs(_Obs):
    def end(self, result: str, ok: bool) -> None:
        self._update(
            output=truncate(result),
            level="DEFAULT" if ok else "WARNING",
        )


def _parsed(arguments: str):
    try:
        return json.loads(arguments) if arguments else {}
    except (TypeError, ValueError):
        return arguments


class Tracer:
    def __init__(
        self,
        session_id: str | None = None,
        metadata: dict | None = None,
        dotenv_path: str | os.PathLike | None = None,
    ):
        env_file = _find_dotenv(Path(dotenv_path) if dotenv_path else Path.cwd())
        if env_file is not None:
            _load_dotenv(env_file)
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        enabled = os.environ.get("LANGFUSE_ENABLED", "1").strip().lower()
        self._lf = None
        if (public_key and secret_key) and enabled not in ("0", "false", "no"):
            from langfuse import Langfuse

            self._lf = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
            )
        self._session_id = session_id
        self._metadata = metadata or {}

    @property
    def enabled(self) -> bool:
        return self._lf is not None

    @contextmanager
    def run(self, name: str, input=None, metadata: dict | None = None):
        """Root observation for one agent run. Nested observations attach
        to it. Yields a RunObs."""
        if self._lf is None:
            yield RunObs()
            return
        stack = ExitStack()
        try:
            if self._session_id:
                from langfuse import propagate_attributes

                stack.enter_context(
                    propagate_attributes(session_id=self._session_id)
                )
            obs = stack.enter_context(
                self._lf.start_as_current_observation(
                    name=name,
                    as_type="span",
                    input=input,
                    metadata={
                        **self._metadata,
                        "session_id": self._session_id,
                        **(metadata or {}),
                    },
                )
            )
        except Exception:
            log.debug("tracing run start failed", exc_info=True)
            _guard(stack.close)
            yield RunObs()
            return
        try:
            yield RunObs(obs)
        finally:
            _guard(stack.__exit__, *sys.exc_info())

    def _start(self, **kw):
        if self._lf is None:
            return None
        return _guard(self._lf.start_observation, **kw)

    @contextmanager
    def generation(self, step: int, model: str, input: list[dict]):
        """One model call. Yields a GenObs."""
        obs = self._start(
            name="llm call", as_type="generation", model=model,
            input=input, metadata={"step": step},
        )
        wrapped = GenObs(obs)
        try:
            yield wrapped
        except Exception as e:
            wrapped._update(level="ERROR", status_message=f"{type(e).__name__}: {e}")
            raise
        finally:
            wrapped._finish()

    @contextmanager
    def tool(self, name: str, arguments: str):
        """One tool call. Yields a ToolObs."""
        obs = self._start(name=name, as_type="tool", input=_parsed(arguments))
        wrapped = ToolObs(obs)
        try:
            yield wrapped
        except Exception as e:
            wrapped._update(level="ERROR", status_message=f"{type(e).__name__}: {e}")
            raise
        finally:
            wrapped._finish()

    @contextmanager
    def span(self, name: str, **attrs):
        """One traced step (llm call, tool call). Records errors."""
        if self._lf is None:
            yield None
            return
        obs = self._lf.start_observation(
            name=name, as_type="span", input=attrs or None
        )
        try:
            yield obs
        except Exception as e:
            try:
                obs.update(
                    level="ERROR", status_message=f"{type(e).__name__}: {e}"
                )
            except Exception:
                pass
            raise
        finally:
            try:
                obs.end()
            except Exception:
                pass

    def close(self) -> None:
        if self._lf is not None:
            try:
                self._lf.flush()
            except Exception:
                pass
