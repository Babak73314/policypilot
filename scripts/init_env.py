#!/usr/bin/env python3
"""Create `.env` from `.env.example` with random local secrets (standard library only).

Every value equal to `change-me` is replaced with a random secret. Machine tokens get the `pp_`
prefix. The database URL is rebuilt with the generated password. An existing `.env` is never
overwritten.

Usage:
    python scripts/init_env.py            # from the repository root
    docker compose run --rm init-env      # without Python on the host
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "change-me"
TOKEN_KEYS = {"MCP_DEV_TOKEN", "N8N_INTEGRATION_TOKEN"}


def generate(key: str) -> str:
    value = secrets.token_urlsafe(32)
    return f"pp_{value}" if key in TOKEN_KEYS else value


def render(example: str) -> str:
    values: dict[str, str] = {}
    lines: list[str] = []
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(line)
            continue
        key, value = stripped.split("=", 1)
        if value == PLACEHOLDER:
            value = generate(key)
        values[key] = value
        lines.append(f"{key}={value}")
    output = "\n".join(lines) + "\n"
    if "POLICYPILOT_DB_PASSWORD" in values and "DATABASE_URL" in values:
        port = values.get("DB_PORT", "5432")
        url = (
            f"postgresql+psycopg://policypilot:{values['POLICYPILOT_DB_PASSWORD']}"
            f"@127.0.0.1:{port}/policypilot"
        )
        output = output.replace(f"DATABASE_URL={values['DATABASE_URL']}", f"DATABASE_URL={url}")
    return output


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else ROOT
    example, target = root / ".env.example", root / ".env"
    if target.exists():
        print(f"{target} already exists; not overwriting it.")
        return 0
    target.write_text(render(example.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"wrote {target} with fresh local secrets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
