-- Add event_type column to trade_outcomes
-- This column tracks whether outcomes are PRIMARY (linked to entry) or GHOST (orphaned)

ALTER TABLE trade_outcomes ADD COLUMN IF NOT EXISTS event_type TEXT DEFAULT 'PRIMARY';

-- Create index for event_type queries
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_event_type ON trade_outcomes(event_type);
