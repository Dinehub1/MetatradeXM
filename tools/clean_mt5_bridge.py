import re
from pathlib import Path

content = Path("src/bridges/mt5_bridge.py").read_text()
if "import logging" not in content:
    content = content.replace("from datetime import datetime\n", "from datetime import datetime\nimport logging\n\nlog = logging.getLogger(\"MT5Bridge\")\n")

content = content.replace("print(f\"  MT5 init error: {mt5.last_error()}\")", "log.error(f\"MT5 init error: {mt5.last_error()}\")")
content = content.replace("print(\"  Could not fetch account info.\")", "log.warning(\"Could not fetch account info.\")")
content = content.replace("print(f\"  Account:  #{info.login}  ({info.server})\")", "log.info(f\"Account:  #{info.login}  ({info.server})\")")
content = content.replace("print(f\"  Balance:  {info.balance:.2f} {info.currency}\")", "log.info(f\"Balance:  {info.balance:.2f} {info.currency}\")")
content = content.replace("print(f\"  Equity:   {info.equity:.2f}\")", "log.info(f\"Equity:   {info.equity:.2f}\")")
content = content.replace("print(f\"  Margin:   {info.margin:.2f}  |  Free: {info.margin_free:.2f}\")", "log.info(f\"Margin:   {info.margin:.2f}  |  Free: {info.margin_free:.2f}\")")
content = content.replace("print(f\"  Leverage: 1:{info.leverage}\")", "log.info(f\"Leverage: 1:{info.leverage}\")")
content = content.replace("print(\"\\n  No open positions.\")", "log.info(\"No open positions.\")")
content = content.replace("print(f\"\\n  Open positions ({len(positions)}):\")", "log.info(f\"Open positions ({len(positions)}):\")")
content = content.replace("            print(f\"  #{p.ticket}  {p.symbol}  {direction}  {p.volume} lots  \"\n                  f\"open@{p.price_open:.5f}  profit:{p.profit:.2f}\")", "            log.info(f\"#{p.ticket}  {p.symbol}  {direction}  {p.volume} lots open@{p.price_open:.5f}  profit:{p.profit:.2f}\")")
content = content.replace("print(f\"  Symbol {symbol} not found.\")", "log.error(f\"Symbol {symbol} not found.\")")
content = content.replace("print(f\"  Order error: retcode={result.retcode}  {result.comment}\")", "log.error(f\"Order error: retcode={result.retcode}  {result.comment}\")")
content = content.replace("print(f\"  Position #{ticket} not found.\")", "log.error(f\"Position #{ticket} not found.\")")

Path("src/bridges/mt5_bridge.py").write_text(content)
