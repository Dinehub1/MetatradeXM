import sqlite3
conn = sqlite3.connect('trades.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = c.fetchall()
print("Tables:", tables)
for table in tables:
    table_name = table[0]
    print(f"\nSchema for table {table_name}:")
    c.execute(f"PRAGMA table_info({table_name})")
    columns = c.fetchall()
    for col in columns:
        print(f"  {col[1]} ({col[2]})")
conn.close()