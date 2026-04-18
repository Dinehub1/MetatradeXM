"""
ai_client.py — Unified OpenRouter AI client for MetatradeXM.

Single source of truth for ALL AI calls in the system.
- Two-tier fallback: T1 (primary fast model) → T2 (reliable backup)
- Robust JSON extraction: handles truncated output, nested objects, thinking tags
- Configurable via .env

Timeout config (set in .env):
    OPENROUTER_TIER1_TIMEOUT = 45   # seconds (thinking models need 30-40s)
    OPENROUTER_TIER2_TIMEOUT = 45   # seconds (Llama 3.3 70B is fast but give headroom)

Model config (set in .env):
    OPENROUTER_MODEL       = google/gemini-2.5-flash          ← T1 primary
    OPENROUTER_FAST_MODEL  = meta-llama/llama-3.3-70b-instruct ← T2 fallback
"""
from __future__ import annotations

import os
import re
import json
import requests

# ── OpenRouter endpoint ───────────────────────────────────────────────────────
_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_HEADERS_BASE = {
    "Content-Type":  "application/json",
    "HTTP-Referer":  "https://metatradexm.bot",
    "X-Title":       "MetatradeXM",
}

# ── Tier config (read once at import time, respects .env loaded by caller) ────
_T1_MODEL   = os.getenv("OPENROUTER_MODEL",      "google/gemini-2.5-flash")
_T1_TIMEOUT = int(os.getenv("OPENROUTER_TIER1_TIMEOUT", "45"))

_T2_MODEL   = os.getenv("OPENROUTER_FAST_MODEL", "meta-llama/llama-3.3-70b-instruct")
_T2_TIMEOUT = int(os.getenv("OPENROUTER_TIER2_TIMEOUT", "45"))

_TIERS = [
    (_T1_MODEL, _T1_TIMEOUT, "T1"),
    (_T2_MODEL, _T2_TIMEOUT, "T2"),
]


# ── JSON extraction & repair helpers ─────────────────────────────────────────

def _strip_thinking(text: str) -> str:
    """
    Remove reasoning/thinking blocks that some models emit before the JSON.
    Handles: <think>...</think>, <thinking>...</thinking>, <thought>...</thought>,
    ◁think▷...◁/think▷, and leading prose before the first '{'.
    """
    # Remove XML-style thinking tags (greedy to handle multiple blocks)
    text = re.sub(r"<(?:think|thinking|thought)>.*?</(?:think|thinking|thought)>",
                  "", text, flags=re.DOTALL)
    # Remove ◁think▷ style tags (some models use these)
    text = re.sub(r"◁think▷.*?◁/think▷", "", text, flags=re.DOTALL)
    # Remove leading prose before first '{' or '[' (common when model explains first)
    first_brace = -1
    for i, ch in enumerate(text):
        if ch in '{[':
            first_brace = i
            break
    if first_brace > 0:
        # Only strip if there's clearly prose (no JSON structure) before the brace
        prefix = text[:first_brace].strip()
        if not any(k in prefix for k in ['"', ':', ',']):
            text = text[first_brace:]
    return text.strip()


def _extract_balanced_json(text: str) -> str | None:
    """
    Extract the first balanced JSON object from text using brace counting.
    Handles nested objects correctly (unlike the old [^{}]+ regex).
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
    # Unbalanced — return what we have (will try repair)
    return text[start:]


def _repair_truncated_json(text: str) -> dict | None:
    """
    Attempt to repair a truncated JSON object by closing unclosed strings
    and braces. This handles the common case where max_tokens cuts off the
    response mid-string (e.g. "reason": "The trend is...).
    """
    text = text.strip()
    if not text:
        return None

    # Try parsing as-is first
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy: find the last complete key-value pair and truncate there
    # Look for the last "," or "{" that's outside a string, then close from there
    depth = 0
    in_string = False
    escape = False
    last_safe_pos = 0  # position right after a comma or opening brace, outside strings

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

    # Build a repaired string: content up to last safe position + closing braces
    repaired = text[:last_safe_pos].rstrip(',').rstrip()

    # Close any unclosed strings
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

    # Close remaining open braces
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
    Handles: markdown fences, thinking tags, nested objects, and truncated output.
    """
    # Strip markdown fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    # Strip thinking/reasoning blocks
    text = _strip_thinking(text)

    # Try direct parse first
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Extract balanced JSON object (handles nested {})
    balanced = _extract_balanced_json(text)
    if balanced:
        try:
            result = json.loads(balanced)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            # Try to repair the truncated JSON
            repaired = _repair_truncated_json(balanced)
            if repaired is not None:
                return repaired

    # Last resort: try to repair whatever we have
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
    return f"  [AI] OpenRouter {tier} OK ({model}) — {tag}{summary}"


# ── Public API ────────────────────────────────────────────────────────────────

def ask_openrouter(
    messages: list,
    max_tokens: int = 400,
    temperature: float = 0.3,
    label: str = "",
) -> dict | None:
    """
    Send a chat request to OpenRouter with T1 → T2 automatic fallback.

    Args:
        messages:    OpenAI-format message list [{"role": ..., "content": ...}, ...]
        max_tokens:  Max response tokens (default 400 — enough for JSON with long reasons)
        temperature: Sampling temperature (default 0.3 — deterministic but not rigid)
        label:       Short label for log lines (e.g. "XAUUSD" or "exit-check")

    Returns:
        Parsed dict on success (e.g. {"direction": "BUY", "confidence": 0.72, "reason": "..."})
        None if both tiers fail.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        print("  [AI] ERROR — OPENROUTER_API_KEY not set in .env")
        return None

    headers = {**_HEADERS_BASE, "Authorization": f"Bearer {api_key}"}

    for model, timeout, tier in _TIERS:
        try:
            resp = requests.post(
                _API_URL,
                headers=headers,
                json={
                    "model":       model,
                    "messages":    messages,
                    "max_tokens":  max_tokens,
                    "temperature": temperature,
                },
                timeout=timeout,
            )
            resp.raise_for_status()

            raw  = resp.json()["choices"][0]["message"]["content"].strip()
            data = _extract_json(raw)

            # Sanity: result must be a non-empty dict
            if not isinstance(data, dict) or not data:
                raise ValueError(f"Parsed result is not a valid dict: {data!r}")

            used = resp.json().get("model", model)
            print(_log_line(data, tier, used, label))
            return data

        except requests.exceptions.Timeout:
            msg = f"timed out after {timeout}s"
        except requests.exceptions.HTTPError as e:
            msg = f"HTTP {e.response.status_code}" if e.response else str(e)
        except (json.JSONDecodeError, ValueError) as e:
            msg = f"JSON parse error — {str(e)[:60]}"
        except Exception as e:
            msg = str(e)[:70]

        suffix = " — trying T2..." if tier == "T1" else " — all tiers exhausted"
        print(f"  [AI] OpenRouter {tier}/{model} failed ({msg}){suffix}")

    return None


# ── Alias ─────────────────────────────────────────────────────────────────────
# analyzer.py imports ask_gemini by name.  OpenRouter routes to Gemini 2.5 Flash
# as T1, so this alias is semantically correct — Gemini IS the primary backend.
ask_gemini = ask_openrouter
