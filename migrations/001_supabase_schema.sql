-- Supabase PostgreSQL Schema for MetatradeXM
-- Run this in Supabase SQL Editor to set up all tables

-- Trade Outcomes Table
CREATE TABLE IF NOT EXISTS trade_outcomes (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    ticket TEXT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price NUMERIC(20, 10),
    exit_price NUMERIC(20, 10),
    pips_result NUMERIC(20, 10),
    confidence NUMERIC(5, 4),
    factors_json JSONB,
    conditions_json JSONB,
    duration_min NUMERIC(20, 4),
    outcome TEXT,
    skills_used JSONB,
    event_type TEXT DEFAULT 'PRIMARY',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Trade Entries Table
CREATE TABLE IF NOT EXISTS trade_entries (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    ticket TEXT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price NUMERIC(20, 10),
    confidence NUMERIC(5, 4),
    factors_json JSONB,
    conditions_json JSONB,
    skills_used JSONB,
    closed INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Market Patterns Table
CREATE TABLE IF NOT EXISTS market_patterns (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    hour_utc INTEGER,
    day_of_week INTEGER,
    session TEXT,
    direction TEXT,
    outcome TEXT,
    pips NUMERIC(20, 10),
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Learning Log Table
CREATE TABLE IF NOT EXISTS learning_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    insight_type TEXT NOT NULL,
    insight_text TEXT NOT NULL,
    data_json JSONB,
    applied INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Filtered Trades Table
CREATE TABLE IF NOT EXISTS filtered_trades (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence NUMERIC(5, 4),
    filter_reasons JSONB,
    factors_json JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_symbol ON trade_outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_ts ON trade_outcomes(ts);
CREATE INDEX IF NOT EXISTS idx_trade_outcomes_ticket ON trade_outcomes(ticket);
CREATE INDEX IF NOT EXISTS idx_trade_entries_symbol ON trade_entries(symbol);
CREATE INDEX IF NOT EXISTS idx_trade_entries_ts ON trade_entries(ts);
CREATE INDEX IF NOT EXISTS idx_trade_entries_ticket ON trade_entries(ticket);
CREATE INDEX IF NOT EXISTS idx_market_patterns_symbol ON market_patterns(symbol);
CREATE INDEX IF NOT EXISTS idx_learning_log_ts ON learning_log(ts);
CREATE INDEX IF NOT EXISTS idx_filtered_trades_symbol ON filtered_trades(symbol);

-- Enable Row Level Security (RLS) - disable for now, enable later for multi-tenant
ALTER TABLE trade_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE trade_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_patterns ENABLE ROW LEVEL SECURITY;
ALTER TABLE learning_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE filtered_trades ENABLE ROW LEVEL SECURITY;

-- Create RLS policies that allow all (for single-tenant use)
CREATE POLICY "Allow all access" ON trade_outcomes FOR ALL USING (true);
CREATE POLICY "Allow all access" ON trade_entries FOR ALL USING (true);
CREATE POLICY "Allow all access" ON market_patterns FOR ALL USING (true);
CREATE POLICY "Allow all access" ON learning_log FOR ALL USING (true);
CREATE POLICY "Allow all access" ON filtered_trades FOR ALL USING (true);
