import pandas as pd
import sqlite3
import re
from datetime import datetime
import json
import os
import sys

# Add root dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.paths import DATA_DIR, STATE_DIR

def parse_confidence(comment):
    match = re.search(r'c(\d+)%', str(comment))
    if match:
        return float(match.group(1)) / 100.0
    return 0.0

def main():
    history_file = "/Users/mac/Documents/MetatradeXM/historty/webhook_history.csv"
    if not os.path.exists(history_file):
        print(f"File not found: {history_file}")
        return

    df = pd.read_csv(history_file)
    
    # We want to match entries (entry=0) with exits (entry=1) by position_id
    entries = df[df['entry'] == 0]
    exits = df[df['entry'] == 1]
    
    db_path = DATA_DIR / "trade_memory.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    new_outcomes = 0
    
    for _, exit_row in exits.iterrows():
        pos_id = exit_row['position_id']
        if pos_id == 0:
            continue # not a trade (like deposit)
            
        entry_row = entries[entries['position_id'] == pos_id]
        if entry_row.empty:
            # We don't have the entry for this exit in the CSV. Maybe skip or just use exit row
            continue
            
        entry_row = entry_row.iloc[0]
        
        # Check if already in trade_outcomes
        cursor.execute("SELECT id FROM trade_outcomes WHERE ticket=?", (str(pos_id),))
        if cursor.fetchone():
            continue
            
        symbol = exit_row['symbol']
        direction = "SELL" if entry_row['type'] == 1 else "BUY"
        entry_price = entry_row['price']
        exit_price = exit_row['price']
        profit_usd = exit_row['profit'] + exit_row['swap'] + exit_row['commission'] + exit_row['fee']
        
        # Calculate pips approx. 
        # For gold, pip is 0.1, contract size 100
        if "GOLD" in str(symbol):
            pip = 0.1
            contract = 100
        else:
            pip = 0.01
            contract = 5000
            
        volume = exit_row['volume']
        pip_val = pip * contract * volume
        pips_result = profit_usd / pip_val if pip_val > 0 else 0
        
        confidence = parse_confidence(entry_row['comment'])
        outcome = "WIN" if profit_usd > 0 else "LOSS"
        
        duration_min = (exit_row['time'] - entry_row['time']) / 60.0
        
        cursor.execute("""
            INSERT INTO trade_outcomes 
            (ts, ticket, symbol, direction, entry_price, exit_price, pips_result, confidence, duration_min, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            exit_row['time_str'],
            str(pos_id),
            str(symbol),
            direction,
            entry_price,
            exit_price,
            round(pips_result, 1),
            confidence,
            round(duration_min, 1),
            outcome
        ))
        new_outcomes += 1
        
    conn.commit()
    print(f"Inserted {new_outcomes} trade outcomes into memory.")
    
    # Also fix phantom positions in pyramid_manager state and trader_state.json
    try:
        pyramid_file = STATE_DIR / "pyramid_state.json"
        if pyramid_file.exists():
            pyramid = json.loads(pyramid_file.read_text())
            open_entries = entries[~entries['position_id'].isin(exits['position_id'])]
            open_tickets = set(open_entries['position_id'].astype(str).tolist())
            print(f"Open tickets in CSV: {open_tickets}")
            
            pyramid_file.write_text(json.dumps({}))
            print("Cleared pyramid_state.json to fix phantom positions.")
    except Exception as e:
        print("Error clearing pyramid_state:", e)
        
    try:
        trader_state = STATE_DIR / "trader_state.json"
        if trader_state.exists():
            state = json.loads(trader_state.read_text())
            cursor.execute("SELECT COUNT(*), SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END), SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) FROM trade_outcomes")
            row = cursor.fetchone()
            if row:
                state['total_trades'] = row[0] or 0
                state['wins'] = row[1] or 0
                state['losses'] = row[2] or 0
                trader_state.write_text(json.dumps(state, indent=2))
                print(f"Updated trader_state.json: {state}")
    except Exception as e:
        print("Error updating trader_state:", e)

    conn.close()

if __name__ == "__main__":
    main()
