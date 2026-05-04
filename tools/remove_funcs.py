import re
from pathlib import Path

p = Path("src/continuous_trader.py")
content = p.read_text()

# Remove def utcnow()
content = re.sub(r'def utcnow\(\) -> str:\n.*?return.*?\n\n\n', '', content, flags=re.DOTALL)

# Remove def sanitize(obj):
content = re.sub(r'def sanitize\(obj\):\n.*?(?=\n_HERMES_DIR)', '', content, flags=re.DOTALL)

# Remove def fmt_profit
content = re.sub(r'def fmt_profit\(p: float\) -> str:\n.*?(?=\n\ndef compact_text)', '', content, flags=re.DOTALL)

# Remove def compact_text
content = re.sub(r'def compact_text\(text: str, limit: int = 88\) -> str:\n.*?(?=\n\ndef adx_regime)', '', content, flags=re.DOTALL)

# Remove def adx_regime
content = re.sub(r'def adx_regime\(adx: float\) -> str:\n.*?(?=\n\ndef format_factor_summary)', '', content, flags=re.DOTALL)

# Remove def format_factor_summary
content = re.sub(r'def format_factor_summary\(factor_scores: dict\) -> str:\n.*?(?=\n\n# ── Bridge factory)', '', content, flags=re.DOTALL)

p.write_text(content)
print("Removed duplicate functions")
