"""
Fetch historical OHLCV from Yahoo Finance and write CSVs in the schema
the bot's analyzer expects (time, o, h, l, c, vol).

We pull both raw 15m candles AND 5m candles resampled to 15m so we can
stitch a longer history if needed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent

# Map our display symbol → best Yahoo proxy
PROXY = {
    "XAUUSD": "GC=F",   # Gold futures continuous (most volume)
    "XAGUSD": "SI=F",   # Silver futures continuous
}


def normalise(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Yahoo returns MultiIndex columns when a single ticker is given.
    Flatten and rename to the bot's schema."""
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    # First col might be 'Datetime' or 'Date'
    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    out = pd.DataFrame({
        "time": pd.to_datetime(df[time_col], utc=True),
        "o":    df["Open"].astype(float),
        "h":    df["High"].astype(float),
        "l":    df["Low"].astype(float),
        "c":    df["Close"].astype(float),
        "vol":  df["Volume"].fillna(0).astype(float),
    })
    out = out.dropna(subset=["o", "h", "l", "c"]).sort_values("time").reset_index(drop=True)
    return out


def fetch(symbol: str, interval: str, period: str) -> pd.DataFrame:
    proxy = PROXY[symbol]
    print(f"  Fetching {symbol} ← {proxy}  interval={interval} period={period}")
    df = yf.download(proxy, period=period, interval=interval,
                     progress=False, auto_adjust=False)
    return normalise(df, symbol)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True, choices=list(PROXY.keys()))
    p.add_argument("--interval", default="15m")
    p.add_argument("--period", default="60d")
    args = p.parse_args()

    df = fetch(args.symbol, args.interval, args.period)
    if df.empty:
        print("  ❌ Empty result — check symbol/interval/period", file=sys.stderr)
        sys.exit(1)

    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"history_{args.symbol}_{args.interval}.csv"
    df.to_csv(out_path, index=False)
    print(f"  ✅ Saved {len(df)} rows  →  {out_path}")
    print(f"     Range: {df['time'].iloc[0]}  →  {df['time'].iloc[-1]}")
    print(f"     Span: {(df['time'].iloc[-1] - df['time'].iloc[0]).days} days")


if __name__ == "__main__":
    main()
