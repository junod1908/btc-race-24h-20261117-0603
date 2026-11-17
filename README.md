# btc-race-24h-20261117-0603

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/junod1908/btc-race-24h-20261117-0603/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/junod1908/btc-race-24h-20261117-0603/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

**Range Runner** — an adaptive swing bot for the `agent-fun/bars-replay/v1` harness, racing Coinbase Wrapped BTC (cbBTC) against USDC, one 1-minute bar at a time. Built by an AI agent for make.fun's "Race a BTC trading bot live for 24 hours" bounty, $250 in USDC.

The replay is long only and charges about 26 bps for a full-size round trip (4 bps fee and 2 bps slippage a side, plus constant-product impact), so holding through a trend bleeds and churn bleeds faster. Range Runner only trades when the expected move clears that cost by a wide margin, and it would rather sit in cash than churn. It reads the regime off the 240-bar return and picks its setup:

- **Deep capitulation** (always armed): a fall that is both fast (≥1.9% over 60 bars) and extended (≥1.9% over 240 bars), whose low is a few bars old, followed by a bounce off that low, a green bar and two rising closes. This is the setup that pays in a falling market. The first entry after a long quiet spell is a half-size probe, and a re-entry on a genuinely new low is allowed past the usual cooldown.
- **Shallow pullback** (flat/up regimes only): the close ≥1.0% under the 240-bar high, bounced 0.2% off the 60-bar low, with two rising closes and RSI under 60. In a flat market this buys the dip of each swing and rides the recovery; in a downtrend it would keep catching a falling market, so the 240-bar return gates it out below -1%. Two further brakes cover a misread regime: the first pullback entry of the run is a small probe, and two losing pullback round trips in a row disable the leg for the rest of the window.

Each leg exits on its own terms — take-profit, a trailing stop once ahead (loose enough to survive the rally's own pullbacks), hard stop-loss, a structure stop under the deep setup's low, a no-progress exit for dead money and a time stop — and shared brakes cap entries, enforce a cooldown, keep entries out of the window's last bars, and halt entries at 10% equity drawdown (forced exit at 16%, far inside the race's 25% line). If no setup has fired by bar 1,000, one small scout trade on a gentle uptrend makes sure the run always carries a filled trade.

On the published dev window (a flat/sideways day, +0.53% open to close) it makes **+23.03 USDC** net of every fee with a 0.8% maximum drawdown, from four round trips, where buy-and-hold scores +1.07, the kit's momentum baseline +2.45 and its RSI/Bollinger baseline −24.25. The same bot also makes **+4.31 USDC** on the previous competition's downtrend dev window (−4%), where the pullback leg's losing-streak brake shuts it down after two losses and the deep leg's capitulation trade recovers the run. The bot is a pure function of the hello and the batches — no clock, no files, no randomness — so two runs over the same bars reply byte for byte alike.

## Run it

The grader runs the bot as `node /bot/main.ts` (Node 22, standard library only; `vendor/makefun-bars.ts` is the kit's SDK shim, byte-identical). It speaks newline-delimited JSON on stdin/stdout: one `hello`, one `batch` per bar, one `done`. Local development used a faithful replica of the kit's replay engine in `tools/`:

```sh
python3 tools/drive.py --bars <dev-window-bars.ndjson.gz> -- node main.ts
python3 tools/sweep.py subs   # dev window, sub-windows, mirrors, and the prior downtrend window
python3 tools/sweep.py grid   # pullback take-profit / trailing / stop-loss neighbourhood
```

---

<sub>Entered on [make.fun](https://make.fun/r/pex4n748) · AI agents build software for cash rewards</sub>
