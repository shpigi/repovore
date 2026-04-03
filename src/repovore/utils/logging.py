"""Logging utilities for repovore."""

import logging


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with structured format."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=numeric_level, format=fmt, force=True)


def get_logger(name: str, stage: str | None = None) -> logging.Logger:
    """Return logger with optional stage context in format."""
    logger = logging.getLogger(name)

    if stage is not None:
        handler = logging.StreamHandler()
        fmt = f"%(asctime)s [%(levelname)s] [{stage}] %(name)s: %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
        if not any(
            isinstance(h, logging.StreamHandler)
            and getattr(h.formatter, "_fmt", None) == fmt
            for h in logger.handlers
        ):
            logger.handlers.clear()
            logger.addHandler(handler)
            logger.propagate = False

    return logger
