import re
from pathlib import Path

files_to_clean = [
    "src/risk/position_scaler.py",
    "src/risk/pyramid_manager.py",
    "src/learning/self_improver.py",
    "src/risk/capital_manager.py",
    "src/learning/skill_manager.py",
    "src/learning/memory.py",
]

for fp in files_to_clean:
    p = Path(fp)
    if p.exists():
        content = p.read_text()
        # Find if __name__ == "__main__":
        new_content = re.sub(r'\nif __name__ == ["\']__main__["\']:\n.*', '\n', content, flags=re.DOTALL)
        if new_content != content:
            p.write_text(new_content)
            print(f"Cleaned {fp}")

