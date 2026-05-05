#!/usr/bin/env python3
"""
Manual trigger for self-improvement engine.
Run this to analyze recent trades and adjust scoring weights.

Usage:
  python3 scripts/trigger_self_improve.py
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

# Load .env BEFORE importing modules that validate API keys
from dotenv import load_dotenv
load_dotenv(ROOT_DIR / ".env")

from learning.self_improver import PerformanceAnalyzer
from learning.memory import TradeMemory
from learning.skill_manager import SkillManager
from core.logger_factory import get_logger

log = get_logger("manual_improve")

def main():
    print("=" * 80)
    print("SELF-IMPROVEMENT ENGINE — Manual Trigger")
    print("=" * 80)

    # Initialize
    memory = TradeMemory()
    skill_mgr = SkillManager()
    analyzer = PerformanceAnalyzer(memory, skill_mgr)

    # Run analysis
    print("\nGenerating performance report from last 100 trades...")
    print("-" * 80)
    report = analyzer.generate_performance_report()
    print(report)

    print("\n" + "=" * 80)
    print("✓ Performance analysis complete")
    print("=" * 80)
    print("\nTo adjust weights based on this analysis:")
    print("  1. Review factor effectiveness above")
    print("  2. Edit config/scoring_weights.json manually")
    print("  3. Restart the trader to apply changes")
    print("\nNote: Automatic weight adjustment has safeguards to prevent")
    print("excessive drift. Manual adjustments are recommended.")

if __name__ == "__main__":
    main()
