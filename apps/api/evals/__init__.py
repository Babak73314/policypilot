"""Scenario evals for the conversation graph (docs/design/testing-and-evals.md).

uv run python -m evals            # offline: scripted fake model, deterministic checks
uv run python -m evals --live     # opt-in: Claude, real embeddings, plus an LLM judge
"""
