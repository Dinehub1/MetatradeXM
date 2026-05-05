-- Migration 004: Supabase live dashboard source tables
-- Run this in Supabase Dashboard -> SQL Editor after migrations 001-003.

CREATE TABLE IF NOT EXISTS live_market_snapshots (
    symbol TEXT PRIMARY KEY,
    broker_symbol TEXT,
    ts TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    price NUMERIC(20, 10),
    bid NUMERIC(20, 10),
    ask NUMERIC(20, 10),
    spread NUMERIC(20, 6),
    digits INTEGER,
    atr NUMERIC(20, 10),
    daily_high NUMERIC(20, 10),
    daily_low NUMERIC(20, 10),
    signal TEXT,
    confidence NUMERIC(6, 5),
    score NUMERIC(10, 4),
    session TEXT,
    indicators_json JSONB DEFAULT '{}'::jsonb,
    timeframes_json JSONB DEFAULT '{}'::jsonb,
    raw_json JSONB DEFAULT '{}'::jsonb,
    source TEXT DEFAULT 'bot',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS live_account_snapshots (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    ts TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    balance NUMERIC(20, 4),
    equity NUMERIC(20, 4),
    margin NUMERIC(20, 4),
    margin_free NUMERIC(20, 4),
    currency TEXT DEFAULT 'USD',
    leverage INTEGER,
    raw_json JSONB DEFAULT '{}'::jsonb,
    source TEXT DEFAULT 'bot',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS live_positions (
    ticket TEXT PRIMARY KEY,
    symbol TEXT,
    direction TEXT,
    volume NUMERIC(20, 6),
    entry_price NUMERIC(20, 10),
    current_price NUMERIC(20, 10),
    profit_loss_usd NUMERIC(20, 4),
    profit_loss_pct NUMERIC(20, 6),
    sl NUMERIC(20, 10),
    tp NUMERIC(20, 10),
    entry_time TIMESTAMP WITH TIME ZONE,
    status TEXT DEFAULT 'OPEN',
    raw_json JSONB DEFAULT '{}'::jsonb,
    source TEXT DEFAULT 'bot',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS live_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    event_type TEXT NOT NULL,
    source TEXT DEFAULT 'bot',
    symbol TEXT,
    severity TEXT DEFAULT 'INFO',
    payload JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_live_market_updated ON live_market_snapshots(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_live_positions_updated ON live_positions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_live_events_ts ON live_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_live_events_type ON live_events(event_type);

ALTER TABLE live_market_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_account_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE live_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all access" ON live_market_snapshots FOR ALL USING (true);
CREATE POLICY "Allow all access" ON live_account_snapshots FOR ALL USING (true);
CREATE POLICY "Allow all access" ON live_positions FOR ALL USING (true);
CREATE POLICY "Allow all access" ON live_events FOR ALL USING (true);
