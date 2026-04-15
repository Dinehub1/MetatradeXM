#!/usr/bin/env python3
# Add NVIDIA fallback to analyzer.py

import re

with open("/home/ubuntu/trading-bot/analyzer.py", "r") as f:
    content = f.read()

# Find where to add: after "resp.raise_for_status()"
# and also need to initialize data = None earlier

# First, add data = None before the for loop
content = content.replace(
    "for attempt in range(2):   # 1 retry on timeout",
    "data = None\n        for attempt in range(2):   # 1 retry on timeout",
)

# Add NVIDIA fallback after the successful Ollama parse
# Find "data = json.loads(text)" and add NVIDIA after it
old_parse = "                data = json.loads(text)"
new_parse = """                data = json.loads(text)
                
                # NVIDIA fallback layer
                if not data:
                    try:
                        import requests as nr
                        r = nr.post(
                            "https://integrate.api.nvidia.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY', '')}"},
                            json={"model": "nvidia/nemotron-3-super-120b-a12b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
                            timeout=25
                        )
                        r.raise_for_status()
                        txt = r.json()["choices"][0]["message"]["content"].strip()
                        m = re.search(r'\{.*?"direction".*?"confidence".*?"reason".*?\}', txt)
                        if m:
                            data = json.loads(m.group())
                            print("  NVIDIA fallback OK")
                    except Exception as ne:
                        print("  NVIDIA fail:", str(ne)[:50])"""

content = content.replace(old_parse, new_parse)

with open("/home/ubuntu/trading-bot/analyzer.py", "w") as f:
    f.write(content)

print("Done - NVIDIA fallback added")
