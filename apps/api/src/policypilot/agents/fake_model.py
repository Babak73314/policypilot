"""A scripted, rule-based chat model for tests and keyless demos (ADR-0017).

It is NOT a quality signal. It answers by rules over the latest customer message and the node's
instructions, so the graph exercises the same code paths as with Claude: structured output,
tool calls, tool results and final text with quote placeholders and citation markers.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from policypilot.agents.schemas import ProfileDelta, RouteDecision

_NUMBER_WORDS = {
    "no": 0, "zero": 0, "none": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8,
}  # fmt: skip
_SCHEMAS: dict[str, type[BaseModel]] = {
    "RouteDecision": RouteDecision,
    "ProfileDelta": ProfileDelta,
}


def _last_human(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _system(messages: list[BaseMessage]) -> str:
    return "\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage))


def _directive(system: str, name: str) -> str | None:
    match = re.search(rf"^{name}:\s*(.*)$", system, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _line_of(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\b(car|auto|vehicle|driv\w*|motor)\b", lowered):
        return "auto"
    if re.search(r"\b(health|medical|doctor|hospital|deductible|silver|gold|bronze)\b", lowered):
        return "health"
    if re.search(r"\b(life|term|death|beneficiar\w*|family protection)\b", lowered):
        return "life"
    return None


def classify(text: str) -> RouteDecision:
    lowered = text.lower().strip()
    line = _line_of(lowered)
    rules: list[tuple[str, str]] = [
        (r"\b(weather|recipe|poem|bitcoin|stocks?|joke|homework|python code)\b", "out_of_scope"),
        (r"\b(agent|human|real person|someone real)\b", "human"),
        (r"\b(call me|contact me|get in touch|email me)\b", "contact_request"),
        (r"\b(submit|apply|application|sign me up|go ahead|confirm)\b", "apply"),
        (r"\b(compare|comparison|difference|versus|vs)\b", "compare"),
        (r"\b(quote|price|cost|how much|premium|cheapest|afford)\b", "quote"),
    ]
    is_question = "?" in lowered or re.match(
        r"(does|do|is|are|what|when|how|can|will|which)\b", lowered
    )
    policy_topic = re.search(
        r"\b(covered|exclu\w*|waiting period|claim\w*|grace period|cancel\w*|suicide|"
        r"maternity|pregnan\w*|rental|roadside|deliver\w*|surgery|conversion|lapse\w*)\b",
        lowered,
    )
    price_words = re.search(r"\b(price|cost|quote|premium|how much)\b", lowered)
    if is_question and policy_topic and not price_words:
        return RouteDecision(intent="product_question", line=line, confidence=0.8)
    for pattern, intent in rules:
        if re.search(pattern, lowered):
            return RouteDecision.model_validate({"intent": intent, "line": line, "confidence": 0.8})
    if re.fullmatch(r"(hi|hello|hey|thanks|thank you|good (morning|afternoon))[!. ]*", lowered):
        return RouteDecision(intent="greeting", line=None, confidence=0.9)
    return RouteDecision(intent="needs_analysis", line=line, confidence=0.6)


def _amount(raw: str, suffix: str | None) -> float:
    value = float(raw.replace(",", ""))
    if suffix and suffix.lower() in ("k", "thousand"):
        value *= 1_000
    if suffix and suffix.lower() in ("m", "million"):
        value *= 1_000_000
    return value


def extract(text: str) -> ProfileDelta:
    lowered = text.lower()
    data: dict[str, Any] = dict.fromkeys(ProfileDelta.model_fields)
    line = _line_of(lowered)
    data["lines"] = [line] if line else None
    age_pattern = r"\b(?:i'?m|i am|age[d]?|aged)\s+(\d{2})\b|\b(\d{2})[\s-]*(?:years?[\s-]old|yo)\b"
    if m := re.search(age_pattern, lowered):
        age = int(m.group(1) or m.group(2))
        data["age"] = age
        if line == "auto":
            data["driver_age"] = age
    if m := re.search(r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")\s+(kids|children|dependants|"
                      r"dependents|sons|daughters)\b", lowered):  # fmt: skip
        word = m.group(1)
        data["dependents"] = int(word) if word.isdigit() else _NUMBER_WORDS[word]
    if re.search(r"\bno (kids|children|dependants|dependents)\b", lowered):
        data["dependents"] = 0
    if m := re.search(r"\$?\s*([\d,]+(?:\.\d+)?)\s*(k|thousand)?\s*(?:a|per|/)\s*month", lowered):
        data["monthly_budget"] = _amount(m.group(1), m.group(2))
    elif m := re.search(r"budget[^\d$]{0,20}\$?\s*([\d,]+(?:\.\d+)?)", lowered):
        data["monthly_budget"] = _amount(m.group(1), None)
    cover = re.search(
        r"\$\s*([\d,]+(?:\.\d+)?)\s*(k|m|thousand|million)?\b(?!\s*(?:a|per|/)\s*month)", lowered
    )
    if cover and line in (None, "life") and "month" not in lowered[cover.end() : cover.end() + 8]:
        amount = _amount(cover.group(1), cover.group(2))
        if amount >= 10_000:
            data["coverage_goal"] = amount
    if m := re.search(r"\b(10|20|30)[- ]?(?:years?|yr)\b", lowered):
        data["term_years"] = int(m.group(1))
    if m := re.search(r"\b(north|central|south)\b", lowered):
        data["region"] = m.group(1)
    for tier in ("bronze", "silver", "gold"):
        if tier in lowered:
            data["tier_preference"] = tier
    vehicle_words = (
        ("suv", "suv"),
        ("economy", "economy"),
        ("premium", "premium"),
        ("luxury", "premium"),
        ("sedan", "standard"),
        ("standard car", "standard"),
        ("hatchback", "economy"),
    )
    for word, vehicle in vehicle_words:
        if word in lowered:
            data["vehicle_class"] = vehicle
    if m := re.search(r"\b(low|medium|high)\s+mileage\b", lowered):
        data["annual_mileage_band"] = m.group(1)
    elif m := re.search(r"([\d,]+)\s*(?:miles|mi)\b", lowered):
        miles = int(m.group(1).replace(",", ""))
        data["annual_mileage_band"] = (
            "low" if miles < 8000 else "high" if miles > 15000 else "medium"
        )
    if re.search(r"\bno claims\b|\bnever (made|had) a claim\b|\bclean record\b", lowered):
        data["claims_last_5y"] = 0
    elif m := re.search(r"\b(\d+|one|two|three|four)\s+claims?\b", lowered):
        word = m.group(1)
        data["claims_last_5y"] = int(word) if word.isdigit() else _NUMBER_WORDS[word]
    if m := re.search(r"\b(liability|comprehensive)\b", lowered):
        data["coverage_level"] = m.group(1)
    return ProfileDelta.model_validate(data)


_STOPWORDS = frozenset(
    {
        "about", "after", "also", "does", "from", "have", "into", "more", "that", "their",
        "there", "these", "this", "what", "when", "where", "which", "will", "with", "would",
        "your", "policy", "plan",
    }
)  # fmt: skip


def _content_words(text: str) -> set[str]:
    """Lowercase words of four letters or more, without a plural "s", minus filler words."""
    words = {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 4}
    return {w.removesuffix("s") for w in words - _STOPWORDS}


def policy_answer(chunks: list[dict[str, Any]], question: str) -> str | None:
    """The clause sentence that best matches the question, with its heading and citation.

    Sentences of the top three chunks are scored by the content words they and their clause
    heading share with the question; ties go to the better-ranked chunk, then to the earlier
    sentence.
    """
    wanted = _content_words(question)
    best: tuple[tuple[int, int, int], dict[str, Any], str | None, str] | None = None
    for rank, chunk in enumerate(chunks[:3]):
        body = re.sub(r"</?document[^>]*>", "", chunk["text"]).strip()
        body = re.sub(r"^\d+(?:\.\d+)*\s+", "", body)
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
        heading = None
        if len(sentences) > 1 and len(sentences[0].split()) <= 4:  # "Suicide.", "Grace period."
            heading, sentences = sentences[0].rstrip("."), sentences[1:]
        titled = _content_words(heading or "")  # the heading counts toward each of its sentences
        for position, sentence in enumerate(sentences):
            key = (len((_content_words(sentence) | titled) & wanted), -rank, -position)
            if best is None or key > best[0]:
                best = (key, chunk, heading, sentence)
    if best is None:
        return None
    _, chunk, heading, sentence = best
    clause = f"clause {chunk['section']}" + (f" ({heading})" if heading else "")
    return f"{chunk['document']}, {clause}: {sentence} [doc:{chunk['chunk_id']}]"


def _tool_results(messages: list[BaseMessage]) -> list[tuple[str, Any]]:
    """Tool results since the latest customer message, as (tool_name, parsed_content)."""
    results: list[tuple[str, Any]] = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, ToolMessage):
            try:
                content: Any = json.loads(str(message.content))
            except json.JSONDecodeError:
                content = {"error": str(message.content)}
            results.append((message.name or "", content))
    return list(reversed(results))


def _call(name: str, args: dict[str, Any], index: int) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"call_fake_{index}_{name}", "type": "tool_call"}


class ScriptedChatModel(BaseChatModel):
    role: str = "writer"

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable[LanguageModelInput, AIMessage]:
        formatted = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(tools=formatted)

    def with_structured_output(  # type: ignore[override]
        self, schema: type[BaseModel], *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[LanguageModelInput, BaseModel]:
        bound = self.bind(structured_schema=schema.__name__)

        def parse(message: AIMessage) -> BaseModel:
            return schema.model_validate_json(str(message.content))

        return bound | RunnableLambda(parse)

    # ------------------------------------------------------------------ generation

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        schema_name = kwargs.get("structured_schema")
        if schema_name:
            message = AIMessage(content=self._structured(schema_name, messages))
        elif kwargs.get("tools"):
            message = self._advisor(messages)
        else:
            message = AIMessage(content=self._write(messages))
        prompt_tokens = sum(len(str(m.content).split()) for m in messages)
        completion_tokens = len(str(message.content).split()) + 10 * len(message.tool_calls)
        message.usage_metadata = {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        message.response_metadata = {"model_name": "scripted-fake"}
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _structured(self, schema_name: str, messages: list[BaseMessage]) -> str:
        text = _last_human(messages)
        schema = _SCHEMAS[schema_name]
        if schema is RouteDecision:
            return classify(text).model_dump_json()
        return extract(text).model_dump_json()

    def _advisor(self, messages: list[BaseMessage]) -> AIMessage:
        system = _system(messages)
        intent = _directive(system, "Intent") or "quote"
        line = _directive(system, "Line") or _line_of(_last_human(messages)) or "life"
        complete = (_directive(system, "Profile complete") or "no") == "yes"
        budget = _directive(system, "Monthly budget")
        focus = _directive(system, "Product in focus")
        results = _tool_results(messages)
        text = _last_human(messages)

        args: dict[str, Any]
        if not results:
            if intent == "product_question":
                args = {"query": text, "product_id": focus if focus and focus != "none" else None}
                return AIMessage(content="", tool_calls=[_call("search_policy_documents", args, 0)])
            if complete and intent in ("quote", "compare", "needs_analysis"):
                max_budget = float(budget) if budget and budget != "none" else None
                args = {"line": line, "max_monthly_budget": max_budget}
                return AIMessage(content="", tool_calls=[_call("quote_products", args, 0)])
            args = {"line": line, "must_have": None}
            return AIMessage(content="", tool_calls=[_call("search_products", args, 0)])

        names = [name for name, _ in results]
        if intent == "compare" and "compare_products" not in names:
            ids = [
                q["product_id"]
                for name, content in results
                if name == "quote_products"
                for q in content.get("quotes", [])
            ] or [
                p["product_id"]
                for name, content in results
                if name == "search_products"
                for p in content.get("products", [])
            ]
            if len(ids) >= 2:
                args = {"product_ids": ids[:3]}
                return AIMessage(content="", tool_calls=[_call("compare_products", args, 1)])
        return AIMessage(content=self._compose(results, text))

    def _compose(self, results: list[tuple[str, Any]], question: str) -> str:
        parts: list[str] = []
        for name, content in results:
            if name == "quote_products":
                quotes = content.get("quotes", [])
                if content.get("over_budget"):
                    parts.append("Nothing fits that budget exactly; this is the closest option:")
                elif quotes:
                    parts.append("Here are estimates for your profile, cheapest first:")
                for quote in quotes[:3]:
                    parts.append(
                        f"- {quote['product']} by {quote['carrier']}: {quote['placeholder']}"
                    )
                if not quotes and content.get("ineligible"):
                    parts.append("None of the products accept this profile.")
            elif name == "get_quote" and "placeholder" in content:
                parts.append(
                    f"{content['product']} by {content['carrier']}: {content['placeholder']}"
                )
            elif name == "search_products":
                products = content.get("products", [])
                if products:
                    parts.append("These products could suit you:")
                for product in products[:4]:
                    parts.append(
                        f"- {product['name']} ({product['carrier']}): {product['summary']}"
                    )
            elif name == "compare_products":
                parts.append("I've put the products side by side so you can compare them.")
            elif name == "search_policy_documents":
                if answer := policy_answer(content.get("chunks", []), question):
                    parts.append(answer)
            elif "error" in content:
                parts.append("I couldn't complete that step: " + str(content["error"]))
        if not parts:
            parts.append("I couldn't find anything for that yet. Could you tell me a bit more?")
        if not any(name == "search_policy_documents" for name, _ in results):
            parts.append("Would you like to compare these, ask about the policy terms, or apply?")
        return "\n".join(parts)

    def _write(self, messages: list[BaseMessage]) -> str:
        system = _system(messages)
        if ask := _directive(system, "Ask for"):
            items = [item.strip() for item in ask.split(";") if item.strip()]
            joined = " and ".join(items[:2])
            return f"To find the right options I need a couple of details: {joined}."
        if _directive(system, "Task") == "summarise_for_agent":
            return "Customer asked to speak with an agent. " + _last_human(messages)[:200]
        if _directive(system, "Task") == "repair":
            draft = _last_human(messages)
            for phrase in ("guaranteed", "guarantee", "you are covered", "best price", "approved"):
                draft = re.sub(phrase, "", draft, flags=re.IGNORECASE)
            return re.sub(r"\s{2,}", " ", draft).strip()
        if _directive(system, "Task") == "summarise_history":
            return "Earlier: the customer discussed insurance options with the assistant."
        return "Thanks! I can help with life, health or auto insurance. What would you like to do?"
