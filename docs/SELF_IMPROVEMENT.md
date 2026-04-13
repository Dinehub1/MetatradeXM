# Self-Improvement System

MetatradeXM utilizes an autonomous self-improver loop to prevent the logic from falling out of sync with shifting market volatility structures. The loop runs constantly in the background.

## The Tri-Node Pipeline

### Node 1. `memory.py` 
Serves as the deterministic ledger. When a trade is closed, `continuous_trader.py` invokes the `memory.record_outcome(...)` function. It serializes the exact indicator variables and factor scores that justified the original entry and binds them to the P&L outcome.

### Node 2. `self_improver.py` 
Designed to act as a quantitative daily auditor.
- **Factor Effectiveness:** Maps historical trades. If trades matching high ADX (`f5_adx_strength`) had a 75% Win Rate over the last week, the self-improver proposes increasing its weight.
- If an indicator proves negatively correlated with winning, its weight multiplier in `scoring_weights.json` decreases dynamically. (Max ±10% change per day prevents radical logic corruption).

### Node 3. `skill_manager.py` (Hermes Pattern)
Operates higher level strategic logic decoupled from baseline indicators.
- **Skill.md**: Saves independent YAML markdown configurations (e.g., `volatility-regime`, `time-bias`). 
- If `self_improver` detects an emerging pattern (e.g. *Mondays at 8 AM consistently draw down*), it utilizes Ollama to auto-generate a new `Skill` that vetoes (blocks) entries resembling the newly identified adverse pattern.
