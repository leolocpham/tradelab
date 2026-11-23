# TradeLab strategy library (29 + custom)

All strategies decide at a bar's close and fill at the next bar's open. Long-only unless **Allow shorts** is ticked; shorts mirror the long rules.
"Intraday" strategies need 1–30 minute bars and stay flat outside 9:30–15:55 ET. The bot (`bot/engine.py`) uses identical Python versions, checked to match the web page exactly.

## Trend
| Strategy | Rules (defaults) | Origin |
|---|---|---|
| SMA crossover | Long while SMA(10) > SMA(30) | Classic |
| EMA crossover | Long while EMA(9) > EMA(21) | Classic |
| MACD signal cross | Long while MACD(12,26) > signal(9) | Appel |
| Supertrend | ATR(10)×3 bands around (H+L)/2 that only trail toward price; long while trend is up, flips when close crosses the opposite band | Olivier Seban |
| Parabolic SAR | Stop-and-reverse with acceleration 0.02 per new extreme, max 0.20; long while SAR is below price | J. Welles Wilder (1978) |
| Hull MA cross | Long while HMA(9) > HMA(21); HMA(n) = WMA(2·WMA(n/2) − WMA(n), √n) | Alan Hull |
| Heikin-Ashi trend | Enter after 2 bullish HA candles in a row; exit on the first non-bullish HA candle | Japanese candle averaging |
| ADX + DI cross | Long when +DI > −DI and ADX(14) > 25; exit when +DI < −DI | Wilder |
| Ichimoku cloud | Long when Tenkan(9) > Kijun(26) and close above the cloud (spans shifted 26 bars); exit on Tenkan < Kijun or close below the cloud | Goichi Hosoda |

## Mean reversion
| Strategy | Rules (defaults) | Origin |
|---|---|---|
| RSI mean reversion | Buy RSI(14) < 30, sell RSI > 70 | Wilder |
| Bollinger mean reversion | Buy close < lower band (20, 2σ); exit at the middle band | Bollinger |
| Stochastic reversal | Buy %K crossing above %D below 20; exit %K > 80 | Lane |
| RSI(2) pullback | Close > SMA(200) and RSI(2) < 10 → buy; exit close > SMA(5) | Larry Connors |
| Internal bar strength | Buy IBS = (C−L)/(H−L) < 0.2; exit IBS > 0.8 or close above prior high | Quantified-strategies research |
| Williams %R reversal | Buy %R(14) crossing back above −80; exit above −20 | Larry Williams |
| VWAP z-score reversion | Buy when (close − VWAP) < −2 × rolling σ (10:15–15:45 only); exit back at VWAP | Intraday practice |
| Gap fade | Gap of 1–6% vs prior close → trade against it after the first bar; exit when the gap fills (prior close) or EOD | Classic gap fill |

## Breakout / momentum
| Strategy | Rules (defaults) | Origin |
|---|---|---|
| Donchian breakout | Buy close > prior 20-bar high; exit close < prior 10-bar low | Turtle traders |
| Keltner breakout | Buy close > EMA(20) + 2·ATR(10); exit close < EMA(20) | Chester Keltner / Linda Raschke |
| Squeeze release | Bollinger(20,2) inside Keltner(20,1.5 ATR) = squeeze; when it releases, trade the momentum sign; exit when momentum flips | John Carter (TTM Squeeze) |
| CCI momentum | Buy CCI(20) crossing above +100; exit CCI < 0 | Donald Lambert |
| Dual Thrust | Range = max(HH−LC, HC−LL) of prior 4 days; long above open + 0.5·Range, flat/short below open − 0.5·Range | Michael Chalek |
| Volatility breakout | Long once price closes above today's open + 0.5 × yesterday's range; hold to EOD | Larry Williams |

## Intraday research
| Strategy | Rules (defaults) | Origin |
|---|---|---|
| VWAP trend | Long above VWAP + 0.1% after 9:45; exit below VWAP | Intraday practice |
| Opening range breakout | Break of the first 15-minute high, one trade/day, exit below the range midpoint | Toby Crabel |
| ORB stocks-in-play | Trade in the direction of the first 5-minute candle; stop 10% of 14-day ATR; exit EOD. The paper applies it only to the top-20 relative-volume stocks, which is what the bot's screener does | Zarattini, Barbon & Aziz (2024), *A Profitable Day Trading Strategy for the U.S. Equity Market* |
| Noise-area momentum | Bands = open × (1 ± avg absolute move from the open at that time of day over 14 days), gap-adjusted; checked every :00/:30; long above the upper band; trailing exit at max(band, VWAP) | Zarattini, Aziz & Barbon (2024), *Beat the Market* |
| Last half-hour momentum | If the first half-hour return (prior close → 10:00) is positive, buy at 15:30 and hold to the close | Gao, Han, Li & Zhou (2018), *Journal of Financial Economics* |
| Gap and go | Gap ≥ 2%; after 15 min, buy a break of the opening range while above VWAP; exit below VWAP | Day-trading practice |

## Custom
Write entry and exit rules as formulas (see Settings → Formula helpers). Use `P1`–`P3` as tunable numbers so the optimizer and walk-forward can tune them.

## Caveats
- Published results (e.g. the stocks-in-play ORB) depend on universe, data and costs; independent replications are often weaker.
- Picking the best of 29 strategies on 25 days of history favours luck. Judge the bot by its out-of-sample paper record in the Bot tab, and use Walk-forward before trusting any single strategy.
