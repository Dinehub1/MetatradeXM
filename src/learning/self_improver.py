"""
self_improver.py — Self-Improvement Engine

The brain that ties memory + skills together and continuously improves.
Runs daily reviews, adjusts scoring weights, improves/generates skills.
"""

import json
from core.logger_factory import get_logger
import time
from datetime import datetime, timezone
from pathlib import Path

from learning.memory import TradeMemory
from learning.skill_manager import SkillManager
from core.paths import CONFIG_DIR


log = get_logger("improver")

WEIGHTS_PATH     = CONFIG_DIR / "scoring_weights.json"
LAST_REVIEW_FILE = Path(__file__).parent / ".last_review_date"  # persists across restarts

# Minimum weight floor — prevent score collapse from compounding daily decays
WEIGHT_FLOOR = 0.85
WEIGHT_CEIL  = 1.20

# SAFETY: Weight adjustments are enabled, guarded by minimum sample sizes
# and bounded floor/ceiling values to prevent score collapse.
WEIGHT_ADJUSTMENT_ENABLED = True

# Explicit mapping from factor stat keys (analyzer) to weight keys (scoring_weights.json)
FACTOR_TO_WEIGHT = {
    "f1_h4_trend":      "f1_h4_trend",
    "f2_h1_trend":      "f2_h1_trend",
    "f3_rsi_zone":      "f3_rsi",
    "f4_macd_momentum": "f4_macd",
    "f5_adx_strength":  "f5_adx",
    "f6_stoch_confirm": "f6_stoch",
    "f7_bb_action":     "f7_bb",
    "f8_h1_rsi":        "f8_h1_rsi",
    "f9_h1_macd":       "f9_h1_macd",
}


