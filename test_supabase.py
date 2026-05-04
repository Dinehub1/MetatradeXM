import sys, os
sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('.env')

from core.supabase_db import SupabaseDB
db = SupabaseDB()
print('✅ Connected to:', db.url)

for t in ['trade_outcomes','trade_entries','market_patterns','learning_log','filtered_trades']:
    r = db.client.table(t).select('id').limit(1).execute()
    print(f'   ✓ {t}')

print('\n✅ All tables reachable!')
