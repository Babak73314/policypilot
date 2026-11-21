"""Platform compatibility helpers."""

from __future__ import annotations

import asyncio
import selectors
import sys
from collections.abc import Coroutine
from typing import Any


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """psycopg's async mode cannot run on Windows' default Proactor loop; containers are Linux."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=selector_loop_factory)
    return asyncio.run(coro)
