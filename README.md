# TradeLab

Personal day-trading research page: live Alpaca market data, built-in and custom indicators, 29 strategies plus a custom-formula strategy (20 added from published research; see STRATEGIES.md), backtester, grid optimizer and walk-forward test.

**Live page:** https://leolocpham.github.io/tradelab/

## How it works
- A single static page (`index.html`) that runs entirely in your browser. There is no server.
- Market data comes from the Alpaca Market Data API v2. The free plan gives real-time IEX and 15-minute-delayed SIP data.
- Downloaded bars are cached in the browser's IndexedDB, so later loads only fetch missing dates.
- API keys are entered in **Settings** and stored only in that browser's local storage (if "Remember" is ticked). They are never in this repo.
- Use the symbol `DEMO` to try every feature with synthetic data and no keys.

## Notes
- Backtests use signals at bar close and fill at the next bar open, with configurable slippage, commission, stop, target, shorts and end-of-day exit.
- Research tool only. Not investment advice.

## Daily bot (paper trading)
`bot/run.py` runs on GitHub Actions every trading day (`.github/workflows/bot.yml`):
1. **9:45 ET screen:** stocks priced over $5, averaging over $20M/day in traded value, gapping ≥2% with ≥1.5× relative volume.
2. **Strategy fit:** all 29 strategies (see STRATEGIES.md) are backtested on each candidate's prior 25 trading days of 5-min bars. Each stock keeps its best strategy if it passes minimum trades, profit factor and Sharpe. The top 5 are traded.
3. **Trading:** every 5 minutes the chosen strategy's signal drives market orders in the Alpaca **paper** account, with a 1.5% stop. No new entries in the last 15 minutes. Everything is flattened 5 minutes before the close.
4. Results go to `data/latest.json`, `data/days/` and `data/equity.csv`, and are shown in the **Bot** tab.

Settings: `bot/config.json`. Secrets needed: `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` (paper keys).
Manual run: Actions → TradeLab daily bot → Run workflow → `test` (screen only, no orders).

## Journal and learning
- **Journal tab:** log trades you take from the bot's plans (auto-filled from today's picks). Stored in your browser and, if connected in Settings → Journal sync, in your private `tradelab-data` repo (journal.json) so it is the same on every device.
- **Learning:** win rate, average %, R-multiple and verdicts by strategy, gap size, relative volume, entry time and plan adherence, from your trades and/or the bot's paper trades. Suggests settings changes, and fits a logistic-regression model once there are 20+ trades.
- **Bot self-tuning:** the bot appends every paper trade to `data/trades.csv`. When choosing strategies, it blends backtest Sharpe with each strategy's live record (weight n/(n+10)) and drops strategies averaging a loss over 15+ live trades (`learn_*` settings in `bot/config.json`).
