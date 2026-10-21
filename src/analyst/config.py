"""Agent configuration: load agent.yaml, expand ${VAR} from the environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _load_dotenv(root: Path) -> None:
    """Load KEY=VALUE lines from root/.env into os.environ (no override)."""
    env_file = root / ".env"
    if not env_file.is_file():
        return
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
        return _ENV_VAR.sub(lambda m: os.environ.get(m.group(1), ""), value)
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
    sql_checks: bool = False
    clarify: bool = False


def load_config(path: str) -> AgentConfig:
    p = Path(path)
    _load_dotenv(p.parent)
    raw = yaml.safe_load(p.read_text()) or {}
    for k, v in raw.items():
        if isinstance(v, str):
            raw[k] = _expand(v)
    known = {f for f in AgentConfig.__dataclass_fields__}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown keys in {path}: {sorted(unknown)}")
    return AgentConfig(**raw)
