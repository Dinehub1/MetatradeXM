# Setup & Installation

Follow these steps to deploy MetatradeXM on a local machine or server.

## 1. Prerequisites

- Python 3.9+
- An MT5 Demo or Live account (e.g. XM Global, IC Markets)
- A running Windows MT5 webhook bridge on ports `5001` and `5002`
- A Supabase project
- An NVIDIA API key

## 2. Windows Bridge Configuration

Point the bot at your Windows MT5 bridge:
1. Make sure the Windows bridge is running.
2. Confirm the HTTP endpoint on port `5001`.
3. Confirm the WebSocket endpoint on port `5002`.

## 3. Project Configuration

1. Clone or download this repository.
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Prepare the environment variables file:
   ```bash
   cp .env.example .env
   ```
4. Edit the `.env` file and input your keys:
   ```
   NVIDIA_API_KEY=your_nvidia_key_here
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_ANON_KEY=your_anon_key_here
   WIN_WEBHOOK_URL=http://your-windows-host:5001
   WIN_WS_URL=ws://your-windows-host:5002
   ```

## 4. AI Configuration

MetatradeXM uses NVIDIA for trade confirmation. If NVIDIA is unavailable, the bot falls back to deterministic indicator logic.

## 5. First Run

Before full deployment, test via Paper Trading.

```bash
bash start_trading_cycle.sh --dry
```
This boots the continuous trader and dashboard but prevents the `metaapi_bridge` from placing actual trades to the broker. Monitor the logs to ensure indicators calculate successfully.
This boots the continuous trader and dashboard but prevents the Windows bridge flow from placing actual trades to the broker. Monitor the logs to ensure indicators calculate successfully.
