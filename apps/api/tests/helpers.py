"""Test helpers: SSE parsing and chat shortcuts."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if event and data is not None:
            events.append((event, json.loads(data)))
    return events


async def new_session(client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    response = await client.post("/api/v1/chat/sessions", json={"locale": "en"})
    assert response.status_code == 201, response.text
    body = response.json()
    return body["conversationId"], {"Authorization": f"Bearer {body['token']}"}


async def say(
    client: httpx.AsyncClient,
    conversation_id: str,
    headers: dict[str, str],
    text: str,
    client_message_id: str | None = None,
) -> tuple[int, list[tuple[str, dict[str, Any]]]]:
    response = await client.post(
        f"/api/v1/chat/sessions/{conversation_id}/messages",
        json={"text": text, "clientMessageId": client_message_id or f"c-{uuid.uuid4()}"},
        headers=headers,
        timeout=60,
    )
    if response.status_code != 200:
        return response.status_code, []
    return 200, parse_sse(response.text)


def completed(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    matches = [data for name, data in events if name == "message.completed"]
    assert matches, f"no message.completed in {[e for e, _ in events]}"
    return matches[-1]


def artifacts(events: list[tuple[str, dict[str, Any]]], kind: str) -> list[dict[str, Any]]:
    return [data for name, data in events if name == f"artifact.{kind}"]
