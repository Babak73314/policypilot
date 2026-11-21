"""Model list prices per million tokens (docs/reference/verified-facts.md, checked 2026-11-20).

Used only for the dashboard's cost estimate; billing is the provider's.
"""

from __future__ import annotations

# model: (input, output, cache read) in USD per 1M tokens
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "gpt-6.2-sol": (2.00, 10.00, 0.10),
    "gpt-6-luna": (0.10, 0.50, 0.01),
}


def estimate_cost_micros(model: str, input_tokens: int, output_tokens: int, cache_read: int) -> int:
    """Estimated cost in millionths of a US dollar."""
    prices = next((p for name, p in PRICES.items() if model.startswith(name)), None)
    if prices is None:
        return 0
    input_price, output_price, cache_price = prices
    fresh_input = max(0, input_tokens - cache_read)
    dollars = (
        fresh_input * input_price + cache_read * cache_price + output_tokens * output_price
    ) / 1_000_000
    return round(dollars * 1_000_000)
