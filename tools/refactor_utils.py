import re
from pathlib import Path

# Files to update
TARGETS = [
    "src/continuous_trader.py",
]

for fp in TARGETS:
    p = Path(fp)
    if not p.exists(): continue
    content = p.read_text()
    
    # Replace usages
    content = re.sub(r'\b_sanitize\b', 'sanitize', content)
    content = re.sub(r'\b_utcnow\b', 'utcnow', content)
    content = re.sub(r'\b_compact_text\b', 'compact_text', content)
    content = re.sub(r'\b_adx_regime\b', 'adx_regime', content)
    content = re.sub(r'\b_format_factor_summary\b', 'format_factor_summary', content)
    
    # Add import near the top, after other core imports
    if "from core.utils import" not in content:
        content = content.replace(
            "from core.paths import ROOT_DIR, CONFIG_DIR, STATE_DIR, LOG_DIR, DATA_DIR",
            "from core.paths import ROOT_DIR, CONFIG_DIR, STATE_DIR, LOG_DIR, DATA_DIR\nfrom core.utils import sanitize, fmt_profit, compact_text, adx_regime, format_factor_summary, utcnow"
        )
    
    p.write_text(content)

print("Done refactoring utils usages.")
