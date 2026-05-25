from __future__ import annotations

import os
from pathlib import Path


_LOADED = False


def _parse_env_lines(lines: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            parsed[key] = value
    return parsed


def load_local_env() -> None:
    global _LOADED
    if _LOADED:
        return

    repo_root = Path(__file__).resolve().parents[1]
    for candidate in (repo_root / ".env", repo_root / ".env.local"):
        if not candidate.exists():
            continue
        try:
            values = _parse_env_lines(candidate.read_text(encoding="utf-8").splitlines())
        except OSError:
            continue
        for key, value in values.items():
            os.environ.setdefault(key, value)

    _LOADED = True
