# TradeLab

Personal day-trading research page: live Alpaca market data, built-in and custom indicators, 9 strategies plus a custom-formula strategy, backtester, grid optimizer and walk-forward test.

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
