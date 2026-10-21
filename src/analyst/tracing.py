"""Langfuse tracing. No-op when Langfuse keys are absent."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from .config import _find_dotenv, _load_dotenv


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
    def run(self, name: str, input=None):
        """Root span for one agent run. Nested spans attach to it."""
        if self._lf is None:
            yield None
            return
        with self._lf.start_as_current_observation(
            name=name,
            as_type="span",
            input=input,
            metadata={**self._metadata, "session_id": self._session_id},
        ) as obs:
            yield obs

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
