"""
ai_client.py — Multi-tier AI client for MetatradeXM.

Single source of truth for ALL AI calls in the system.

Three-tier fallback chain:
  T1 (NVIDIA Llama 3.3 70B) — primary, fast deep reasoning via NIM API
  T2 (gemini-2.5-pro)       — secondary fallback if NVIDIA is down
  T3 (gemini-2.5-flash)     — emergency fast fallback

Config (set in .env at project root):
    NVIDIA_API_KEY     = nvapi-...            ← NVIDIA NIM key (primary)
    INVOKE_URL         = https://...          ← NVIDIA endpoint
    MODEL_NAME         = meta/llama-3.3-...   ← NVIDIA model
    GEMINI_API_KEY     = AIza...              ← Google AI Studio key (fallback)
    GEMINI_PRO_MODEL   = gemini-2.5-pro       ← T2 fallback
    GEMINI_FLASH_MODEL = gemini-2.5-flash     ← T3 fallback
    GEMINI_TIMEOUT_S   = 60                   ← seconds
"""
from __future__ import annotations

import os
import re
import json
import time
import logging
import requests

log = logging.getLogger("ai_client")

# ── NVIDIA NIM API (T1 — Primary) ─────────────────────────────────────────────
_NVIDIA_KEY   = os.getenv("NVIDIA_API_KEY", "")
_NVIDIA_URL   = os.getenv("INVOKE_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
_NVIDIA_MODEL = os.getenv("MODEL_NAME", "meta/llama-3.3-70b-instruct")
_NVIDIA_MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))  # capped for trading JSON
_NVIDIA_TIMEOUT = 45  # seconds

# ── Google Gemini OpenAI-compatible endpoint (T2/T3 — Fallback) ────────────────
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
_GEMINI_URL  = f"{_GEMINI_BASE}/chat/completions"
_GEMINI_KEY  = os.getenv("GEMINI_API_KEY", "")
_T2_MODEL    = os.getenv("GEMINI_PRO_MODEL",   "gemini-2.5-pro")
_T3_MODEL    = os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash")
_GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT_S", "60"))

# ── Tier definitions: (provider, url, api_key, model, timeout, label) ──────────
_TIERS = []

# T1: NVIDIA (only if key is set)
if _NVIDIA_KEY:
    _TIERS.append(("nvidia", _NVIDIA_URL, _NVIDIA_KEY, _NVIDIA_MODEL, _NVIDIA_TIMEOUT, "T1-NVIDIA"))

