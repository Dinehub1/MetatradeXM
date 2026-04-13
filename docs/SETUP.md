# Setup & Installation

Follow these steps to deploy MetatradeXM on a local machine or server.

## 1. Prerequisites

- Python 3.9+
- A [MetaApi Cloud](https://app.metaapi.cloud/) account
- An MT5 Demo or Live account (e.g. XM Global, IC Markets)
- [Ollama](https://ollama.com/) installed and running locally

## 2. MetaApi Configuration

MetaApi translates standard Python REST calls into MT5 terminal commands.
1. Sign up at MetaApi Cloud.
2. Navigate to "MT4/MT5 Accounts" -> "Add account".
3. Provide your MT5 broker, login, and password. Wait for provisioning (Status: `DEPLOYED`).
4. Generate an API Token under "API Access".

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
   METAAPI_TOKEN=your_jwt_token_here
   METAAPI_ACCOUNT_ID=the_uuid_of_your_mt5_account_here
   ```

## 4. Ollama Configuration

MetatradeXM relies on `minimax-m2.7:cloud` for deterministic reasoning, but any robust reasoning model works (like `llama3.1`).
1. Make sure Ollama daemon is running.
2. Pull the model:
   ```bash
   ollama run minimax-m2.7:cloud
   ```
If you encounter timeout errors during execution, ensure your machine has sufficient RAM allocated to Ollama.

## 5. First Run

Before full deployment, test via Paper Trading.

```bash
bash start_trading_cycle.sh --dry
```
This boots the continuous trader and dashboard but prevents the `metaapi_bridge` from placing actual trades to the broker. Monitor the logs to ensure indicators calculate successfully.
