import sqlite3
conn = sqlite3.connect('trades.db')
c = conn.cursor()
c.execute("SELECT symbol, direction, entry_price, exit_price, profit, timestamp FROM trades WHERE symbol='UNKNOWN' ORDER BY timestamp DESC LIMIT 5")
rows = c.fetchall()
for row in rows:
    print(row)
conn.close()