class PerformanceAnalyzer:
    """Analyzes trading performance and drives self-improvement."""

    def __init__(self, memory: TradeMemory = None, skill_mgr: SkillManager = None):
        self.memory = memory or TradeMemory()
        self.skill_mgr = skill_mgr or SkillManager()
        self._last_review = None

    def should_run_review(self) -> bool:
        """Check if daily review should run — once per UTC day, persisted to disk.
        Uses a file (.last_review_date) so process restarts do NOT re-trigger the review."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Check persistent file FIRST — survives process restarts
        if LAST_REVIEW_FILE.exists():
            try:
                last = LAST_REVIEW_FILE.read_text().strip()
                if last == today:
                    return False
            except Exception as e:
                try: log.debug(f'Caught exception: {e}')
                except: pass
                pass

        # In-memory guard (secondary)
        if self._last_review == today:
            return False

        # Check if we have enough data
        outcomes = self.memory.get_recent_outcomes(hours=24)
        return len(outcomes) >= 3

    def daily_review(self):
        """
        Main self-improvement loop. Analyzes recent trades and adjusts strategy.
        Safe: max ±10% weight change per day to prevent overfitting.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._last_review = today
        # Persist to disk so restarting the process doesn't re-trigger today's review
        try:
            LAST_REVIEW_FILE.write_text(today)
        except Exception as e:
            try: log.debug(f'Caught exception: {e}')
            except: pass
            pass

        log.info("=" * 50)
        log.info("[SELF-IMPROVE] Starting daily performance review")

        outcomes = self.memory.get_recent_outcomes(hours=24)
        if len(outcomes) < 3:
            log.info("[SELF-IMPROVE] Not enough trades for review (need 3+)")
            return

        log.info(f"[SELF-IMPROVE] Analyzing {len(outcomes)} trades from last 24h")

        # 1. Compute factor effectiveness
        factor_stats = self.memory.get_factor_stats()
        self._log_factor_stats(factor_stats)

        # 2. Detect patterns
        patterns = self._detect_patterns(outcomes)
        for p in patterns:
            log.info(f"[PATTERN] {p['description']} (confidence: {p['confidence']:.0%})")

        # 3. Adjust scoring weights
        adjustments = self._compute_weight_adjustments(factor_stats, outcomes)
        if adjustments:
            self._apply_weight_adjustments(adjustments)

        # 4. Improve existing skills
        all_outcomes = self.memory.get_all_outcomes(limit=50)
        _backoff = 2  # seconds between API calls to avoid rate-limiting
        for skill_info in self.skill_mgr.list_skills():
            if skill_info["total_trades"] >= 10:
                try:
                    self.skill_mgr.improve_skill(skill_info["name"], all_outcomes)
                    _backoff = 2  # reset on success
                except Exception as e:
                    log.warning(f"[SELF-IMPROVE] Skill '{skill_info['name']}' improve failed: {e}")
                    _backoff = min(_backoff * 2, 30)  # exponential backoff, cap 30s
                time.sleep(_backoff)  # throttle API bursts

        # 5. Auto-generate skills from strong patterns
        for pattern in patterns:
            if pattern["confidence"] >= 0.75 and pattern["sample_size"] >= 5:
                name = self.skill_mgr.auto_generate_skill(pattern)
                if name:
                    self.memory.log_learning(
                        "AUTO_SKILL",
                        f"Generated skill '{name}' from pattern: {pattern['description']}",
                        data=pattern,
                        applied=True
                    )

        log.info("[SELF-IMPROVE] Daily review complete")
        log.info("=" * 50)

    def _log_factor_stats(self, stats: dict):
        """Log factor effectiveness for monitoring."""
        if not stats:
            log.info("[FACTORS] No factor data yet")
            return

        log.info("[FACTORS] Factor effectiveness:")
        for name, data in sorted(stats.items()):
            if data['sample_size'] >= 3:
                log.info(f"  {name}: WR={data['win_rate']:.0%} "
                         f"win_avg={data['avg_when_win']:+.1f} "
                         f"loss_avg={data['avg_when_loss']:+.1f} "
                         f"(n={data['sample_size']})")

    def _detect_patterns(self, outcomes: list) -> list:
        """Detect recurring profitable/losing patterns in trade outcomes."""
        patterns = []

        # Pattern 1: Session-based performance
        by_session = {}
        for o in outcomes:
            try:
                conditions = json.loads(o.get("conditions_json", "{}"))
                session = conditions.get("session", "UNKNOWN")
                by_session.setdefault(session, []).append(o)
            except (json.JSONDecodeError, ValueError):
                continue

        for session, trades in by_session.items():
            if len(trades) >= 3:
                wins = sum(1 for t in trades if t.get("outcome") == "WIN")
                wr = wins / len(trades)
                avg_pips = sum(t.get("pips_result") or 0 for t in trades) / len(trades)
                if wr >= 0.7 or wr <= 0.3:
                    patterns.append({
                        "type": "session_bias",
                        "description": f"{'Profitable' if wr >= 0.7 else 'Losing'} "
                                       f"in {session} session (WR: {wr:.0%}, avg: {avg_pips:+.1f}pips)",
                        "confidence": wr if wr >= 0.7 else (1 - wr),
                        "sample_size": len(trades),
                        "session": session,
                        "win_rate": wr,
                    })

        # Pattern 2: Direction-based performance
        by_dir = {}
        for o in outcomes:
            d = o.get("direction", "UNKNOWN")
            by_dir.setdefault(d, []).append(o)

        for direction, trades in by_dir.items():
            if len(trades) >= 3:
                wins = sum(1 for t in trades if t.get("outcome") == "WIN")
                wr = wins / len(trades)
                if wr >= 0.7:
                    patterns.append({
                        "type": "direction_bias",
                        "description": f"{direction} trades winning {wr:.0%} of the time",
                        "confidence": wr,
                        "sample_size": len(trades),
                        "direction": direction,
                        "win_rate": wr,
                    })

        # Pattern 3: Confidence-calibration check
        high_conf = [o for o in outcomes if (o.get("confidence") or 0) >= 0.70]
        low_conf = [o for o in outcomes if 0.55 <= (o.get("confidence") or 0) < 0.70]

        if len(high_conf) >= 3:
            hc_wr = sum(1 for o in high_conf if o.get("outcome") == "WIN") / len(high_conf)
            patterns.append({
                "type": "confidence_calibration",
                "description": f"High-confidence (>=70%) trades: WR={hc_wr:.0%} (n={len(high_conf)})",
                "confidence": 0.8,
                "sample_size": len(high_conf),
                "win_rate": hc_wr,
            })

        if len(low_conf) >= 3:
            lc_wr = sum(1 for o in low_conf if o.get("outcome") == "WIN") / len(low_conf)
            patterns.append({
                "type": "confidence_calibration",
                "description": f"Low-confidence (55-70%) trades: WR={lc_wr:.0%} (n={len(low_conf)})",
                "confidence": 0.8,
                "sample_size": len(low_conf),
                "win_rate": lc_wr,
            })

        return patterns

    def _compute_weight_adjustments(self, factor_stats: dict, outcomes: list) -> dict:
        """Compute weight adjustments based on factor effectiveness."""
        if not WEIGHT_ADJUSTMENT_ENABLED:
            log.info("[SELF-IMPROVE] Weight adjustments DISABLED - skipping")
            return {}
        adjustments = {}

        # 1. Calculate baseline win rate to contextualize factor performance
        total_wins = sum(1 for o in outcomes if (o.get("pips_result") or 0) > 0)
        baseline_wr = total_wins / len(outcomes) if outcomes else 0.5

        for factor_name, stats in factor_stats.items():
            # GUARDRAIL: Require statistically significant sample size
            if stats['sample_size'] < 15:
                continue

            # 2. Dynamic thresholding based on baseline
            factor_wr = stats['win_rate']
            
            # GUARDRAIL: Only adjust if the factor meaningfully deviates from baseline
            if factor_wr >= max(0.60, baseline_wr + 0.10):
                adjustments[factor_name] = 1.05  # Reward overperformance
            elif factor_wr <= min(0.40, baseline_wr - 0.10):
                adjustments[factor_name] = 0.95  # Penalize underperformance

        return adjustments

    def _apply_weight_adjustments(self, adjustments: dict):
        """
        Apply weight adjustments to scoring_weights.json.
        DISABLED by default. WEIGHT_ADJUSTMENT_ENABLED must be True.
        Manual changes only. Max 3% per adjustment, hard bounds 0.85-1.20.
        """
        try:
            if WEIGHTS_PATH.exists():
                weights = json.loads(WEIGHTS_PATH.read_text())
            else:
                weights = {}
        except (json.JSONDecodeError, IOError):
            weights = {}

        changes = []
        for factor_key, multiplier in adjustments.items():
            # Map factor stat names to weight keys using explicit mapping
            weight_key = FACTOR_TO_WEIGHT.get(factor_key, factor_key)

            if weight_key in weights:
                old = weights[weight_key]
                new = round(old * multiplier, 3)
                # Clamp: no single adjustment can move weight more than 3% relative to old value
                # Reduced from 10% to prevent oscillation and compounding drift
                max_delta = old * 0.03
                new = max(old - max_delta, min(old + max_delta, new))
                # Hard absolute bounds: floor at 0.85 prevents score collapse
                # from compounding daily decays; ceil at 1.20 prevents overweighting
                new = round(max(WEIGHT_FLOOR, min(WEIGHT_CEIL, new)), 3)

                if new != old:
                    weights[weight_key] = new
                    changes.append(f"{weight_key}: {old} -> {new}")

        if changes:
            weights["last_adjusted"] = datetime.now(timezone.utc).isoformat()
            history = weights.get("adjustment_history", [])
            history.append({
                "ts": weights["last_adjusted"],
                "changes": changes,
            })
            # Keep last 30 adjustments
            weights["adjustment_history"] = history[-30:]
            weights["version"] = int(weights.get("version", 1)) + 1

            WEIGHTS_PATH.write_text(json.dumps(weights, indent=4))
            log.info(f"[SELF-IMPROVE] Applied weight adjustments: {changes}")

            self.memory.log_learning(
                "WEIGHT_ADJUST",
                f"Adjusted {len(changes)} weights: {', '.join(changes)}",
                data={"adjustments": adjustments},
                applied=True
            )
        else:
            log.info("[SELF-IMPROVE] No weight adjustments needed")

    def generate_performance_report(self) -> str:
        """Generate a human-readable performance report."""
        outcomes = self.memory.get_all_outcomes(limit=100)
        if not outcomes:
            return "No trades recorded yet."

        total = len(outcomes)
        wins = sum(1 for o in outcomes if o.get("outcome") == "WIN")
        losses = sum(1 for o in outcomes if o.get("outcome") == "LOSS")
        total_pips = sum(o.get("pips_result") or 0 for o in outcomes)
        avg_pips = total_pips / total if total > 0 else 0

        report = [
            "=" * 50,
            "TRADING PERFORMANCE REPORT",
            "=" * 50,
            f"Total trades: {total} (W:{wins} L:{losses})",
            f"Win rate: {wins/total*100:.1f}%" if total > 0 else "Win rate: N/A",
            f"Total pips: {total_pips:+.1f}",
            f"Avg pips/trade: {avg_pips:+.1f}",
            "",
            "Factor effectiveness:",
        ]

        factor_stats = self.memory.get_factor_stats()
        for name, data in sorted(factor_stats.items()):
            if data['sample_size'] >= 3:
                report.append(f"  {name}: WR={data['win_rate']:.0%} (n={data['sample_size']})")

        report.append("")
        report.append("Skills performance:")
        for skill in self.skill_mgr.list_skills():
            report.append(f"  {skill['name']}: WR={skill['win_rate']}% ({skill['total_trades']} trades)")

        report.append("")
        report.append("Recent learning insights:")
        for ins in self.memory.get_recent_learning(limit=5):
            report.append(f"  [{ins['insight_type']}] {ins['insight_text']}")

        return "\n".join(report)
