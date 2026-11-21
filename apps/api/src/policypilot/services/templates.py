"""Channel templates with locale fallback (ADR-0013)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from policypilot.db.models import MessageTemplate


@dataclass(frozen=True)
class Template:
    key: str
    channel: str
    locale: str
    body: str
    params: tuple[str, ...]
    meta_template_name: str | None
    meta_language_code: str | None

    def render(self, **values: str) -> str:
        missing = [p for p in self.params if p not in values]
        if missing:
            raise KeyError(f"template {self.key} needs {missing}")
        return self.body.format(**values)


class TemplateStore:
    """Loaded once per process; templates change only through the seed."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], Template] = {}

    async def load(self, session: AsyncSession) -> None:
        rows = (await session.execute(select(MessageTemplate))).scalars()
        self._items = {
            (r.key, r.channel, r.locale): Template(
                key=r.key,
                channel=r.channel,
                locale=r.locale,
                body=r.body,
                params=tuple(r.params),
                meta_template_name=r.meta_template_name,
                meta_language_code=r.meta_language_code,
            )
            for r in rows
        }

    def get(self, key: str, channel: str, locale: str = "en") -> Template:
        language = locale.split("-")[0]
        for candidate in ((key, channel, locale), (key, channel, language), (key, channel, "en"),
                          (key, "web", language), (key, "web", "en")):  # fmt: skip
            if candidate in self._items:
                return self._items[candidate]
        raise KeyError(f"no template {key!r} for {channel}/{locale}")

    def text(self, key: str, channel: str, locale: str = "en", **values: str) -> str:
        return self.get(key, channel, locale).render(**values)
