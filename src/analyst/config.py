"""Agent configuration: load agent.yaml, expand ${VAR} from the environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _find_dotenv(start: Path) -> Path | None:
    """Walk up from start until a .env file is found (configs/ subdirs etc.)."""
    for d in (start, *list(start.parents)[:5]):
        f = d / ".env"
        if f.is_file():
            return f
    return None


def _load_dotenv(env_file: Path) -> None:
    """Load KEY=VALUE lines from env_file into os.environ (no override)."""
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _expand(value) -> str:
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            default = m.group(2)
            return os.environ.get(m.group(1), default if default is not None else "")
        return _ENV_VAR.sub(sub, value)
    return str(value)


@dataclass
class AgentConfig:
    model: str
    base_url: str
    api_key: str
    max_steps: int = 12
    max_total_tokens: int = 20000
    context_window: int = 32000
    db_path: str = "data/olist.sqlite"
    scratch_path: str = "data/scratch.sqlite"
    row_limit: int = 200
    timeout_s: float = 10.0
    rag: bool = False
    rag_index: str = ""
    sql_checks: bool = False
    clarify: bool = False
    # Keep only the newest N successful run_sql results verbatim; older ones
    # are replaced by a one-line marker. 0 disables (the default).
    microcompact_keep: int = 0
    run_name: str = "h1"


def load_config(path: str) -> AgentConfig:
    p = Path(path)
    env_file = _find_dotenv(p.parent)
    if env_file is not None:
        _load_dotenv(env_file)
    raw = yaml.safe_load(p.read_text()) or {}
    for k, v in raw.items():
        if isinstance(v, str):
            raw[k] = _expand(v)
    known = {f for f in AgentConfig.__dataclass_fields__}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown keys in {path}: {sorted(unknown)}")
    return AgentConfig(**raw)
