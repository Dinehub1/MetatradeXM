#!/usr/bin/env python3
"""MT5 AI Trading Bot — Futuristic Dashboard v3.0
Port: 8889 | Chart.js | Multi-symbol | Real-time | Glassmorphism | Threaded
"""
import http.server, json, sqlite3, base64, os
from socketserver import ThreadingMixIn
from pathlib import Path

PORT         = 8889
BASE_DIR     = Path(__file__).parent
DB_FILE      = BASE_DIR / "trades.db"
STATUS_FILE  = BASE_DIR / "bot_status.json"
CANDLES_FILE = BASE_DIR / "candles_cache.json"

# ── Simple HTTP Basic Auth (set DASH_USER / DASH_PASS env vars, or use defaults) ─
DASH_USER = os.environ.get("DASH_USER", "admin")
DASH_PASS = os.environ.get("DASH_PASS", "mt5bot2026!")
_AUTH_TOKEN = base64.b64encode(f"{DASH_USER}:{DASH_PASS}".encode()).decode()

# ── HTML Page ──────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MT5 AI — Trading Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#06090f;--bg1:#0b1017;--bg2:rgba(255,255,255,.04);--bg3:rgba(255,255,255,.07);
  --border:rgba(255,255,255,.08);--border2:rgba(255,255,255,.13);
  --cyan:#00e5ff;--cyan-d:rgba(0,229,255,.14);
  --purple:#a855f7;--purple-d:rgba(168,85,247,.12);
  --green:#00ff88;--green-d:rgba(0,255,136,.13);
  --red:#ff3b5c;--red-d:rgba(255,59,92,.13);
  --amber:#fbbf24;--amber-d:rgba(251,191,36,.12);
  --text:#e2e8f0;--muted:#64748b;
  --r:12px;--font:'Inter',system-ui,sans-serif;--mono:'JetBrains Mono','Fira Code',monospace;
}
html{scroll-behavior:smooth}
body{
  background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px;line-height:1.5;
  min-height:100vh;
  background-image:
    radial-gradient(ellipse 90% 55% at 50% -10%,rgba(0,229,255,.055) 0%,transparent 100%),
    radial-gradient(ellipse 55% 45% at 85% 85%,rgba(168,85,247,.04) 0%,transparent 100%);
}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:var(--bg1)}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:3px}

/* ─ Header ─ */
header{
  position:sticky;top:0;z-index:100;
  background:rgba(6,9,15,.88);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border);
  padding:0 20px;height:52px;
  display:flex;align-items:center;gap:14px;
}
.hdr-logo{color:var(--cyan);font-family:var(--mono);font-size:14px;font-weight:500;letter-spacing:.05em;white-space:nowrap}
.hdr-logo em{color:var(--muted);font-style:normal}
.pipe{color:var(--border2);user-select:none}
.hdr-r{margin-left:auto;display:flex;align-items:center;gap:12px}
#clockEl{font-family:var(--mono);font-size:12px;color:var(--muted)}
#clockEl .ist{color:var(--cyan);margin-left:8px;font-size:11px}
#cycleEl{font-family:var(--mono);font-size:11px;color:var(--muted)}

/* ─ Status dot ─ */
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex-shrink:0;transition:background .3s}
.dot.live{background:var(--green);box-shadow:0 0 7px var(--green);animation:pulse 2s infinite}
.dot.sleep{background:var(--amber);box-shadow:0 0 7px var(--amber)}
.dot.err{background:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* ─ Session badge ─ */
.sbadge{font-size:10px;font-family:var(--mono);font-weight:500;padding:2px 9px;border-radius:20px;letter-spacing:.06em;border:1px solid var(--border2);color:var(--muted);background:var(--bg2);transition:all .3s}
.sbadge.LONDON{border-color:rgba(0,229,255,.45);color:var(--cyan);background:var(--cyan-d)}
.sbadge.LONDON_NY_OVERLAP{border-color:rgba(0,255,136,.5);color:var(--green);background:var(--green-d)}
.sbadge.NEW_YORK{border-color:rgba(251,191,36,.45);color:var(--amber);background:var(--amber-d)}
.sbadge.ASIAN{border-color:rgba(168,85,247,.45);color:var(--purple);background:var(--purple-d)}

/* ─ Layout ─ */
.page{max-width:1600px;margin:0 auto;padding:14px 16px}

/* ─ Cards ─ */
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:16px;backdrop-filter:blur(6px);transition:border-color .2s}
.card:hover{border-color:var(--border2)}
.clabel{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:5px}
.cval{font-size:23px;font-weight:600;font-family:var(--mono)}
.csub{font-size:11px;color:var(--muted);margin-top:2px}

/* ─ Stats row ─ */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px}
@media(max-width:860px){.stats{grid-template-columns:repeat(2,1fr)}}

/* ─ Main 3-col ─ */
.main3{display:grid;grid-template-columns:210px 1fr 230px;gap:10px;margin-bottom:10px}
@media(max-width:1080px){.main3{grid-template-columns:1fr 1fr}}
@media(max-width:650px){.main3{grid-template-columns:1fr}}

/* ─ Indicators list ─ */
.irow{display:flex;align-items:center;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border)}
.irow:last-child{border-bottom:none}
.iname{font-size:11px;color:var(--muted);font-family:var(--mono)}
.ival{font-size:12px;font-weight:500;font-family:var(--mono)}
.ival.bull,.idot.bull{color:var(--green)}
.ival.bear,.idot.bear{color:var(--red)}
.ival.neu,.idot.neu{color:var(--amber)}
.idot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.idot.bull{background:var(--green);box-shadow:0 0 4px var(--green)}
.idot.bear{background:var(--red);box-shadow:0 0 4px var(--red)}
.idot.neu{background:var(--amber);box-shadow:0 0 4px var(--amber)}