# T2/T3: Gemini (only if key is set)
if _GEMINI_KEY:
    _TIERS.append(("gemini", _GEMINI_URL, _GEMINI_KEY, _T2_MODEL, _GEMINI_TIMEOUT, "T2-Gemini-Pro"))
    _TIERS.append(("gemini", _GEMINI_URL, _GEMINI_KEY, _T3_MODEL, max(30, _GEMINI_TIMEOUT // 2), "T3-Gemini-Flash"))

# Log active tiers at import time
_tier_names = [t[5] for t in _TIERS]
if _tier_names:
    log.info(f"[AI] Active tiers: {' → '.join(_tier_names)}")
else:
    log.warning("[AI] WARNING — No AI API keys configured! Set NVIDIA_API_KEY or GEMINI_API_KEY in .env")


# ── JSON extraction & repair helpers ─────────────────────────────────────────

def _strip_thinking(text: str) -> str:
    """
    Remove reasoning/thinking blocks that some models emit before the JSON.
    Handles: <think>...</think>, <thinking>...</thinking>, <thought>...</thought>,
    ◁think▷...◁/think▷, and leading prose before the first '{'.
    """
    text = re.sub(r"<(?:think|thinking|thought)>.*?</(?:think|thinking|thought)>",
                  "", text, flags=re.DOTALL)
    text = re.sub(r"◁think▷.*?◁/think▷", "", text, flags=re.DOTALL)
    first_brace = -1
    for i, ch in enumerate(text):
        if ch in '{[':
            first_brace = i
            break
    if first_brace > 0:
        prefix = text[:first_brace].strip()
        if not any(k in prefix for k in ['"', ':', ',']):
            text = text[first_brace:]
    return text.strip()


def _extract_balanced_json(text: str) -> str | None:
    """
    Extract the first balanced JSON object from text using brace counting.
    Handles nested objects correctly.
    """
    start = text.find('{')
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _repair_truncated_json(text: str) -> dict | None:
    """
    Attempt to repair a truncated JSON object by closing unclosed strings
    and braces. Handles the common case where max_tokens cuts off mid-string.
    """
    text = text.strip()
    if not text:
        return None

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    depth = 0
    in_string = False
    escape = False
    last_safe_pos = 0

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
            last_safe_pos = i + 1
        elif ch == ',':
            last_safe_pos = i + 1
        elif ch == '}':
            depth -= 1

    repaired = text[:last_safe_pos].rstrip(',').rstrip()

    in_string = False
    escape = False
    for ch in repaired:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string

    if in_string:
        repaired += '\"'

    depth = 0
    in_string = False
    escape = False
    for ch in repaired:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1

    repaired += '}' * depth

    try:
        result = json.loads(repaired)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        return None


def _extract_json(text: str) -> dict:
    """
    Parse the first valid JSON object from a model response.
    Handles: markdown fences, thinking tags, nested objects, truncated output.
    """
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    text = _strip_thinking(text)

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    balanced = _extract_balanced_json(text)
    if balanced:
        try:
            result = json.loads(balanced)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            repaired = _repair_truncated_json(balanced)
            if repaired is not None:
                return repaired

    repaired = _repair_truncated_json(text)
    if repaired is not None:
        return repaired

    raise json.JSONDecodeError("Could not extract valid JSON", text, 0)


def _log_line(data: dict, tier: str, model: str, label: str) -> str:
    """Build a concise log line for the result."""
    tag = f"[{label}] " if label else ""
    direction = data.get("direction", data.get("exit", "?"))
    conf = data.get("confidence")
    summary = f"{direction} {float(conf):.0%}" if conf is not None else str(direction)
    return f"  [AI] {tier} OK ({model}) — {tag}{summary}"


# HTTP status codes that are safe to retry (transient server-side issues)
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def _call_nvidia(messages: list, model: str, api_key: str, url: str,
                 max_tokens: int, timeout: int) -> dict:
    """Call NVIDIA NIM API (OpenAI-compatible format, non-streaming)."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": min(max_tokens, 1024),  # cap for trading JSON
        "temperature": 0.3,
        "top_p": 0.95,
        "stream": False,  # non-streaming for reliable JSON extraction
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _call_nvidia_with_retry(messages: list, model: str, api_key: str, url: str,
                            max_tokens: int, timeout: int, retries: int = 1) -> dict:
    """
    Call NVIDIA with automatic retry on transient failures.
    Retries once (2s delay) on: timeout, connection error, HTTP 429/5xx.
    """
    for attempt in range(retries + 1):
        try:
            return _call_nvidia(messages, model, api_key, url, max_tokens, timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < retries:
                log.info(f"  [AI] NVIDIA transient error — retry {attempt+1}/{retries} in 2s...")
                time.sleep(2)
                continue
            raise
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            # Retry on known transient codes OR unknown errors (status=0 means no response)
            if (status in _RETRYABLE_HTTP or status == 0) and attempt < retries:
                log.info(f"  [AI] NVIDIA HTTP {status} — retry {attempt+1}/{retries} in 2s...")
                time.sleep(2)
                continue
            raise


def _call_gemini(messages: list, model: str, api_key: str, url: str,
                 max_tokens: int, temperature: float, timeout: int) -> dict:
    """Call Google Gemini via OpenAI-compatible endpoint."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ── Public API ────────────────────────────────────────────────────────────────

def ask_gemini(
    messages: list,
    max_tokens: int = 400,
    temperature: float = 0.3,
    label: str = "",
) -> dict | None:
    """
    Send a chat request through the multi-tier AI chain:
      T1: NVIDIA Llama 3.3 70B (primary)
      T2: Gemini Pro (fallback)
      T3: Gemini Flash (emergency)

    Uses the OpenAI-compatible message format so callers don't need to change.

    Args:
        messages:    OpenAI-format list [{"role": ..., "content": ...}, ...]
        max_tokens:  Max response tokens (default 400)
        temperature: Sampling temperature (default 0.3)
        label:       Short label for log lines (e.g. "XAUUSD" or "exit-check")

    Returns:
        Parsed dict on success (e.g. {"direction": "BUY", "confidence": 0.72, "reason": "..."})
        None if all tiers fail.
    """
    if not _TIERS:
        log.error("[AI] ERROR — No AI API keys configured! Set NVIDIA_API_KEY or GEMINI_API_KEY in .env")
        return None

    for i, (provider, url, api_key, model, timeout, tier) in enumerate(_TIERS):
        try:
            log.info(f"  [AI] Trying {tier} ({model})...")

            if provider == "nvidia":
                resp_json = _call_nvidia_with_retry(messages, model, api_key, url, max_tokens, timeout)
            else:
                resp_json = _call_gemini(messages, model, api_key, url, max_tokens, temperature, timeout)

            msg = resp_json["choices"][0]["message"]
            raw = (msg.get("content") or "").strip()
            if not raw:
                raise ValueError("Empty content — model may be in think/safety mode")
            data = _extract_json(raw)

            if not isinstance(data, dict):
                raise ValueError(f"Parsed result is not a dict: {data!r}")

            used = resp_json.get("model", model)
            log.info(_log_line(data, tier, used, label))
            return data

        except requests.exceptions.Timeout:
            msg = f"timed out after {timeout}s"
        except requests.exceptions.ConnectionError as e:
            msg = f"connection error: {str(e)[:60]}"
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            body = e.response.text[:200] if e.response else ""
            msg = f"HTTP {status}: {body}"
        except (json.JSONDecodeError, ValueError) as e:
            msg = f"JSON parse error — {str(e)[:60]}"
        except Exception as e:
            msg = str(e)[:70]

        is_last = (i == len(_TIERS) - 1)
        suffix = " — all tiers exhausted" if is_last else f" — trying {_TIERS[i+1][5]}..."
        log.warning(f"  [AI] {tier}/{model} failed ({msg}){suffix}")

    return None


# ── Alias for backward compat ─────────────────────────────────────────────────
# analyzer.py imports both ask_gemini and ask_openrouter
ask_openrouter = ask_gemini
