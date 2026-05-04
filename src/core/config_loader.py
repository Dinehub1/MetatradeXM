"""
config_loader.py — Configuration file loading with caching and error handling.
Provides singleton pattern for loading JSON configuration files with fallback defaults.
Used by modules that need to load JSON-based configuration (e.g., scoring_weights.json).
"""
import json
from pathlib import Path
from typing import Any, Optional, Dict

from core.paths import CONFIG_DIR
from core.logger_factory import get_logger

log = get_logger(__name__)


class ConfigLoader:
    """Singleton configuration file loader with caching.

    Loads JSON configuration files from CONFIG_DIR with fallback defaults.
    Caches loaded files to avoid repeated disk I/O and parsing.
    """

    _instance: Optional["ConfigLoader"] = None
    _cache: Dict[str, Any] = {}

    def __new__(cls) -> "ConfigLoader":
        """Create singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, filename: str, default: Optional[Any] = None) -> Any:
        """Load JSON configuration file with caching and error handling.

        Args:
            filename: Configuration filename (e.g., "scoring_weights.json")
            default: Default value if file doesn't exist or fails to parse

        Returns:
            Parsed JSON data or default value
        """
        # Return cached value if available
        if filename in self._cache:
            return self._cache[filename]

        path = CONFIG_DIR / filename
        try:
            if not path.exists():
                log.warning(f"Config file not found: {path}, using default")
                return default or {}

            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            self._cache[filename] = data
            log.debug(f"Loaded config: {filename}")
            return data

        except json.JSONDecodeError as e:
            log.error(f"Failed to parse {filename}: {e}")
            return default or {}
        except (IOError, OSError) as e:
            log.error(f"Failed to read {filename}: {e}")
            return default or {}

    def clear_cache(self, filename: Optional[str] = None) -> None:
        """Clear cached configuration.

        Args:
            filename: Specific file to clear from cache (None clears all)
        """
        if filename:
            self._cache.pop(filename, None)
        else:
            self._cache.clear()


# Global singleton instance
_loader = ConfigLoader()


def load_scoring_weights() -> Dict[str, Any]:
    """Load trading strategy scoring weights configuration.

    Returns:
        Dictionary with keys like buy_threshold, factor_weights, etc.
        Default: {"buy_threshold": 12, "sell_threshold": -12}
    """
    default = {
        "buy_threshold": 12,
        "sell_threshold": -12,
        "ranging_penalty": 2,
    }
    return _loader.load("scoring_weights.json", default=default)


def load_session_config() -> Dict[str, Any]:
    """Load session configuration (e.g., trading hours, markets).

    Returns:
        Dictionary with session parameters
        Default: Empty dict if file doesn't exist
    """
    return _loader.load("session_config.json", default={})


def load_config_file(filename: str, default: Optional[Any] = None) -> Any:
    """Load any JSON configuration file.

    Args:
        filename: Configuration filename
        default: Default value if file doesn't exist

    Returns:
        Parsed JSON data or default value
    """
    return _loader.load(filename, default=default)


def clear_config_cache(filename: Optional[str] = None) -> None:
    """Clear configuration cache.

    Args:
        filename: Specific file to clear (None clears all)
    """
    _loader.clear_cache(filename)
