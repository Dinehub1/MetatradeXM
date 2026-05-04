"""
config.py — Centralized configuration management.
Provides safe environment variable access with fallback defaults.
Used by all modules to load configuration from .env or environment.
"""
import os
from typing import Any, Optional


def get_config(key: str, default: Any = None) -> Any:
    """Get configuration value from environment with fallback default.

    Priority: environment variable > default value

    Args:
        key: Configuration key (environment variable name)
        default: Default value if key is not set

    Returns:
        Environment value or default
    """
    return os.getenv(key, default)


def get_bool(key: str, default: bool = False) -> bool:
    """Get boolean configuration value from environment.

    Args:
        key: Configuration key (environment variable name)
        default: Default boolean value

    Returns:
        Boolean value (true/false regardless of case, or default)
    """
    val = os.getenv(key, "").lower()
    if val in ("true", "1", "yes", "on"):
        return True
    elif val in ("false", "0", "no", "off"):
        return False
    return default


def get_int(key: str, default: int = 0) -> int:
    """Get integer configuration value from environment.

    Args:
        key: Configuration key (environment variable name)
        default: Default integer value

    Returns:
        Integer value or default if conversion fails
    """
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    """Get float configuration value from environment.

    Args:
        key: Configuration key (environment variable name)
        default: Default float value

    Returns:
        Float value or default if conversion fails
    """
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


# ─── MT5 Bridge Configuration ──────────────────────────────────────────────────

def get_webhook_config() -> dict:
    """Get WebHook bridge configuration."""
    return {
        "webhook_url": get_config("WIN_WEBHOOK_URL", "http://206.72.198.54:5001"),
        "ws_url": get_config("WIN_WS_URL", ""),
        "auth_token": get_config("WEBHOOK_AUTH_TOKEN", "super_secret_token_2026"),
    }


def get_tv_config() -> dict:
    """Get TradingView bridge configuration."""
    return {
        "tv_ws_url": get_config("TV_WS_URL", "ws://localhost:8887"),
    }


# ─── AI Provider Configuration ─────────────────────────────────────────────────

def get_nvidia_config() -> dict:
    """Get NVIDIA API configuration."""
    return {
        "api_key": get_config("NVIDIA_API_KEY", ""),
        "api_key_2": get_config("NVIDIA_API_KEY_2", ""),
        "url": get_config("INVOKE_URL", "https://integrate.api.nvidia.com/v1/chat/completions"),
        "model": get_config("MODEL_NAME", "meta/llama-3.3-70b-instruct"),
        "max_tokens": get_int("MAX_TOKENS", 16384),
        "temperature": get_float("TEMPERATURE", 1.0),
        "top_p": get_float("TOP_P", 1.0),
        "stream": get_bool("STREAM", True),
        "thinking": get_bool("THINKING", True),
    }


def get_gemini_config() -> dict:
    """Get Google Gemini API configuration."""
    return {
        "api_key": get_config("GEMINI_API_KEY", ""),
        "model": get_config("GEMINI_MODEL", "gemini-2.0-flash"),
    }


# ─── Local AI Configuration ────────────────────────────────────────────────────

def get_ollama_config() -> dict:
    """Get Ollama (local AI) configuration."""
    return {
        "url": get_config("OLLAMA_URL", "http://localhost:11434/api/chat"),
        "model": get_config("OLLAMA_MODEL", "minimax-m2.7:cloud"),
    }


def get_ollama_url() -> str:
    """Get Ollama API endpoint URL."""
    return get_config("OLLAMA_URL", "http://localhost:11434/api/chat")


def get_ollama_model() -> str:
    """Get Ollama model name."""
    return get_config("OLLAMA_MODEL", "minimax-m2.7:cloud")


# ─── Trading Configuration ────────────────────────────────────────────────────

def get_trading_config() -> dict:
    """Get trading parameters."""
    return {
        "analysis_interval_s": get_int("ANALYSIS_INTERVAL_S", 60),
        "dry_run": get_bool("DRY_RUN", False),
    }


# ─── Logging Configuration ────────────────────────────────────────────────────

def get_log_level() -> str:
    """Get logging level (DEBUG, INFO, WARNING, ERROR)."""
    return get_config("LOG_LEVEL", "INFO")


def get_log_file() -> str:
    """Get log file path."""
    return get_config("LOG_FILE", "logs/bot.log")
