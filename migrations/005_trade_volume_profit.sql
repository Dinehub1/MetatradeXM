-- Migration 005: persist trade volume and realized USD profit for accurate history

ALTER TABLE trade_entries
    ADD COLUMN IF NOT EXISTS volume NUMERIC(20, 6),
    ADD COLUMN IF NOT EXISTS broker_symbol TEXT;

ALTER TABLE trade_outcomes
    ADD COLUMN IF NOT EXISTS volume NUMERIC(20, 6),
    ADD COLUMN IF NOT EXISTS profit_usd NUMERIC(20, 6),
    ADD COLUMN IF NOT EXISTS broker_symbol TEXT;

CREATE INDEX IF NOT EXISTS idx_trade_outcomes_profit_usd ON trade_outcomes(profit_usd);
CREATE INDEX IF NOT EXISTS idx_trade_entries_broker_symbol ON trade_entries(broker_symbol);