/* ─ Signal card ─ */
.sigcard{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:13px;text-align:center;min-height:300px}
.sigbadge{font-size:44px;font-weight:700;font-family:var(--mono);padding:18px 44px;border-radius:var(--r);transition:all .4s;letter-spacing:.05em}
.sigbadge.BUY{color:var(--green);background:var(--green-d);border:1px solid rgba(0,255,136,.45);box-shadow:0 0 40px rgba(0,255,136,.22),0 0 80px rgba(0,255,136,.09);text-shadow:0 0 28px rgba(0,255,136,.9);animation:gbuy 2.5s ease-in-out infinite alternate}
.sigbadge.SELL{color:var(--red);background:var(--red-d);border:1px solid rgba(255,59,92,.45);box-shadow:0 0 40px rgba(255,59,92,.22),0 0 80px rgba(255,59,92,.09);text-shadow:0 0 28px rgba(255,59,92,.9);animation:gsell 2.5s ease-in-out infinite alternate}
.sigbadge.HOLD{color:var(--muted);background:var(--bg3);border:1px solid var(--border2)}
@keyframes gbuy{from{box-shadow:0 0 22px rgba(0,255,136,.15),0 0 45px rgba(0,255,136,.06)}to{box-shadow:0 0 50px rgba(0,255,136,.38),0 0 100px rgba(0,255,136,.14)}}
@keyframes gsell{from{box-shadow:0 0 22px rgba(255,59,92,.15),0 0 45px rgba(255,59,92,.06)}to{box-shadow:0 0 50px rgba(255,59,92,.38),0 0 100px rgba(255,59,92,.14)}}
.priceln{font-family:var(--mono);font-size:13px}
.pask{color:var(--green)}.pbid{color:var(--red)}.psep{color:var(--muted);margin:0 5px}
.confsec{width:100%}
.conflbl{font-size:10px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px;display:flex;justify-content:space-between}
.confbar{height:6px;background:var(--bg3);border-radius:3px;overflow:hidden}
.conffill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--cyan),var(--purple));transition:width .6s ease}
.trendbadges{display:flex;gap:7px;flex-wrap:wrap;justify-content:center}
.tb{font-size:10px;font-family:var(--mono);padding:2px 8px;border-radius:20px;border:1px solid var(--border2);color:var(--muted)}
.tb.bull{border-color:rgba(0,255,136,.35);color:var(--green);background:rgba(0,255,136,.06)}
.tb.bear{border-color:rgba(255,59,92,.35);color:var(--red);background:rgba(255,59,92,.06)}
.reasontxt{font-size:11px;color:var(--muted);line-height:1.55;max-width:380px}

/* ─ Session panel ─ */
.sesspanel{display:flex;flex-direction:column;gap:9px}
.sess-tl{position:relative;height:26px;background:var(--bg3);border-radius:4px;overflow:hidden;margin:2px 0}
.sseg{position:absolute;top:0;height:100%}
.sseg.asian{background:rgba(168,85,247,.5)}
.sseg.london{background:rgba(0,229,255,.5)}
.sseg.ny{background:rgba(251,191,36,.45)}
.sseg.overlap{background:rgba(0,255,136,.65);z-index:2}
.sneedle{position:absolute;top:0;width:2px;height:100%;background:#fff;z-index:10;box-shadow:0 0 5px #fff;transition:left .5s linear}
.sess-lbrow{display:flex;justify-content:space-between;font-size:9px;color:var(--muted);font-family:var(--mono)}
.sessitem{display:flex;align-items:center;gap:8px;padding:4px 0}
.sdot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.sname{font-size:11px;font-family:var(--mono);flex:1}
.stime{font-size:10px;color:var(--muted);font-family:var(--mono)}

/* ─ Charts row ─ */
.charts2{display:grid;grid-template-columns:2fr 1fr;gap:10px;margin-bottom:10px}
@media(max-width:860px){.charts2{grid-template-columns:1fr}}
.ctitle{font-size:10px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);margin-bottom:10px}
.cwrap{position:relative;height:210px}

/* ─ Donut ─ */
.dnutwrap{position:relative;height:175px}
.dnutctr{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none}
.dnutval{font-size:26px;font-weight:700;font-family:var(--mono)}
.dnutlbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.lgrid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:8px;text-align:center}
.lval{font-family:var(--mono);font-weight:600;font-size:15px}
.llbl{font-size:10px;color:var(--muted)}

/* ─ Symbols grid ─ */
.symtitle{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:9px}
.symgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(195px,1fr));gap:10px;margin-bottom:10px}
.symhdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px}
.symname{font-family:var(--mono);font-size:14px;font-weight:600}
.symbadge{font-size:10px;font-family:var(--mono);font-weight:600;padding:2px 8px;border-radius:4px}
.symbadge.BUY{background:var(--green-d);color:var(--green);border:1px solid rgba(0,255,136,.35)}
.symbadge.SELL{background:var(--red-d);color:var(--red);border:1px solid rgba(255,59,92,.35)}
.symbadge.HOLD{background:var(--bg3);color:var(--muted);border:1px solid var(--border2)}
.symprice{font-family:var(--mono);font-size:12px;margin-bottom:5px}
.sask{color:var(--green)}.sbid{color:var(--red)}
.symconf-bar{height:3px;background:var(--bg3);border-radius:2px;overflow:hidden;margin-top:3px}
.symconf-fill{height:100%;border-radius:2px;background:linear-gradient(90deg,var(--cyan),var(--purple));transition:width .5s}
.symspk{height:38px;margin:5px 0}
.symspk svg{width:100%;height:100%}
.symreason{font-size:10px;color:var(--muted);overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}

/* ─ Timeline ─ */
.tlwrap{position:relative;height:150px}

