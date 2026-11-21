from __future__ import annotations

import pytest

from policypilot.security.ingress import sanitize


def test_model_text_hides_contact_details_but_stored_text_keeps_them() -> None:
    result = sanitize("I'm Jane, email jane@example.com or call +1 555 010 4567")
    assert "jane@example.com" in result.stored_text
    assert "[EMAIL]" in result.model_text
    assert "[PHONE]" in result.model_text
    assert "jane@example.com" not in result.model_text
    assert "4567" not in result.model_text


def test_sensitive_identifiers_never_survive() -> None:
    result = sanitize("card 4111 1111 1111 1111 and passport X1234567")
    for text in (result.stored_text, result.model_text):
        assert "4111" not in text
        assert "X1234567" not in text
    assert result.redactions == {"card": 1, "national_id": 1}
    assert result.sensitive_removed


@pytest.mark.parametrize(
    "text",
    [
        "I have type 2 diabetes. Can I still get life cover?",
        "My husband was diagnosed with cancer last year.",
        "I'm a smoker, does that matter?",
    ],
)
def test_volunteered_health_details_are_removed(text: str) -> None:
    result = sanitize(text)
    assert result.health_removed
    assert "[REDACTED:health]" in result.stored_text
    assert "diabetes" not in result.model_text.lower()
    assert "cancer" not in result.model_text.lower()


@pytest.mark.parametrize(
    "text",
    [
        "Does Solace Gold cover cancer treatment?",
        "What is the waiting period for planned surgery on Silver?",
        "I'm 34 with two kids and a budget of $80 a month",
        "coverage of 500000 for 20 years",
    ],
)
def test_ordinary_questions_are_kept(text: str) -> None:
    result = sanitize(text)
    assert not result.redacted
    assert result.stored_text == text
    assert result.model_text == text


def test_health_removal_applies_to_every_source() -> None:
    # Review 02, finding 19: agent, MCP, integration and review notes follow the same policy.
    for source in ("agent", "mcp", "integration", "review_note"):
        result = sanitize("Note: I have diabetes. Call back Monday.", source=source)  # type: ignore[arg-type]
        assert result.health_removed
        assert "diabetes" not in result.stored_text
    general = sanitize("Asked whether surgery has a waiting period.", source="agent")
    assert not general.health_removed


def test_control_characters_are_removed() -> None:
    assert sanitize("hi\x00 there\x1b").stored_text == "hi there"
