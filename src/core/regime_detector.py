import numpy as np
import pandas as pd
from core.logger_factory import get_logger

log = get_logger("regime")

class RegimeDetector:
    """
    Analyzes multi-timeframe price action, volatility, and trend strength
    to classify the current market regime.
    """

    @staticmethod
    def detect(m15_data: dict, h1_data: dict, h4_data: dict) -> dict:
        """
        Determine the market regime using ADX, ATR, and EMA alignment.
        Returns a dictionary with the regime classification and specific trading parameters.
        """
        try:
            adx = m15_data.get('adx', 0)
            plus_di = m15_data.get('plus_di', 0)
            minus_di = m15_data.get('minus_di', 0)
            atr = m15_data.get('atr', 0)
            bb_squeeze = m15_data.get('bb_squeeze', False)
            
            # Use H1 ATR relative to M15 ATR if available to gauge volatility expansion
            # For simplicity, we just look at absolute ADX and DI spreads for now.
            
            is_bullish = plus_di > minus_di
            trend_dir = "UP" if is_bullish else "DOWN"

            # 1. Detect Squeeze / Volatility Contraction
            if bb_squeeze:
                regime = f"SQUEEZE_{trend_dir}"
                volatility = "COMPRESSED"
                strategy = "BREAKOUT"
            
            # 2. Detect Strong Trends
            elif adx >= 25:
                regime = f"STRONG_TREND_{trend_dir}"
                volatility = "EXPANDING"
                strategy = "TREND_FOLLOWING"
                
            # 3. Detect Weak/Developing Trends
            elif adx >= 18:
                regime = f"WEAK_TREND_{trend_dir}"
                volatility = "NORMAL"
                strategy = "TREND_FOLLOWING_CAUTIOUS"
                
            # 4. Detect Ranging / Chop
            else:
                # Distinguish between tight chop and wide volatile range
                # A wide range might be tradable mean-reversion, tight chop is skip
                # We approximate by looking at DI spread. If +DI and -DI are both low, it's tight.
                if max(plus_di, minus_di) < 20:
                    regime = "RANGING_CHOP"
                    volatility = "LOW"
                    strategy = "MEAN_REVERSION_STRICT"
                else:
                    regime = "RANGING_VOLATILE"
                    volatility = "HIGH"
                    strategy = "MEAN_REVERSION"

            return {
                "regime_label": regime,
                "volatility": volatility,
                "recommended_strategy": strategy,
                "adx": round(adx, 1),
                "is_trending": adx >= 18
            }

        except Exception as e:
            log.error(f"[REGIME] Error detecting regime: {e}")
            return {
                "regime_label": "UNKNOWN",
                "volatility": "UNKNOWN",
                "recommended_strategy": "CAUTIOUS",
                "adx": 0,
                "is_trending": False
            }