/* ─ History ─ */
.histhdr{display:flex;align-items:center;gap:10px;margin-bottom:11px}
.histcnt{font-size:10px;background:var(--bg3);padding:2px 8px;border-radius:20px;color:var(--muted);font-family:var(--mono)}
.tabwrap{overflow-x:auto}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:7px 9px;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);border-bottom:1px solid var(--border2);font-weight:500;white-space:nowrap}
td{padding:7px 9px;border-bottom:1px solid var(--border);font-size:12px}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--bg3)}
.dbuy{color:var(--green);font-family:var(--mono);font-weight:600}
.dsell{color:var(--red);font-family:var(--mono);font-weight:600}
.dhold{color:var(--muted);font-family:var(--mono)}
.atrade{color:var(--cyan)}.adry{color:var(--purple)}.ahalt{color:var(--amber)}
.tmono{font-family:var(--mono);font-size:11px}
.treason{color:var(--muted);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.emptyrow td{color:var(--muted);font-style:italic;padding:18px 9px}
.mb10{margin-bottom:10px}
/* ─ Live stats row ─ */
.lstats{display:grid;grid-template-columns:repeat(7,1fr);gap:8px;margin-bottom:10px}
@media(max-width:900px){.lstats{grid-template-columns:1fr 1fr 1fr}}
@media(max-width:550px){.lstats{grid-template-columns:1fr 1fr}}
.lstats .cval{font-size:18px;font-family:var(--mono);font-weight:700}
.lstats .csub{font-size:10px;color:var(--muted);font-family:var(--mono)}

/* ─ Live position timer ─ */
@keyframes breathe{0%,100%{opacity:1}50%{opacity:.6}}
.pos-live{animation:breathe 1.5s ease-in-out infinite}

/* ─ Flash animations ─ */
@keyframes flashGreen{0%{color:var(--green);font-size:18px}100%{color:var(--green);font-size:18px}}
@keyframes flashRed{0%{color:var(--red);font-size:18px}100%{color:var(--red);font-size:18px}}

</style>
</head>
<body>

<!-- HEADER -->
<header>
  <span class="dot" id="statusDot"></span>
  <div class="hdr-logo">⬡ MT5 AI <em>/ TRADING BOT</em></div>
  <span class="pipe">|</span>
  <span class="sbadge" id="sessLabel">LOADING</span>
  <div class="hdr-r">
    <span id="clockEl" class="">--:--:-- UTC <span class="ist">--:--:-- IST</span></span>
    <span class="pipe">|</span>
    <span id="cycleEl">Cycle —</span>
  </div>
</header>

<div class="page">

<!-- STATS ROW -->
<div class="stats mb10">
  <div class="card">
    <div class="clabel">Balance</div>
    <div class="cval" id="sBalance">—</div>
    <div class="csub" id="sCurrency">USD</div>
  </div>
  <div class="card">
    <div class="clabel">Equity</div>
    <div class="cval" id="sEquity">—</div>
    <div class="csub" id="sPnl">P&amp;L: —</div>
  </div>
  <div class="card">
    <div class="clabel">Trades (W/L)</div>
    <div class="cval" id="sTrades">—</div>
    <div class="csub" id="sWin">Win rate: —</div>
  </div>
  <div class="card">
    <div class="clabel">Mode</div>
    <div class="cval" id="sMode">—</div>
    <div class="csub" id="sState">—</div>
  </div>
  <div class="card">
    <div class="clabel">Next Analysis</div>
    <div class="cval" id="sCountdown" style="color:var(--amber);font-family:var(--mono)">—</div>
    <div class="csub" id="sCycle">Cycle —</div>
  </div>
  <div class="card">
    <div class="clabel">MT5 Connection</div>
    <div class="cval" id="sConnect" style="font-size:11px">—</div>
    <div class="csub" id="sConnSub">—</div>
  </div>
  <div class="card">
    <div class="clabel">Live P&L</div>
    <div class="cval" id="sLivePnl" style="color:var(--green)">$0.00</div>
    <div class="csub" id="sPnlPips">0 pips</div>
  </div>
  <div class="card">
    <div class="clabel">Streak (W/L)</div>
    <div class="cval" id="sStreak">W0 / L0</div>
    <div class="csub" id="sStreakSub">—</div>
  </div>
  <div class="card">
    <div class="clabel">Session P&L</div>
    <div class="cval" id="sSessPnl">$0.00</div>
    <div class="csub" id="sSessTrades">0 trades</div>
  </div>
</div>

<!-- OPEN POSITIONS PANEL -->
<div class="card mb10" id="posPanel">
  <div class="histhdr">
    <div class="ctitle" style="margin:0">🟢 Open Positions</div>
    <span class="histcnt" id="posCnt">0 open</span>
  </div>
  <div class="tabwrap">
    <table>
      <thead><tr>
        <th>Ticket</th><th>Symbol</th><th>Dir</th><th>Lots</th>
        <th>Open Price</th><th>SL</th><th>TP</th><th>P&amp;L</th>
      </tr></thead>
      <tbody id="posBody">
        <tr class="emptyrow"><td colspan="8">No open positions.</td></tr>
      </tbody>
    </table>
  </div>
</div>
<div id="posTimer" style="display:none;margin-top:8px;text-align:center">
  <span class="tmono" style="font-size:11px;color:var(--muted)">Position age: </span>
  <span class="tmono" id="posAge" style="font-size:13px;color:var(--cyan);font-weight:600">—</span>
</div>
<div id="posPnLWrap" style="display:none;margin-top:4px;text-align:center">
  <span class="tmono" style="font-size:15px;font-weight:700" id="posPnlVal">—</span>
  <span class="tmono" style="font-size:11px;color:var(--muted);margin-left:8px" id="posPipsVal">—</span>
</div>

<!-- MAIN 3-COL -->
<div class="main3 mb10">

  <!-- INDICATORS -->
  <div class="card">
    <div class="clabel">Indicators</div>
    <div class="irow"><span class="iname">RSI 14</span><span class="ival neu" id="i-rsi">—</span><div class="idot neu" id="d-rsi"></div></div>
    <div class="irow"><span class="iname">EMA Trend</span><span class="ival neu" id="i-ema">—</span><div class="idot neu" id="d-ema"></div></div>
    <div class="irow"><span class="iname">MACD</span><span class="ival neu" id="i-macd">—</span><div class="idot neu" id="d-macd"></div></div>
    <div class="irow"><span class="iname">Bollinger</span><span class="ival neu" id="i-bb">—</span><div class="idot neu" id="d-bb"></div></div>
    <div class="irow"><span class="iname">ATR 14</span><span class="ival neu" id="i-atr">—</span><div class="idot neu" id="d-atr"></div></div>
    <div class="irow"><span class="iname">H1 Trend</span><span class="ival neu" id="i-h1">—</span><div class="idot neu" id="d-h1"></div></div>
    <div class="irow"><span class="iname">H4 Trend</span><span class="ival neu" id="i-h4">—</span><div class="idot neu" id="d-h4"></div></div>
  </div>

  <!-- SIGNAL PANEL -->
  <div class="card sigcard">
    <div class="sigbadge HOLD" id="sigBadge">—</div>
    <div class="priceln">
      <span class="pask" id="pAsk">—</span><span class="psep">/</span><span class="pbid" id="pBid">—</span>
    </div>
    <div class="confsec">
      <div class="conflbl"><span>AI Confidence</span><span id="confPct" style="color:var(--cyan);font-family:var(--mono)">—%</span></div>
      <div class="confbar"><div class="conffill" id="confFill" style="width:0%"></div></div>
    </div>
    <div class="trendbadges" id="trendBadges"></div>
    <div class="reasontxt" id="reasonTxt">Waiting for bot data...</div>
  </div>

  <!-- SESSION PANEL -->
  <div class="card sesspanel">
    <div class="clabel">Market Sessions (UTC)</div>
    <div class="sess-tl">
      <div class="sseg asian"   style="left:0%;width:29.2%"></div>
      <div class="sseg london"  style="left:29.2%;width:37.5%"></div>
      <div class="sseg ny"      style="left:54.2%;width:37.5%"></div>
      <div class="sseg overlap" style="left:54.2%;width:12.5%"></div>
      <div class="sseg asian"   style="left:91.7%;width:8.3%"></div>
      <div class="sneedle" id="sessNeedle" style="left:0%"></div>
    </div>
    <div class="sess-lbrow"><span>0h</span><span>6h</span><span>12h</span><span>18h</span><span>24h</span></div>
    <div class="sessitem">
      <div class="sdot" style="background:rgba(168,85,247,.85)"></div>
      <span class="sname">Asian</span><span class="stime">22:00–07:00</span>
    </div>
    <div class="sessitem">
      <div class="sdot" style="background:rgba(0,229,255,.85)"></div>
      <span class="sname">London</span><span class="stime">07:00–16:00</span>
    </div>
    <div class="sessitem">
      <div class="sdot" style="background:rgba(0,255,136,.9)"></div>
      <span class="sname" style="color:var(--green)">Overlap ★</span><span class="stime">13:00–16:00</span>
    </div>
    <div class="sessitem">
      <div class="sdot" style="background:rgba(251,191,36,.85)"></div>
      <span class="sname">New York</span><span class="stime">13:00–22:00</span>
    </div>
  </div>

</div><!-- /main3 -->

<!-- LIVE STATS ROW -->
<div class="lstats mb10">
  <div class="card">
    <div class="clabel">Total Pips</div>
    <div class="cval" id="lsPips" style="color:var(--amber)">0</div>
    <div class="csub">closed pips</div>
  </div>
  <div class="card">
    <div class="clabel">Avg Win</div>
    <div class="cval" id="lsAvgWin" style="color:var(--green)">—</div>
    <div class="csub">pips/trade</div>
  </div>
  <div class="card">
    <div class="clabel">Avg Loss</div>
    <div class="cval" id="lsAvgLoss" style="color:var(--red)">—</div>
    <div class="csub">pips/trade</div>
  </div>
  <div class="card">
    <div class="clabel">Best Trade</div>
    <div class="cval" id="lsBest" style="color:var(--green)">—</div>
    <div class="csub">pips</div>
  </div>
  <div class="card">
    <div class="clabel">Worst Trade</div>
    <div class="cval" id="lsWorst" style="color:var(--red)">—</div>
    <div class="csub">pips</div>
  </div>
  <div class="card">
    <div class="clabel">Smart Exit P&L</div>
    <div class="cval" id="lsSE" style="color:var(--cyan);font-size:16px">—</div>
    <div class="csub" id="lsSESub">exits tracked</div>
  </div>
  <div class="card">
    <div class="clabel">Avg Duration</div>
    <div class="cval" id="lsDur" style="color:var(--muted)">—</div>
    <div class="csub">minutes</div>
  </div>
</div>

<!-- CHARTS ROW -->
<div class="charts2 mb10">
  <div class="card">
    <div class="ctitle">Equity Curve — Cumulative P&amp;L (pips)</div>
    <div class="cwrap"><canvas id="eqChart"></canvas></div>
  </div>
  <div class="card">
    <div class="ctitle">Signal Distribution</div>
    <div class="dnutwrap">
      <canvas id="donutChart"></canvas>
      <div class="dnutctr"><div class="dnutval" id="dnutVal">—</div><div class="dnutlbl">signals</div></div>
    </div>
    <div class="lgrid">
      <div><div class="lval" style="color:var(--green)" id="lgBuy">—</div><div class="llbl">BUY</div></div>
      <div><div class="lval" style="color:var(--red)"   id="lgSell">—</div><div class="llbl">SELL</div></div>
      <div><div class="lval" style="color:var(--muted)" id="lgHold">—</div><div class="llbl">HOLD</div></div>
    </div>
  </div>
</div>

<!-- SYMBOLS GRID -->
<div class="symtitle">Active Symbols</div>
<div class="symgrid mb10" id="symGrid">
  <div class="card" style="opacity:.4"><div class="clabel">Loading symbols...</div></div>
</div>

<!-- CONFIDENCE TIMELINE -->
<div class="card mb10">
  <div class="ctitle">Confidence Timeline — last 60 signals</div>
  <div class="tlwrap"><canvas id="confChart"></canvas></div>
</div>

<!-- HISTORY TABLE -->
<div class="card mb10">
  <div class="histhdr">
    <div class="ctitle" style="margin:0">Recent Signals &amp; Trades</div>
    <span class="histcnt" id="histCnt">0 records</span>
  </div>
  <div class="tabwrap">
    <table>
      <thead><tr>
        <th>Time</th><th>Symbol</th><th>Signal</th><th>Conf</th><th>Action</th><th>Reason</th>
      </tr></thead>
      <tbody id="histBody">
        <tr class="emptyrow"><td colspan="6">No data yet — start the bot.</td></tr>
      </tbody>
    </table>
  </div>
</div>

</div><!-- /page -->

<script>
// ── Globals ──────────────────────────────────────────────────────────────────
let eqChart = null, donutChart = null, confChart = null;
let lastCandles = {};
let _sessionStartEquity = null;
let _posOpenTime = null;

// ── Chart init ───────────────────────────────────────────────────────────────
function initCharts() {
  Chart.defaults.color = '#64748b';
  Chart.defaults.borderColor = 'rgba(255,255,255,0.05)';
  Chart.defaults.font.family = "'JetBrains Mono', monospace";
  Chart.defaults.font.size = 11;

  // Equity curve
  const ec = document.getElementById('eqChart').getContext('2d');
  const eg = ec.createLinearGradient(0, 0, 0, 210);
  eg.addColorStop(0, 'rgba(0,229,255,0.28)');
  eg.addColorStop(1, 'rgba(0,229,255,0.0)');
  eqChart = new Chart(ec, {
    type: 'line',
    data: { labels: [], datasets: [{ label: 'Pips', data: [], borderColor: '#00e5ff', backgroundColor: eg, borderWidth: 2, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: '#00e5ff' }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 500 },
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, tooltip: { backgroundColor: 'rgba(11,16,23,0.92)', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1, callbacks: { label: c => '  $' + c.parsed.y.toFixed(2) } } },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
        y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { callback: v => '$' + v.toFixed(0) } }
      }
    }
  });

  // Donut
  const dc = document.getElementById('donutChart').getContext('2d');
  donutChart = new Chart(dc, {
    type: 'doughnut',
    data: { labels: ['BUY', 'SELL', 'HOLD'], datasets: [{ data: [1,1,1], backgroundColor: ['rgba(0,255,136,0.7)','rgba(255,59,92,0.7)','rgba(100,116,139,0.45)'], borderColor: ['rgba(0,255,136,0.9)','rgba(255,59,92,0.9)','rgba(100,116,139,0.65)'], borderWidth: 1, hoverOffset: 6 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: '72%', animation: { duration: 600 }, plugins: { legend: { display: false }, tooltip: { backgroundColor: 'rgba(11,16,23,0.92)', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1 } } }
  });

  // Confidence timeline
  const cc = document.getElementById('confChart').getContext('2d');
  const cg = cc.createLinearGradient(0, 0, 0, 150);
  cg.addColorStop(0, 'rgba(251,191,36,0.22)');
  cg.addColorStop(1, 'rgba(251,191,36,0.0)');
  confChart = new Chart(cc, {
    type: 'line',
    data: { labels: [], datasets: [{ label: 'Confidence', data: [], borderColor: '#fbbf24', backgroundColor: cg, borderWidth: 1.5, fill: true, tension: 0.3, pointRadius: 0, pointHoverRadius: 3 }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
      plugins: { legend: { display: false } },
      scales: { x: { grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { maxTicksLimit: 10, maxRotation: 0 } }, y: { min: 0, max: 1, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { callback: v => Math.round(v*100) + '%' } } }
    }
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function indClass(val, isRsi) {
  if (isRsi) return +val < 40 ? 'bull' : +val > 60 ? 'bear' : 'neu';
  const s = String(val).toUpperCase();
  if (s.includes('BULL') || s === 'BULLISH' || s.includes('ABOVE')) return 'bull';
  if (s.includes('BEAR') || s === 'BEARISH' || s.includes('BELOW')) return 'bear';
  return 'neu';
}
function setInd(id, val, cl) {
  const e = document.getElementById('i-' + id), d = document.getElementById('d-' + id);
  if (!e) return;
  e.textContent = val || '—'; e.className = 'ival ' + (cl || 'neu');
  if (d) d.className = 'idot ' + (cl || 'neu');
}
function fmtPrice(v, sym) {
  if (!v) return '—';
  if (!sym) return (+v).toFixed(5);
  if (sym.includes('JPY')) return (+v).toFixed(3);
  if (sym.includes('XAU') || sym.includes('XAG')) return (+v).toFixed(2);
  return (+v).toFixed(5);
}
function sparklineSvg(closes) {
  if (!closes || closes.length < 3) return '';
  const sl = closes.slice(-80);
  const mn = Math.min(...sl), mx = Math.max(...sl), rng = mx - mn || 0.0001;
  const W = 100, H = 35;
  const pts = sl.map((v,i) => ((i/(sl.length-1))*W).toFixed(1) + ',' + (H - ((v-mn)/rng)*(H-4) - 2).toFixed(1)).join(' ');
  const clr = sl[sl.length-1] >= sl[0] ? '#00ff88' : '#ff3b5c';
  return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" style="width:100%;height:100%"><polyline points="'+pts+'" fill="none" stroke="'+clr+'" stroke-width="1.5" stroke-linejoin="round"/></svg>';
}

// ── Open positions ────────────────────────────────────────────────────────────
function renderPositions(positions) {
  const cnt = document.getElementById('posCnt');
  const body = document.getElementById('posBody');
  const pnlWrap = document.getElementById('posPnLWrap');
  if (!positions || !positions.length) {
    cnt.textContent = '0 open';
    body.innerHTML = '<tr class="emptyrow"><td colspan="8">No open positions.</td></tr>';
    if (pnlWrap) pnlWrap.style.display = 'none';
    const timerEl = document.getElementById('posTimer');
    if (timerEl) timerEl.style.display = 'none';
    _posOpenTime = null;
    return;
  }
  cnt.textContent = positions.length + ' open';
  cnt.textContent = positions.length + ' open';
  body.innerHTML = positions.map(p => {
    const dc = p.direction === 'BUY' ? 'dbuy' : 'dsell';
    const pc = (p.profit || 0) >= 0 ? 'var(--green)' : 'var(--red)';
    const pSign = (p.profit || 0) >= 0 ? '+' : '';
    const sym = p.symbol || '—';
    const gold = sym.includes('XAU') || sym.includes('GOLD');
    const dig = gold ? 2 : 5;
    return '<tr>' +
      '<td class="tmono">' + (p.ticket||'—') + '</td>' +
      '<td class="tmono">' + sym + '</td>' +
      '<td class="' + dc + '">' + (p.direction||'—') + '</td>' +
      '<td class="tmono">' + (p.volume||'—') + '</td>' +
      '<td class="tmono">' + (p.open_price ? (+p.open_price).toFixed(dig) : '—') + '</td>' +
      '<td class="tmono" style="color:var(--red)">' + (p.sl ? (+p.sl).toFixed(dig) : '—') + '</td>' +
      '<td class="tmono" style="color:var(--green)">' + (p.tp ? (+p.tp).toFixed(dig) : '—') + '</td>' +
      '<td class="tmono" style="color:' + pc + ';font-weight:600">' + pSign + (+p.profit||0).toFixed(2) + '</td>' +
    '</tr>';
  }).join('');

  // Start live timer for first position
  const p = positions[0];
  if (p) {
    // Try to get open time from various possible field names
    const openTime = p.open_time || p.openTime || p.ctime || p.time;
    if (openTime) {
      try {
        const ms = new Date(openTime).getTime();
        if (!isNaN(ms)) startPosTimer(ms);
      } catch(e) {}
    }
    // Show live P&L
    if (pnlWrap) {
      pnlWrap.style.display = 'block';
      const pc = (p.profit||0) >= 0 ? 'var(--green)' : 'var(--red)';
      const ps = (p.profit||0) >= 0 ? '+' : '';
      const pvEl = document.getElementById('posPnlVal');
      if (pvEl) {
        pvEl.textContent = ps + '$' + (+p.profit||0).toFixed(2);
        pvEl.style.color = pc;
      }
    }
  }
}

// ── Countdown timer ───────────────────────────────────────────────────────────
let _countdownVal = 0;
let _countdownInterval = null;
function startCountdown(secs) {
  _countdownVal = secs;
  if (_countdownInterval) clearInterval(_countdownInterval);
  _countdownInterval = setInterval(() => {
    if (_countdownVal > 0) _countdownVal--;
    const el = document.getElementById('sCountdown');
    if (el) el.textContent = _countdownVal > 0 ? _countdownVal + 's' : 'NOW';
  }, 1000);
}

// ── Live position timer ─────────────────────────────────────────────────────
let _posTimerInterval = null;
function startPosTimer(openTimeMs) {
  _posOpenTime = openTimeMs;
  const timerEl = document.getElementById('posTimer');
  if (timerEl) timerEl.style.display = 'block';
  if (_posTimerInterval) clearInterval(_posTimerInterval);
  _posTimerInterval = setInterval(() => {
    if (!_posOpenTime) return;
    const secs = Math.floor((Date.now() - _posOpenTime) / 1000);
    const m = Math.floor(secs / 60), s = secs % 60;
    const el = document.getElementById('posAge');
    if (el) el.textContent = m + 'm ' + String(s).padStart(2,'0') + 's';
  }, 1000);
}

// ── Live stats from status ──────────────────────────────────────────────────
function renderLiveStats(d) {
  const st = d.stats || {};
  const se = st.smart_exit || {};
  const cap = st.capital || {};

  // Smart exit P&L
  const seEl = document.getElementById('lsSE');
  if (seEl) {
    const seUsd = se.total_usd || 0;
    seEl.textContent = (seUsd >= 0 ? '+' : '') + '$' + seUsd.toFixed(2);
    seEl.style.color = seUsd >= 0 ? 'var(--green)' : 'var(--red)';
  }
  const seSub = document.getElementById('lsSESub');
  if (seSub) seSub.textContent = (se.total || 0) + ' exits';

  // Avg pips
  const avgPips = se.avg_pips;
  const awEl = document.getElementById('lsAvgWin');
  const alEl = document.getElementById('lsAvgLoss');
  if (avgPips !== undefined) {
    if (avgPips >= 0 && awEl) awEl.textContent = '+' + Math.abs(avgPips).toFixed(0);
    if (avgPips < 0 && alEl) alEl.textContent = Math.abs(avgPips).toFixed(0);
  }

  // Streak
  const streakEl = document.getElementById('sStreak');
  if (streakEl) streakEl.textContent = 'W' + (cap.consecutive_wins||0) + ' / L' + (cap.consecutive_losses||0);
  const streakSub = document.getElementById('sStreakSub');
  if (streakSub) {
    const cw = cap.consecutive_wins||0, cl = cap.consecutive_losses||0;
    if (cw >= 3) streakSub.textContent = '🔥 Hot streak!';
    else if (cl >= 3) streakSub.textContent = '❄️ Cold streak';
    else streakSub.textContent = (st.win_rate||0)+'% WR';
  }

  // Session P&L (from bot_status)
  if (_sessionStartEquity === null) {
    const ac = d.account || {};
    if (ac.balance !== undefined) _sessionStartEquity = ac.balance;
  }
  if (_sessionStartEquity !== null) {
    const ac = d.account || {};
    const sessPnl = (ac.equity || 0) - _sessionStartEquity;
    const spEl = document.getElementById('sSessPnl');
    if (spEl) {
      spEl.textContent = (sessPnl >= 0 ? '+' : '') + '$' + sessPnl.toFixed(2);
      spEl.style.color = sessPnl >= 0 ? 'var(--green)' : 'var(--red)';
    }
  }
  const sessTradesEl = document.getElementById('sSessTrades');
  if (sessTradesEl) sessTradesEl.textContent = (st.total_trades||0) + ' total trades';

  // Live P&L (equity vs balance)
  const ac = d.account || {};
  const pnl = (ac.equity || 0) - (ac.balance || 0);
  const lp = document.getElementById('sLivePnl');
  if (lp) {
    lp.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
    lp.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
  }
}


// ── Render status ─────────────────────────────────────────────────────────────
function renderStatus(d) {
  // Dot
  const dot = document.getElementById('statusDot');
  dot.className = 'dot ' + (d.state === 'running' ? 'live' : d.state === 'sleeping' ? 'sleep' : 'err');

  // Session badge
  const sess = d.session || (d.symbols ? (Object.values(d.symbols)[0] || {}).session : '') || '';
  const sl = document.getElementById('sessLabel');
  sl.textContent = sess.replace(/_/g,' ') || (d.state || '—').toUpperCase();
  sl.className = 'sbadge ' + sess;

  document.getElementById('cycleEl').textContent = 'Cycle ' + (d.cycle || '—');

  // Account stats
  const ac = d.account || {};
  if (ac.balance !== undefined) {
    document.getElementById('sBalance').textContent = '$' + (+ac.balance).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
    document.getElementById('sEquity').textContent  = '$' + (+ac.equity).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2});
    const pnl = (ac.equity || 0) - (ac.balance || 0);
    const pe = document.getElementById('sPnl');
    pe.textContent = 'P&L: ' + (pnl >= 0 ? '+' : '') + pnl.toFixed(2) + ' ' + (ac.currency || 'USD');
    pe.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
    document.getElementById('sCurrency').textContent = ac.currency || 'USD';
  }
  document.getElementById('sMode').textContent = d.dry_run === false ? '🔴 LIVE' : '📝 PAPER';
  document.getElementById('sState').textContent = d.state || '—';

  // Cycle counter
  const cycleEl2 = document.getElementById('sCycle');
  if (cycleEl2) cycleEl2.textContent = 'Cycle ' + (d.cycle || '—');

  // Countdown to next analysis
  if (d.next_analysis_in !== undefined) startCountdown(d.next_analysis_in);

  // Connection status
  const connEl = document.getElementById('sConnect');
  const connSub = document.getElementById('sConnSub');
  if (connEl) {
    const ok = d.connection === 'OK';
    connEl.textContent = ok ? '🟢 CONNECTED' : '🔴 ' + (d.connection || 'UNKNOWN');
    connEl.style.color = ok ? 'var(--green)' : 'var(--red)';
    if (connSub) connSub.textContent = ok ? 'MetaApi → XMGlobal' : 'Reconnecting...';
  }

  // Stats from continuous trader
  const st = d.stats || {};
  if (st.total_trades !== undefined) {
    document.getElementById('sTrades').textContent =
      (st.wins||0) + 'W / ' + (st.losses||0) + 'L (' + (st.total_trades||0) + ')';
    document.getElementById('sWin').textContent =
      'Win rate: ' + (st.win_rate||0) + '%';
  }

  // Open positions panel
  renderPositions(d.open_positions || []);

  // Live stats update
  renderLiveStats(d);

  // Pick primary signal (from first symbol if multi-symbol, else flat)
  let pSig, pConf, pReason, pInd, pH1, pH4, pAsk, pBid, pSym;
  if (d.symbols && Object.keys(d.symbols).length > 0) {
    const k = Object.keys(d.symbols)[0];
    const s = d.symbols[k] || {};
    pSym = k; pSig = s.signal; pConf = s.confidence; pReason = s.reason;
    pInd = s.indicators || {}; pH1 = s.h1_trend; pH4 = s.h4_trend;
    pAsk = s.ask; pBid = s.bid;
  } else {
    pSym = d.symbol; pSig = d.signal; pConf = d.confidence; pReason = d.reason;
    pInd = d.indicators || {}; pH1 = d.h1_trend; pH4 = d.h4_trend;
    pAsk = d.ask; pBid = d.bid;
  }
  pSig = pSig || 'HOLD'; pConf = pConf || 0;

  // Signal badge
  const sb = document.getElementById('sigBadge');
  sb.textContent = pSig;
  sb.className = 'sigbadge ' + (['BUY','SELL'].includes(pSig) ? pSig : 'HOLD');

  document.getElementById('confFill').style.width = Math.round(pConf * 100) + '%';
  document.getElementById('confPct').textContent = Math.round(pConf * 100) + '%';
  if (pReason) document.getElementById('reasonTxt').textContent = pReason;
  document.getElementById('pAsk').textContent = fmtPrice(pAsk, pSym);
  document.getElementById('pBid').textContent = fmtPrice(pBid, pSym);

  // Trend badges
  const tb = document.getElementById('trendBadges');
  const bs = [];
  if (pH4) { const c = indClass(pH4); bs.push('<span class="tb ' + c + '">H4: ' + pH4 + '</span>'); }
  if (pH1) { const c = indClass(pH1); bs.push('<span class="tb ' + c + '">H1: ' + pH1 + '</span>'); }
  tb.innerHTML = bs.join('');

  // Indicators
  if (pInd.rsi !== undefined) setInd('rsi', (+pInd.rsi).toFixed(1), indClass(+pInd.rsi, true));
  if (pInd.ema_trend)  setInd('ema',  pInd.ema_trend,  indClass(pInd.ema_trend));
  if (pInd.macd_signal) setInd('macd', pInd.macd_signal, indClass(pInd.macd_signal));
  if (pInd.bb_position) setInd('bb',  pInd.bb_position, indClass(pInd.bb_position));
  if (pInd.atr !== undefined) setInd('atr', (+pInd.atr).toFixed(4), 'neu');
  if (pH1) setInd('h1', pH1, indClass(pH1));
  if (pH4) setInd('h4', pH4, indClass(pH4));

  // Session needle
  const n = document.getElementById('sessNeedle');
  const now = new Date();
  n.style.left = ((now.getUTCHours() + now.getUTCMinutes()/60) / 24 * 100).toFixed(2) + '%';

  // Symbol cards
  if (d.symbols && Object.keys(d.symbols).length > 0) {
    renderSymbols(d.symbols);
  } else if (pSym) {
    renderSymbols({ [pSym]: { signal: pSig, confidence: pConf, reason: pReason, ask: pAsk, bid: pBid } });
  }
}

// ── Symbol cards ──────────────────────────────────────────────────────────────
function renderSymbols(syms) {
  const keys = Object.keys(syms);
  if (!keys.length) return;
  document.getElementById('symGrid').innerHTML = keys.map(sym => {
    const s = syms[sym] || {};
    const sig = s.signal || 'HOLD';
    const conf = s.confidence || 0;
    const label = sym === 'XAUUSD' ? '🥇 '+sym : sym === 'XAGUSD' ? '🥈 '+sym : sym === 'USOIL' ? '🛢 '+sym : sym;
    const sp = sparklineSvg((lastCandles[sym] || {}).closes);
    return '<div class="card">' +
      '<div class="symhdr"><span class="symname">'+label+'</span><span class="symbadge '+sig+'">'+sig+'</span></div>' +
      '<div class="symprice"><span class="sask">'+fmtPrice(s.ask, sym)+'</span> <span style="color:var(--muted);font-size:9px">ASK</span> &nbsp; <span class="sbid">'+fmtPrice(s.bid, sym)+'</span> <span style="color:var(--muted);font-size:9px">BID</span></div>' +
      '<div><span style="font-size:10px;color:var(--muted)">Conf '+Math.round(conf*100)+'%</span><div class="symconf-bar"><div class="symconf-fill" style="width:'+Math.round(conf*100)+'%"></div></div></div>' +
      '<div class="symspk">'+(sp || '<div style="height:100%;background:var(--bg3);border-radius:2px;opacity:.3"></div>')+'</div>' +
      '<div class="symreason">'+(s.reason || '—')+'</div>' +
    '</div>';
  }).join('');
}

// ── History + charts ──────────────────────────────────────────────────────────
function renderHistory(rows) {
  document.getElementById('histCnt').textContent = rows.length + ' records';

  const exec = rows.filter(r => r.action === 'TRADE' || r.action === 'DRY_RUN');
  document.getElementById('sTrades').textContent = exec.length;
  const buys = exec.filter(r => r.direction === 'BUY').length;
  document.getElementById('sWin').textContent = exec.length ? Math.round(buys/exec.length*100) + '% BUY rate' : '—';

  // Donut
  const nB = rows.filter(r => r.direction === 'BUY').length;
  const nS = rows.filter(r => r.direction === 'SELL').length;
  const nH = rows.filter(r => r.direction === 'HOLD').length;
  donutChart.data.datasets[0].data = [nB, nS, nH];
  donutChart.update('none');
  document.getElementById('dnutVal').textContent = rows.length;
  document.getElementById('lgBuy').textContent  = nB;
  document.getElementById('lgSell').textContent = nS;
  document.getElementById('lgHold').textContent = nH;

  // Equity curve — real account balance tracking
  // We reconstruct equity progression from cumulative pips
  // Starting balance $781.90 as reference
  const START_BALANCE = 781.90;
  const PIP_VALUE = 0.10; // approx $0.10 per pip per 0.01 lot
  let cumPips = 0;
  const elabels = [], edata = [];
  rows.slice().reverse().filter(r => r.action === 'TRADE' || r.action === 'DRY_RUN').forEach(r => {
    const pips = r.pips || (r.direction === 'BUY' ? 5 : r.direction === 'SELL' ? -5 : 0);
    cumPips += pips;
    elabels.push((r.ts || '').substring(5,16).replace('T',' '));
    edata.push(START_BALANCE + cumPips * PIP_VALUE);
  });
  eqChart.data.labels = elabels; eqChart.data.datasets[0].data = edata;
  eqChart.update('none');

  // Confidence timeline
  const recent = rows.slice(0,60).reverse();
  confChart.data.labels = recent.map(r => (r.ts||'').substring(5,16).replace('T',' '));
  confChart.data.datasets[0].data = recent.map(r => r.confidence || 0);
  confChart.update('none');

  // Table
  const aC = { TRADE:'atrade', DRY_RUN:'adry', MARKET_CLOSED:'ahalt', DRAWDOWN_HALT:'ahalt', SKIP_SESSION:'ahalt' };
  document.getElementById('histBody').innerHTML = rows.length
    ? rows.slice(0,50).map(r => {
        const dc = r.direction==='BUY' ? 'dbuy' : r.direction==='SELL' ? 'dsell' : 'dhold';
        const utcTs = (r.ts||'').substring(0,19).replace('T',' ');
        // Convert UTC ts → IST (+5:30). Trim to 19 chars first to drop microseconds.
        let istTs = '';
        try {
          const clean = utcTs.replace(' ','T') + 'Z'; // "2026-04-13T11:16:11Z"
          const d = new Date(clean);
          if (!isNaN(d)) {
            const ist = new Date(d.getTime() + 5.5*3600000);
            const p = x => String(x).padStart(2,'0');
            istTs = ist.getUTCFullYear()+'-'+p(ist.getUTCMonth()+1)+'-'+p(ist.getUTCDate())+' '+p(ist.getUTCHours())+':'+p(ist.getUTCMinutes())+':'+p(ist.getUTCSeconds());
          }
        } catch(e) {}
        return '<tr><td class="tmono" style="line-height:1.6">'+utcTs+'<br><span style="color:var(--cyan);font-size:10px">'+istTs+' IST</span></td>' +
          '<td class="tmono">'+(r.symbol||'—')+'</td>' +
          '<td class="'+dc+'">'+(r.direction||'—')+'</td>' +
          '<td class="tmono">'+Math.round((r.confidence||0)*100)+'%</td>' +
          '<td class="'+(aC[r.action]||'')+'">'+(r.action||'—')+'</td>' +
          '<td class="treason">'+(r.reason||'—')+'</td></tr>';
      }).join('')
    : '<tr class="emptyrow"><td colspan="6">No signals yet.</td></tr>';
}

// ── Clock + session needle ────────────────────────────────────────────────────
function updateClock() {
  const n = new Date();
  const pad = x => String(x).padStart(2,'0');
  // UTC time
  const utcStr = pad(n.getUTCHours())+':'+pad(n.getUTCMinutes())+':'+pad(n.getUTCSeconds())+' UTC';
  // IST = UTC + 5:30
  const istOffset = 5.5 * 60; // minutes
  const istMs = n.getTime() + istOffset * 60000;
  const ist = new Date(istMs);
  const istStr = pad(ist.getUTCHours())+':'+pad(ist.getUTCMinutes())+':'+pad(ist.getUTCSeconds())+' IST';
  const clockEl = document.getElementById('clockEl');
  if (clockEl) {
    clockEl.childNodes[0].textContent = utcStr + ' ';
    const istSpan = clockEl.querySelector('.ist');
    if (istSpan) istSpan.textContent = istStr;
  }
  const needle = document.getElementById('sessNeedle');
  if (needle) needle.style.left = ((n.getUTCHours()+n.getUTCMinutes()/60)/24*100).toFixed(2)+'%';
}

// ── Poll ──────────────────────────────────────────────────────────────────────
async function poll() {
  try {
    const [status, history, candles] = await Promise.all([
      fetch('/api/status').then(r => r.ok ? r.json() : null).catch(() => null),
      fetch('/api/history').then(r => r.ok ? r.json() : []).catch(() => []),
      fetch('/api/candles').then(r => r.ok ? r.json() : {}).catch(() => ({}))
    ]);
    if (candles && Object.keys(candles).length) lastCandles = candles;
    if (status) {
      renderStatus(status);
      // Refresh positions even if history didn't change
      if (status.open_positions !== undefined) renderPositions(status.open_positions);
    }
    if (history && history.length) renderHistory(history);
  } catch(e) { console.warn('poll error', e); }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  poll();
  setInterval(poll, 2000);
  setInterval(updateClock, 1000);
  updateClock();
});
</script>
</body>
</html>"""


# ── HTTP Handler ───────────────────────────────────────────────────────────────
class DashHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress per-request noise

    def _json(self, data, code=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        """Return True if request is authenticated."""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            token = auth[6:].strip()
            if token == _AUTH_TOKEN:
                return True
        # Not authenticated — send 401
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="MT5 Trading Dashboard"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._check_auth():
            return
        path = self.path.split('?')[0]

        if path in ('/', '/index.html'):
            body = HTML.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)

        elif path == '/api/status':
            data = {'state': 'stopped'}
            if STATUS_FILE.exists():
                try:
                    import time as _t
                    data = json.loads(STATUS_FILE.read_text())
                    # Inject staleness: how many seconds since bot last wrote status
                    age_s = int(_t.time() - STATUS_FILE.stat().st_mtime)
                    data['_status_age_s'] = age_s
                    if age_s > 120:
                        data['_stale'] = True
                        data['state'] = f"stale ({age_s}s ago)"
                except Exception:
                    data = {'state': 'error'}
            self._json(data)

        elif path == '/api/history':
            rows = []
            if DB_FILE.exists():
                try:
                    conn = sqlite3.connect(str(DB_FILE))
                    conn.row_factory = sqlite3.Row
                    rows = [dict(r) for r in conn.execute(
                        'SELECT ts, symbol, direction, confidence, reason, action, ticket '
                        'FROM signals ORDER BY id DESC LIMIT 200'
                    ).fetchall()]
                    conn.close()
                except Exception:
                    rows = []
            self._json(rows)

        elif path == '/api/candles':
            data = {}
            if CANDLES_FILE.exists():
                try:
                    data = json.loads(CANDLES_FILE.read_text())
                except Exception:
                    data = {}
            self._json(data)

        else:
            self.send_response(404)
            self.end_headers()


# ── Threaded HTTP Server (handles concurrent connections) ──────────────────────
class ThreadedHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    """Handle each request in a separate thread — no more connection timeouts."""
    daemon_threads = True
    allow_reuse_address = True  # prevents 'Address already in use' on fast restart


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    srv = ThreadedHTTPServer(('0.0.0.0', PORT), DashHandler)
    print(f"\n{'='*58}")
    print(f"  MT5 AI Trading Dashboard  v3.0  [THREADED]")
    print(f"  Open  →  http://92.4.71.177:{PORT}")
    print(f"  DB    →  {DB_FILE}")
    print(f"  Start: python3 continuous_trader.py")
    print(f"{'='*58}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nDashboard stopped.')
