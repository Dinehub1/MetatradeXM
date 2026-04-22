import sqlite3
conn = sqlite3.connect('trades.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = c.fetchall()
print("Tables:", tables)
if tables:
    # Assume the first table is the trades table
    table_name = tables[0][0]
    print("Using table:", table_name)
    c.execute(f"SELECT symbol, direction, entry_price, exit_price, profit, timestamp FROM {table_name} WHERE symbol='UNKNOWN' ORDER BY timestamp DESC LIMIT 5")
    rows = c.fetchall()
    for row in rows:
        print(row)
else:
    print("No tables found")
conn.close()