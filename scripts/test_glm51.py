#!/usr/bin/env python3
"""
Quick connectivity test for NVIDIA NIM GLM-5.1.
Verifies the model works with the existing NVIDIA_API_KEY.
"""
import os
import sys
import time
import json
import requests

# Load .env manually (no dotenv dependency)
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

API_KEY = os.getenv("NVIDIA_API_KEY", "")
if not API_KEY:
    print("❌ NVIDIA_API_KEY not set in .env")
    sys.exit(1)

# ── GLM 5.1 endpoint (OpenAI-compatible via NVIDIA NIM) ──
BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "z-ai/glm-5.1"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
}

# Simple test prompt — mirrors trading analysis format
payload = {
    "model": MODEL,
    "messages": [
        {
            "role": "system",
            "content": "You are a trading analysis assistant. Respond with JSON only."
        },
        {
            "role": "user",
            "content": (
                "Test message. Respond with this exact JSON:\n"
                '{"status": "ok", "model": "glm-5.1", "message": "API connection successful"}'
            ),
        },
    ],
    "max_tokens": 256,
    "temperature": 0.3,
    "top_p": 0.95,
    "stream": False,  # non-streaming for reliable JSON extraction
}

print(f"🔗 Testing GLM 5.1 at {BASE_URL}")
print(f"   Model: {MODEL}")
print(f"   API Key: {API_KEY[:12]}...{API_KEY[-4:]}")
print()

t0 = time.time()
try:
    resp = requests.post(BASE_URL, headers=headers, json=payload, timeout=90)
    elapsed = time.time() - t0

    print(f"📡 HTTP {resp.status_code} — {elapsed:.1f}s")

    if resp.status_code == 200:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        model_used = data.get("model", "?")
        usage = data.get("usage", {})

        print(f"✅ SUCCESS!")
        print(f"   Model returned: {model_used}")
        print(f"   Tokens: prompt={usage.get('prompt_tokens', '?')} completion={usage.get('completion_tokens', '?')}")
        print(f"   Latency: {elapsed:.1f}s")
        print(f"   Response: {content[:500]}")
    else:
        print(f"❌ FAILED — HTTP {resp.status_code}")
        print(f"   Body: {resp.text[:500]}")

except requests.exceptions.Timeout:
    print(f"❌ TIMEOUT after {time.time() - t0:.1f}s")
except requests.exceptions.ConnectionError as e:
    print(f"❌ CONNECTION ERROR: {e}")
except Exception as e:
    print(f"❌ ERROR: {e}")
