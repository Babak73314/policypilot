from __future__ import annotations

import pytest

from policypilot.security.pii import iban_valid, luhn_valid, mask_pii, redact_sensitive


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("my card is 4111 1111 1111 1111 thanks", "card"),
        ("card 4111-1111-1111-1111", "card"),
        ("IBAN DE89 3704 0044 0532 0130 00 please", "iban"),
        ("ssn 123-45-6789", "national_id"),
        ("my passport number is X1234567", "national_id"),
    ],
)
def test_sensitive_identifiers_are_redacted(text: str, kind: str) -> None:
    result = redact_sensitive(text)
    assert result.counts[kind] == 1
    assert f"[REDACTED:{kind}]" in result.text
    assert not any(ch.isdigit() for ch in result.text.split("[REDACTED")[1])


@pytest.mark.parametrize(
    "text",
    [
        "I'm 34 and my budget is $80 a month",
        "coverage of 500000 for 20 years",
        "order 1234 5678 9012 3456",  # not Luhn-valid
        "call me on 555-0100",
        "DE00 0000 0000 0000 0000 00",  # bad IBAN checksum
    ],
)
def test_ordinary_numbers_are_kept(text: str) -> None:
    result = redact_sensitive(text)
    assert not result.redacted
    assert result.text == text


def test_checksums() -> None:
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")
    assert iban_valid("GB82 WEST 1234 5698 7654 32")
    assert not iban_valid("GB82 WEST 1234 5698 7654 33")


def test_mask_pii_masks_email_and_phone() -> None:
    masked = mask_pii("reach jane.doe@example.com or +1 (555) 010-4567, card 4111111111111111")
    assert "jane.doe" not in masked
    assert "j***@example.com" in masked
    assert "4567" in masked
    assert "555" not in masked
    assert "[REDACTED:card]" in masked
