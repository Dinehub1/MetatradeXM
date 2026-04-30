"""
ai_client.py — Multi-tier AI client for MetatradeXM.

Single source of truth for ALL AI calls in the system.

Primary AI:
  T1 (NVIDIA Llama 3.3 70B) — primary, deep reasoning via NIM API
     Supports streaming + thinking tokens for best quality output.

Fallback chain (only if NVIDIA is down):
  T2 (gemini-2.5-pro)   — secondary fallback
  T3 (gemini-2.5-flash) — emergency fast fallback

Config (.env):
    NVIDIA_API_KEY     = nvapi-...
    INVOKE_URL         = https://integrate.api.nvidia.com/v1/chat/completions
    MODEL_NAME         = meta/llama-3.3-70b-instruct
    MAX_TOKENS         = 16384
    TEMPERATURE        = 1.00
    TOP_P              = 1.00
    STREAM             = true
    THINKING           = true
    GEMINI_API_KEY     = AIza...  (fallback only)
"""
from __future__ import annotations

import os
import re
import json
import time
import logging
import requests

log = logging.getLogger("ai_client")

# ── NVIDIA NIM (T1 — Primary, T2 — Backup key) ───────────────────────────────
_NVIDIA_KEY      = os.getenv("NVIDIA_API_KEY", "")
_NVIDIA_KEY_2    = os.getenv("NVIDIA_API_KEY_2", "")
_NVIDIA_URL      = os.getenv("INVOKE_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
_NVIDIA_MODEL    = os.getenv("MODEL_NAME", "meta/llama-3.3-70b-instruct")
_NVIDIA_TOKENS   = int(os.getenv("MAX_TOKENS", "16384"))
_NVIDIA_TEMP     = float(os.getenv("TEMPERATURE", "1.0"))
_NVIDIA_TOP_P    = float(os.getenv("TOP_P", "1.0"))
_NVIDIA_STREAM   = os.getenv("STREAM", "true").lower() == "true"
_NVIDIA_THINKING = os.getenv("THINKING", "true").lower() == "true"
_NVIDIA_TIMEOUT  = 90  # longer for thinking + streaming

# ── Google Gemini (T2/T3 — Fallback only) ─────────────────────────────────────
_GEMINI_BASE    = "https://generativelanguage.googleapis.com/v1beta/openai"
_GEMINI_URL     = f"{_GEMINI_BASE}/chat/completions"
_GEMINI_KEY     = os.getenv("GEMINI_API_KEY", "")
_T2_MODEL       = os.getenv("GEMINI_PRO_MODEL",   "gemini-2.5-pro")
_T3_MODEL       = os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash")
_GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT_S", "60"))

# ── Tier list ─────────────────────────────────────────────────────────────────
_TIERS = []

if _NVIDIA_KEY:
    _TIERS.append(("nvidia", _NVIDIA_URL, _NVIDIA_KEY, _NVIDIA_MODEL, _NVIDIA_TIMEOUT, "T1-NVIDIA"))

if _NVIDIA_KEY_2:
    _TIERS.append(("nvidia", _NVIDIA_URL, _NVIDIA_KEY_2, _NVIDIA_MODEL, _NVIDIA_TIMEOUT, "T2-NVIDIA-B"))

# Re-enable Gemini as T2/T3 fallback if configured (prevents single point of failure)
if _GEMINI_KEY:
    _TIERS.append(("gemini", _GEMINI_URL, _GEMINI_KEY, _T2_MODEL, _GEMINI_TIMEOUT, "T2-Gemini-Pro"))
    _TIERS.append(("gemini", _GEMINI_URL, _GEMINI_KEY, _T3_MODEL, max(30, _GEMINI_TIMEOUT // 2), "T3-Gemini-Flash"))
else:
    log.warning("[AI] No Gemini API key configured. Set GEMINI_API_KEY for fallback tier.")

def _validate_api_keys():
    """Validate API key configuration at startup. Fail fast if misconfigured."""
    if not _NVIDIA_KEY and not _NVIDIA_KEY_2:
        raise RuntimeError(
            "[AI] CRITICAL: No NVIDIA API keys configured. "
            "Set NVIDIA_API_KEY in .env. Cannot trade without AI confirmation."
        )
    # Validate key format: NVIDIA keys should start with 'nvapi-' and be long enough
    for key_name, key_val in [("NVIDIA_API_KEY", _NVIDIA_KEY), ("NVIDIA_API_KEY_2", _NVIDIA_KEY_2)]:
        if key_val and (not key_val.startswith("nvapi-") or len(key_val) < 20):
            raise RuntimeError(f"[AI] Invalid {key_name} format. NVIDIA keys must start with 'nvapi-'")


_validate_api_keys()

_tier_names = [t[5] for t in _TIERS]
if _tier_names:
    log.info(f"[AI] Active tiers: {' → '.join(_tier_names)}")
    if _NVIDIA_KEY:
        log.info(f"[AI] NVIDIA config: stream={_NVIDIA_STREAM} thinking={_NVIDIA_THINKING} "
                 f"tokens={_NVIDIA_TOKENS} temp={_NVIDIA_TEMP} top_p={_NVIDIA_TOP_P}")
else:
    log.error("[AI] CRITICAL: No AI tiers available! Validate API keys in .env")
    raise RuntimeError("No working AI tier configured")


# ── JSON extraction helpers ───────────────────────────────────────────────────

def _strip_thinking(text: str) -> str:
    """Remove reasoning/thinking blocks before JSON extraction."""
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
        repaired += '"'
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


def _validate_response_schema(data: dict) -> bool:
    """Validate that AI response has required fields for trading."""
    if not isinstance(data, dict):
        return False

    # Required fields for trading decision
    required_fields = {"direction", "confidence"}
    if not required_fields.issubset(data.keys()):
        missing = required_fields - set(data.keys())
        log.warning(f"[AI] Response missing required fields: {missing}")
        return False

    # Validate direction is one of the accepted values
    direction = data.get("direction", "").upper()
    if direction not in ("BUY", "SELL", "HOLD"):
        log.warning(f"[AI] Invalid direction: {direction} (must be BUY/SELL/HOLD)")
        return False

    # Validate confidence is a number between 0 and 1
    try:
        confidence = float(data.get("confidence", 0))
        if not (0 <= confidence <= 1):
            log.warning(f"[AI] Invalid confidence: {confidence} (must be 0-1)")
            return False
    except (TypeError, ValueError):
        log.warning(f"[AI] Confidence not a number: {data.get('confidence')}")
        return False

    return True


def _extract_json(text: str) -> dict:
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
            # Validate schema before returning
            if _validate_response_schema(result):
                return result
            else:
                raise json.JSONDecodeError("Invalid response schema", text, 0)
    except json.JSONDecodeError:
        pass
    balanced = _extract_balanced_json(text)
    if balanced:
        try:
            result = json.loads(balanced)
            if isinstance(result, dict):
                if _validate_response_schema(result):
                    return result
                else:
                    raise json.JSONDecodeError("Invalid response schema", text, 0)
        except json.JSONDecodeError:
            repaired = _repair_truncated_json(balanced)
            if repaired is not None and _validate_response_schema(repaired):
                return repaired
    repaired = _repair_truncated_json(text)
    if repaired is not None and _validate_response_schema(repaired):
        return repaired
    raise json.JSONDecodeError("Could not extract valid JSON with correct schema", text, 0)


def _log_line(data: dict, tier: str, model: str, label: str) -> str:
    tag = f"{label} | " if label else ""
    direction = data.get("direction", data.get("exit", "?"))
    conf = data.get("confidence")
    summary = f"{direction} {float(conf):.0%}" if conf is not None else str(direction)
    return f"AI-RESP | {tier} | {tag}{summary}"


_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


# ── NVIDIA call (streaming + thinking aware) ──────────────────────────────────

def _call_nvidia_stream(messages: list, model: str, api_key: str, url: str,
                        max_tokens: int, timeout: int) -> dict:
    """
    Call NVIDIA NIM with streaming enabled. Aggregates SSE chunks into a
    single response. Handles thinking tokens in reasoning_content field.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model":       model,
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": _NVIDIA_TEMP,
        "top_p":       _NVIDIA_TOP_P,
        "stream":      True,
    }

    resp = requests.post(url, headers=headers, json=payload,
                         timeout=timeout, stream=True)
    resp.raise_for_status()

    content_parts   = []
    reasoning_parts = []

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data: "):
            continue
        chunk_str = line[6:].strip()
        if chunk_str == "[DONE]":
            break
        try:
            chunk = json.loads(chunk_str)
        except json.JSONDecodeError:
            continue
        # Use `or [{}]` so an empty choices list (content-filter) doesn't IndexError
        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
        if delta.get("content"):
            content_parts.append(delta["content"])
        if delta.get("reasoning_content"):
            reasoning_parts.append(delta["reasoning_content"])

    # Prefer content; fall back to reasoning_content if model only streams that
    full_content = "".join(content_parts) or "".join(reasoning_parts)
    return {"choices": [{"message": {"content": full_content}, "model": model}]}


def _call_nvidia_sync(messages: list, model: str, api_key: str, url: str,
                      max_tokens: int, timeout: int) -> dict:
    """Non-streaming NVIDIA call."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model":       model,
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": _NVIDIA_TEMP,
        "top_p":       _NVIDIA_TOP_P,
        "stream":      False,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _call_nvidia_with_retry(messages: list, model: str, api_key: str, url: str,
                            max_tokens: int, timeout: int, retries: int = 3) -> dict:
    """Retry strategy: 3 attempts with exponential backoff (2s, 5s, 12s).
    NVIDIA NIM free tier rate-limits aggressively — needs space between calls."""
    caller = _call_nvidia_stream if _NVIDIA_STREAM else _call_nvidia_sync
    backoffs = [2, 5, 12]   # exponential
    for attempt in range(retries + 1):
        try:
            return caller(messages, model, api_key, url, max_tokens, timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < retries:
                wait = backoffs[min(attempt, len(backoffs)-1)]
                log.info(f"AI-RETRY | NVIDIA transient error | retry {attempt+1}/{retries} in {wait}s")
                time.sleep(wait)
                continue
            raise
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if (status in _RETRYABLE_HTTP or status == 0) and attempt < retries:
                wait = backoffs[min(attempt, len(backoffs)-1)]
                log.info(f"AI-RETRY | NVIDIA HTTP {status} | retry {attempt+1}/{retries} in {wait}s")
                time.sleep(wait)
                continue
            raise


def _call_gemini(messages: list, model: str, api_key: str, url: str,
                 max_tokens: int, temperature: float, timeout: int) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model":      model,
        "messages":   messages,
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
    Send a chat request through the AI chain.
    NVIDIA is primary (T1). Gemini is fallback only.

    Returns parsed dict on success, None if all tiers fail.
    """
    if not _TIERS:
        log.error("[AI] ERROR — No AI keys configured! Set NVIDIA_API_KEY in .env")
        return None

    # NVIDIA uses its own token/temp settings from .env
    nvidia_tokens = _NVIDIA_TOKENS

    for i, (provider, url, api_key, model, timeout, tier) in enumerate(_TIERS):
        try:
            log.info(f"AI-TRY | {tier} | model {model}")

            if provider == "nvidia":
                resp_json = _call_nvidia_with_retry(
                    messages, model, api_key, url, nvidia_tokens, timeout
                )
            else:
                resp_json = _call_gemini(
                    messages, model, api_key, url, max_tokens, temperature, timeout
                )

            msg = resp_json["choices"][0]["message"]
            raw = (msg.get("content") or "").strip()
            if not raw:
                raise ValueError("Empty content — model may be in think/safety mode")
            # Nemotron sometimes prepends a narrative before the JSON for non-trading
            # prompts (e.g., skills analysis with no data). Strip leading prose so
            # _extract_json finds the object reliably.
            if "{" in raw and not raw.lstrip().startswith("{"):
                raw = raw[raw.index("{"):]
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


# Alias — analyzer.py imports both names
ask_openrouter = ask_gemini
