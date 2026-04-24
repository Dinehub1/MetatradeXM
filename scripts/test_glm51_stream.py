#!/usr/bin/env python3
"""
Test GLM 5.1 with STREAMING to avoid timeout.
Streaming keeps the connection alive while the model thinks.
"""
import os
import sys
import time
import json

# Load .env
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

import requests

API_KEY = os.getenv("NVIDIA_API_KEY", "")
BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "z-ai/glm-5.1"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
}

# Test with a simple prompt first
payload = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": "You are a concise trading analyst. Respond with JSON only."},
        {"role": "user", "content": 'Say {"status":"ok","model":"glm-5.1"} — nothing else.'},
    ],
    "max_tokens": 128,
    "temperature": 0.2,
    "stream": True,  # ← KEY: streaming avoids server-side timeout
}

print(f"🔗 Testing GLM 5.1 (STREAMING mode)")
print(f"   Model: {MODEL}")
print()

t0 = time.time()
full_content = ""
first_token_time = None

try:
    # Use a long read timeout but short connect timeout
    resp = requests.post(
        BASE_URL,
        headers=headers,
        json=payload,
        timeout=(15, 300),  # (connect_timeout, read_timeout)
        stream=True,
    )

    if resp.status_code != 200:
        print(f"❌ HTTP {resp.status_code}")
        print(f"   Body: {resp.text[:500]}")
        sys.exit(1)

    print(f"📡 Connected in {time.time() - t0:.1f}s — streaming tokens...")

    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                if first_token_time is None:
                    first_token_time = time.time() - t0
                full_content += content
                print(content, end="", flush=True)
        except json.JSONDecodeError:
            continue

    elapsed = time.time() - t0
    print()
    print()
    print(f"✅ SUCCESS!")
    print(f"   Total time: {elapsed:.1f}s")
    print(f"   Time to first token: {first_token_time:.1f}s" if first_token_time else "   No tokens received")
    print(f"   Full response: {full_content}")

except requests.exceptions.Timeout:
    print(f"❌ TIMEOUT after {time.time() - t0:.1f}s")
except requests.exceptions.ConnectionError as e:
    print(f"❌ CONNECTION ERROR: {e}")
except Exception as e:
    print(f"❌ ERROR: {type(e).__name__}: {e}")
