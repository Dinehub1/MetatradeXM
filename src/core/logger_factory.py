"""
logger_factory.py — Centralized logger creation.
Provides get_logger() function to standardize logger initialization across all modules.
Loggers are cached after first creation for performance.
"""
import logging
from typing import Optional

# Cache for created loggers
_logger_cache: dict = {}


def get_logger(name: str) -> logging.Logger:
    """Get or create a logger with standardized configuration.

    Loggers are cached after first creation to avoid redundant initialization.
    All loggers use the root logger's handlers and formatting.

    Args:
        name: Logger name (typically __name__ for module-level use)

    Returns:
        Configured logging.Logger instance
    """
    if name not in _logger_cache:
        logger = logging.getLogger(name)
        _logger_cache[name] = logger
    return _logger_cache[name]


def reset_cache() -> None:
    """Reset the logger cache (useful for testing).

    This function clears all cached loggers, forcing new ones to be created
    on the next get_logger() call.
    """
    _logger_cache.clear()


def get_cached_loggers() -> dict:
    """Return the current logger cache (for introspection/testing).

    Returns:
        Dictionary of logger name → logger.Logger instances
    """
    return _logger_cache.copy()
