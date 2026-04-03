"""Retry decorators with exponential backoff for repovore."""

import asyncio
import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def async_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[F], F]:
    """Async decorator with exponential backoff."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_retries:
                        logger.error(
                            "Function %s failed after %d attempt(s): %s",
                            func.__name__,
                            attempt + 1,
                            exc,
                        )
                        raise
                    delay = min(base_delay * 2**attempt, max_delay)
                    if jitter:
                        delay += random.uniform(0, delay)
                    logger.warning(
                        "Function %s raised %s on attempt %d/%d, retrying in %.2fs",
                        func.__name__,
                        type(exc).__name__,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)

        return wrapper  # type: ignore[return-value]

    return decorator


def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[F], F]:
    """Sync decorator with exponential backoff."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_retries:
                        logger.error(
                            "Function %s failed after %d attempt(s): %s",
                            func.__name__,
                            attempt + 1,
                            exc,
                        )
                        raise
                    delay = min(base_delay * 2**attempt, max_delay)
                    if jitter:
                        delay += random.uniform(0, delay)
                    logger.warning(
                        "Function %s raised %s on attempt %d/%d, retrying in %.2fs",
                        func.__name__,
                        type(exc).__name__,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    time.sleep(delay)

        return wrapper  # type: ignore[return-value]

    return decorator
