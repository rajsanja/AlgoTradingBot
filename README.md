# 🚀 SMC Algorithmic Trading Bot (Binance Futures & TradFi Perpetuals)

An institutional-grade **Smart Money Concepts (SMC)** automated trading bot designed for **Binance USDⓈ-M Futures** and **TradFi Perpetuals (Gold - XAU/USDT)**.

Built with top-down multi-timeframe analysis, 4-Hour key level anchoring, 1-Hour execution triggers, and native **24/7 Binance Cloud Server-Side SL/TP Protection**.

---

## 🌟 Key Features

- **Top-Down Multi-Timeframe Structure**:
  - Analyzes **Macro Trends** across `4H`, `1D`, `1W`, and `1M` timeframes.
  - Computes 4H Swing Highs (**BSL**), Swing Lows (**SSL**), Order Blocks (**OB**), Fair Value Gaps (**FVG**), and Key Support/Resistance floors & ceilings.
- **Precision 1H Execution**:
  - Triggers trades on the 1-Hour candle close aligned with the 50 EMA and fresh FVG confirmation.
- **Native 24/7 Binance Cloud Protection**:
  - Submits real `STOP_MARKET` and `TAKE_PROFIT_MARKET` algo orders directly to Binance servers (`/fapi/v1/algoOrder`).
  - **Safe to shut down your PC**: Your positions are protected by Binance cloud servers 24/7.
  - Automatically moves Stop Loss to Breakeven when price reaches 50% of the target distance.
- **Institutional Risk Management**:
  - **1% Risk Per Trade**: Dynamically sizes position quantity based on the exact distance to the 4H invalidation level.
  - **3% Max Daily Loss Circuit Breaker**: Automatically suspends trading for the day if the daily threshold is reached.
  - **Global Concurrent Trade Cap**: Configurable `MAX_CONCURRENT_TRADES` (default: `2`) to prevent over-exposure across correlated pairs.
  - **Single Trade Per Pair Rule**: Strictly enforces a single open position per symbol—never averages down into running trades.
- **Dual-Asset Diversification**:
  - Monitors high-liquidity crypto assets (`BTC/USDT`, `ETH/USDT`, `BNB/USDT`, `SOL/USDT`) alongside TradFi Gold perpetuals (`XAU/USDT`).
  - Automatically signs Binance TradFi-Perps contract agreements upon connection.
- **Live Visual Portfolio Dashboard**:
  - Displays real-time position metrics, entry prices, cloud stop-loss/take-profit levels, and unrealized PnL.

---

## 📋 Monitored Instruments

| Symbol | Asset Class | Base Precision |
| :--- | :--- | :--- |
| `BTC/USDT` | Cryptocurrency | 3 Decimals |
| `ETH/USDT` | Cryptocurrency | 2 Decimals |
| `BNB/USDT` | Cryptocurrency | 2 Decimals |
| `SOL/USDT` | Cryptocurrency | 2 Decimals |
| `XAU/USDT` | TradFi Commodity (Gold) | 3 Decimals |

---

## 🛠️ Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/rajsanja/AlgoTradingBot.git
cd AlgoTradingBot
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure API Credentials
Create a `.env` file in the project root directory:
```env
BINANCE_TESTNET_API_KEY=your_binance_testnet_api_key_here
BINANCE_TESTNET_API_SECRET=your_binance_testnet_api_secret_here
```

---

## 🚀 Running the Bot

Start the bot with:
```bash
python trading_bot.py
```

### Sample Dashboard Output
```text
==================================================================================
💼 [LIVE PORTFOLIO DASHBOARD - ALL ACTIVE POSITIONS]
==================================================================================
| Symbol    | Side  | Size     | Entry      | Current    | SL         | TP         | PnL ($)   | PnL (%)  |
|-----------|-------|----------|------------|------------|------------|------------|-----------|----------|
| BTC/USDT  | LONG  | 0.027    | $83,468.50 | $83,401.70 | $82,633.82 | $85,137.87 | -$1.80    | -0.08%   |
| XAU/USDT  | LONG  | 2.155    | $4,199.51  | $4,200.46  | $4,157.51  | $4,283.50  | +$2.05    | +0.02%   |
==================================================================================
💰 Total Unrealized PnL: +$0.24 | Active Positions: 2/2
☁️ Native Cloud SL/TP: ACTIVE on Binance (24/7 Protection - Safe to close PC)
==================================================================================
```

---

## 🔒 Security & Privacy

- `.env` and sensitive credentials are automatically ignored by `.gitignore`. Never commit API secret keys to version control.
- Designed by default to run on **Binance Futures Testnet (Demo)** for risk-free paper trading validation.

---

## 📄 License
MIT License
