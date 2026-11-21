#!/usr/bin/env python3
"""Smoke test for the running compose stack (standard library only).

    docker compose up --build -d
    python scripts/smoke.py              # or: make smoke

Checks, through the web entry point (nginx on 127.0.0.1:8080) and Mailpit:
  1. the web app and a deep link, API health and readiness through nginx, and the public catalog;
  2. a web chat turn, then a handoff request, whose `handoff.requested` event travels
     API outbox -> n8n events router -> Mailpit (an email that names the conversation);
  3. the WhatsApp simulator: a signed webhook round trip with the AI disclosure and a reply.
Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


def request(
    method: str,
    url: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60,
) -> tuple[int, str]:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"not an HTTP URL: {url}")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})  # noqa: S310
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 (checked above)
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except (urllib.error.URLError, OSError) as error:  # not up yet, or the connection dropped
        return 0, str(error)


def sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if name and data is not None:
            events.append((name, json.loads(data)))
    return events


def wait_for(description: str, check: Any, timeout: float = 60) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(1)
    raise AssertionError(f"timed out waiting for {description}")


class Smoke:
    def __init__(self, api: str, mailpit: str) -> None:
        self.api = api.rstrip("/")
        self.mailpit = mailpit.rstrip("/")
        self.passed = 0

    def ok(self, message: str) -> None:
        self.passed += 1
        print(f"  ok  {message}")

    def web(self) -> None:
        for path in ("/", "/agent/reviews"):
            status, body = request("GET", f"{self.api}{path}")
            assert status == 200, f"{path} answered {status}"
            assert '<div id="root">' in body, f"{path} did not serve the SPA"
        self.ok("web app and client-side routes served by nginx")

    def health(self) -> None:
        status, _ = request("GET", f"{self.api}/healthz")
        assert status == 200, f"/healthz answered {status}"
        status, body = request("GET", f"{self.api}/readyz")
        assert status == 200, f"/readyz answered {status}: {body}"
        self.ok("API health and readiness")
        status, body = request("GET", f"{self.api}/api/v1/catalog/products")
        assert status == 200, body
        products = json.loads(body)
        items = products.get("items", products) if isinstance(products, dict) else products
        assert len(items) >= 10, f"expected the seeded catalog, got {len(items)} products"
        self.ok(f"public catalog lists {len(items)} products")

    def chat_handoff_reaches_mailpit(self) -> None:
        status, body = request("POST", f"{self.api}/api/v1/chat/sessions", {"locale": "en"})
        assert status == 201, body
        session = json.loads(body)
        conversation_id = session["conversationId"]
        auth = {"Authorization": f"Bearer {session['token']}"}

        def say(text: str) -> list[tuple[str, dict[str, Any]]]:
            status, body = request(
                "POST",
                f"{self.api}/api/v1/chat/sessions/{conversation_id}/messages",
                {"text": text, "clientMessageId": f"smoke-{uuid.uuid4()}"},
                auth,
            )
            assert status == 200, f"turn answered {status}: {body}"
            return sse_events(body)

        events = say("Hi, I am looking for life insurance")
        assert any(name == "message.completed" for name, _ in events), events
        self.ok("web chat turn completed over SSE")
        events = say("Can I talk to a real person please?")
        assert any(name == "message.completed" for name, _ in events), events
        self.ok("handoff requested")

        query = urllib.parse.urlencode({"query": conversation_id})

        def email_found() -> Any:
            status, body = request("GET", f"{self.mailpit}/api/v1/search?{query}")
            if status != 200:
                return None
            messages = json.loads(body).get("messages", [])
            return messages[0] if messages else None

        message = wait_for("the handoff email in Mailpit", email_found, timeout=90)
        assert "[High priority]" in message["Subject"], message["Subject"]
        self.ok(f"n8n emailed the agency: {message['Subject']!r}")

    def whatsapp_simulator(self) -> None:
        user = f"sim-smoke-{uuid.uuid4().hex[:8]}"
        status, body = request(
            "POST", f"{self.api}/dev/whatsapp/simulate", {"userId": user, "text": "hello"}
        )
        assert status == 200, f"simulate answered {status}: {body}"

        def replies() -> Any:
            status, body = request("GET", f"{self.api}/dev/whatsapp/outbox?userId={user}&after=0")
            items = json.loads(body).get("items", []) if status == 200 else []
            return items if len(items) >= 2 else None

        items = wait_for("two WhatsApp replies", replies)
        assert "AI assistant" in items[0]["payload"]["text"], items[0]
        self.ok("WhatsApp simulator: disclosure and reply delivered")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8080", help="the web entry point")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    args = parser.parse_args()
    smoke = Smoke(args.api, args.mailpit)
    try:
        wait_for("the API", lambda: request("GET", f"{smoke.api}/healthz")[0] == 200, 120)
        smoke.web()
        smoke.health()
        smoke.chat_handoff_reaches_mailpit()
        smoke.whatsapp_simulator()
    except (AssertionError, urllib.error.URLError, OSError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print(f"smoke passed ({smoke.passed} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
