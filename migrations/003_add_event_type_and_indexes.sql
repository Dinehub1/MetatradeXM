-- Migration 003: Add event_type column and performance indexes
-- Run this in Supabase Dashboard → SQL Editor

-- Add event_type to track PRIMARY vs GHOST outcomes
ALTER TABLE trade_outcomes ADD COLUMN IF NOT EXISTS event_type TEXT DEFAULT 'PRIMARY';

-- Update all existing records to PRIMARY (they are real closed trades)
UPDATE trade_outcomes SET event_type = 'PRIMARY' WHERE event_type IS NULL;

-- Add index for fast filtering by event_type
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_event_type ON trade_outcomes(event_type);

-- Add composite index for common AI queries (symbol + outcome)
CREATE INDEX IF NOT EXISTS idx_outcomes_symbol_outcome ON trade_outcomes(symbol, outcome);

-- Add index for win-rate queries by direction
CREATE INDEX IF NOT EXISTS idx_outcomes_symbol_direction ON trade_outcomes(symbol, direction);
