#!/usr/bin/env python3
"""Export the PolicyPilot workflows from the running n8n into `workflows/n8n/` (standard library).

    python scripts/n8n_export.py          # or: make n8n-export

Edit a workflow in the n8n editor, export it with this script, review the diff and commit. The
script keeps only portable fields (no IDs of versions, timestamps, sharing or static data),
turns the instance-specific values back into the placeholders that `docker/n8n/import.sh` fills
in, and fails if a workflow contains credential data or one of the local secrets.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "workflows" / "n8n"
EXPORT_DIR = "/tmp/pp-export"  # noqa: S108 (a path inside the n8n container)
FILES = {
    "pp-events-router": "events-router.json",
    "pp-lead-follow-up": "lead-follow-up.json",
    "pp-daily-digest": "daily-digest.json",
}
KEEP = ("id", "name", "nodes", "connections", "settings", "pinData", "meta", "tags")
SECRET_KEYS = ("N8N_WEBHOOK_TOKEN", "N8N_INTEGRATION_TOKEN", "N8N_ENCRYPTION_KEY")


def dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def compose(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 (fixed argv, no shell)
        ["docker", "compose", *args],  # noqa: S607 (docker from PATH, as in the README)
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def sanitise(workflow: dict[str, Any], placeholders: list[tuple[str, str]]) -> dict[str, Any]:
    for node in workflow.get("nodes", []):
        for reference in (node.get("credentials") or {}).values():
            if "data" in reference:
                raise SystemExit(f"{workflow['id']}: node {node['name']!r} embeds credential data")
    text = json.dumps({key: workflow[key] for key in KEEP if key in workflow}, ensure_ascii=False)
    for value, placeholder in placeholders:
        text = text.replace(value, placeholder)
    cleaned: dict[str, Any] = json.loads(text)
    return cleaned


def main() -> int:
    env = dotenv()
    placeholders = sorted(
        [
            ("http://api:8000", "__POLICYPILOT_API_URL__"),
            (env.get("APP_BASE_URL", "http://localhost:8080"), "__APP_BASE_URL__"),
            ("agents@policypilot.test", "__AGENCY_INBOX__"),
        ],
        key=lambda pair: -len(pair[0]),
    )
    secrets = {key: env[key] for key in SECRET_KEYS if env.get(key)}
    export = f"rm -rf {EXPORT_DIR} && n8n export:workflow --backup --output={EXPORT_DIR}"
    compose("exec", "-T", "n8n", "sh", "-c", export)
    listed = compose("exec", "-T", "n8n", "ls", EXPORT_DIR).split()
    for name in listed:
        workflow_id = name.removesuffix(".json")
        if workflow_id not in FILES:
            print(f"skipping {name}: only the pp-* workflows are kept in git", file=sys.stderr)
            continue
        raw = json.loads(compose("exec", "-T", "n8n", "cat", f"{EXPORT_DIR}/{name}"))
        cleaned = sanitise(raw, placeholders)
        output = json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n"
        leaked = [key for key, value in secrets.items() if value in output]
        if leaked:
            raise SystemExit(f"{workflow_id}: contains the value of {', '.join(leaked)}")
        (OUT_DIR / FILES[workflow_id]).write_text(output, encoding="utf-8", newline="\n")
        print(f"wrote workflows/n8n/{FILES[workflow_id]}")
    compose("exec", "-T", "n8n", "rm", "-rf", EXPORT_DIR)
    print("review the diff before committing: git diff workflows/n8n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
