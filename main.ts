// Range Runner — an adaptive swing bot for agent-fun/bars-replay/v1.
//
// One position at a time, long only, $1,000 of paper money against 1-minute
// cbBTC/USDC bars. A full-size round trip costs about 26 bps (4 bps fee and
// 2 bps slippage a side, plus constant-product impact), so the bot only
// trades when the expected move clears that cost by a wide margin, and it
// would rather sit in cash than churn.
//
// The bot reads the regime off the 240-bar return and picks its setup:
//
//   * Deep capitulation (always armed). A fall that is both fast (at least
//     1.9% over 60 bars) and extended (at least 1.9% over 240 bars), whose
//     low is a few bars old, followed by a bounce off that low, a green bar
//     and two rising closes. This is the setup that pays in a falling
//     market: it buys the turn after a capitulation, never the knife itself.
//     The first such entry after a long quiet spell is a half-size probe;
//     a re-entry a few bars later on a genuinely new low is allowed past
//     the usual cooldown, so a stopped-out first attempt does not lock the
//     bot out of the real turn.
//
//   * Shallow pullback (flat/up regimes only). The close at least 1.0%
//     under the 240-bar high, bounced 0.2% off the 60-bar low, with two
//     rising closes and RSI under 60. In a flat market this buys the dip of
//     each swing and rides the recovery; in a downtrend it would keep
//     catching a falling market, so the 240-bar return gates it out
//     (below -1% the leg stays shut). Two further brakes cover the case
//     the gate misreads the regime: the first pullback entry of the run is
//     a small probe, and two losing pullback round trips in a row disable
//     the leg for the rest of the window — the market has told the bot its
//     read is wrong, and the bot listens.
//
// Each leg exits on its own terms: a take-profit, a trailing stop once the
// trade is ahead (loose enough to survive the rally's own pullbacks), a
// hard stop-loss, a structure stop under the deep setup's low, a
// no-progress exit for dead money, and a time stop. Around both legs sit
// the shared brakes: an entry cap, a cooldown after each exit, no entries
// in the last bars of the window (a fresh position there would be flattened
// before it could pay for its own round trip), and an equity drawdown brake
// that stops new entries at 10% and forces an exit at 16%, far inside the
// race's 25% line. If no setup has fired by bar 1,000, one small scout
// position is taken on a gentle uptrend so the run always carries the
// filled trade the race requires.
//
// The bot is a pure function of the hello and the batches: it reads no
// clock, no file and no environment, and uses no randomness at all, so two
// runs over the same bars produce byte-identical replies.

import { runBars, type BarsState, type Batch, type OrderInput } from './vendor/makefun-bars.ts';

const SYMBOL = 'CBBTC';

/** Every tunable choice, in one place. */
const P = {
  /** 240-bar return under which the market counts as a downtrend. */
  downR240: -0.01,
  /** Deep setup: fall over 60 bars that arms it (0.019 = 1.9%). */
  deepDrop60: 0.019,
  /** Deep setup: fall over 240 bars that arms it. */
  deepDrop240: 0.019,
  /** Deep setup: bounce off the fall's low that confirms the turn. */
  deepBounce: 0.0035,
  /** Deep setup: the fall's low must be at least this many bars old. */
  deepLowMinAge: 2,
  /** Deep setup: …and no older than this. */
  deepLowMaxAge: 30,
  /** Deep setup: share of equity staked by a probe entry. */
  deepProbeFrac: 0.4,
  /** Deep setup: bars of quiet before the next entry counts as a probe. */
  deepProbeGap: 240,
  /** Deep setup: share of equity staked by a full entry. */
  deepSize: 0.65,
  /** Deep setup: take-profit, value over cost (0.032 = +3.2%). */
  deepTakeProfit: 0.032,
  /** Deep setup: stop-loss, value under cost (0.013 = -1.3%). */
  deepStopLoss: 0.013,
  /** Deep setup: trailing stop arms at this gain (0.008 = +0.8%). */
  deepTrailArm: 0.008,
  /** Deep setup: …and exits this far under the peak ratio (0.007). */
  deepTrailDist: 0.007,
  /** Deep setup: bars a position may live before the time stop sells it. */
  deepMaxHold: 240,
  /** Deep setup: bars after which a position that has not paid is sold. */
  deepNoProgressBars: 90,
  /** Deep setup: the ratio a position must beat by then to live on. */
  deepNoProgressRatio: 1.002,
  /** Deep setup: a close this far under the setup's low also exits. */
  deepStructStop: 0.003,
  /** Deep setup: bars between an exit and the next entry. */
  deepCooldown: 25,
  /** Pullback setup: bars over which the rolling high is read. */
  pullRefBars: 240,
  /** Pullback setup: fall under the rolling high that arms it (1.0%). */
  pullFall: 0.01,
  /** Pullback setup: bars over which the pullback low is read. */
  pullLowBars: 60,
  /** Pullback setup: bounce off the pullback low that confirms the turn. */
  pullBounce: 0.002,
  /** Pullback setup: consecutive rising closes confirming the turn. */
  pullUpCloses: 2,
  /** Pullback setup: RSI must stay under this at entry. */
  pullRsiMax: 60,
  /** Pullback setup: share of equity staked by the first entry (a probe). */
  pullProbeFrac: 0.4,
  /** Pullback setup: share of equity staked by later entries. */
  pullSize: 0.9,
  /** Pullback setup: take-profit, value over cost (0.02 = +2.0%). */
  pullTakeProfit: 0.02,
  /** Pullback setup: stop-loss, value under cost (0.008 = -0.8%). */
  pullStopLoss: 0.008,
  /** Pullback setup: trailing stop arms at this gain (0.004 = +0.4%). */
  pullTrailArm: 0.004,
  /** Pullback setup: …and exits this far under the peak ratio (0.01). */
  pullTrailDist: 0.01,
  /** Pullback setup: bars a position may live before the time stop sells it. */
  pullMaxHold: 400,
  /** Pullback setup: bars after which a position that has not paid is sold. */
  pullNoProgressBars: 150,
  /** Pullback setup: the ratio a position must beat by then to live on. */
  pullNoProgressRatio: 1.001,
  /** Pullback setup: bars between an exit and the next entry. */
  pullCooldown: 20,
  /** Pullback setup: losing round trips in a row that disable the leg. */
  pullLossStreakBrake: 2,
  /** Entries allowed in one run. */
  maxEntries: 6,
  /** Scored bar from which the one scout entry may open if none has. */
  scoutBar: 1000,
  /** Share of equity staked by the scout entry. */
  scoutSize: 0.2,
  /** Equity drawdown from its peak that stops new entries (0.10 = 10%). */
  ddHalt: 0.1,
  /** Equity drawdown from its peak that forces an exit (0.16 = 16%). */
  ddExit: 0.16,
  /** Slippage caps sent with the orders, measured from the latest close. */
  entrySlipBps: 150,
  exitSlipBps: 250,
  /** Scored bar (window bars are 1,440) after which no new entries open. */
  lastEntryBar: 1395,
};

/** Opens, highs, lows and closes of every bar seen, as USDC floats. */
const opens: number[] = [];
const lows: number[] = [];
const closes: number[] = [];
/** Bars of closes Wilder RSI averages over. */
const RSI_BARS = 14;
/** Wilder RSI state over the closes (14 bars). */
let rsiGain: number | null = null;
let rsiLoss: number | null = null;
const rsiFirst: number[] = [];
let rsiLast: number | null = null;

/** The open position's bookkeeping; entrySeq < 0 while flat. */
let entrySeq = -1;
let entryCost = 0;
let peakRatio = 1;
let setupLow = 0;
let leg: 'deep' | 'pull' | 'scout' | null = null;
/** Entries used, the last exit's bar, and the lowest low seen since it. */
let entries = 0;
let pullEntries = 0;
let lastDeepSeq = -1_000_000;
let lastExitSeq = -1_000_000;
let lowAtExit = Infinity;
/** Losing pullback round trips in a row; at the brake the leg shuts down. */
let pullLossStreak = 0;
let pullDisabled = false;
/** Equity peak (USDC float) for the drawdown brake. */
let equityPeak = 0;

/** The batch's bar folded into the price history (the universe is one symbol). */
function pushBar(batch: Batch): void {
  for (const bar of batch.bars) {
    if (bar.symbol !== SYMBOL) continue;
    const o = Number(bar.o) / 1e5;
    const h = Number(bar.h) / 1e5;
    const l = Number(bar.l) / 1e5;
    const c = Number(bar.c) / 1e5;
    // A bar with a non-finite price is corrupt data, not a market move:
    // skip it entirely so no indicator or setup ever reads NaN.
    if (!Number.isFinite(o) || !Number.isFinite(h) || !Number.isFinite(l) || !Number.isFinite(c)) continue;
    opens.push(o);
    lows.push(l);
    closes.push(c);
    if (rsiLast !== null) {
      const d = c - rsiLast;
      if (rsiGain === null || rsiLoss === null) {
        rsiFirst.push(d);
        if (rsiFirst.length === RSI_BARS) {
          rsiGain = rsiFirst.reduce((a, x) => a + (x > 0 ? x : 0), 0) / RSI_BARS;
          rsiLoss = rsiFirst.reduce((a, x) => a + (x < 0 ? -x : 0), 0) / RSI_BARS;
        }
      } else {
        rsiGain = (rsiGain * (RSI_BARS - 1) + Math.max(d, 0)) / RSI_BARS;
        rsiLoss = (rsiLoss * (RSI_BARS - 1) + Math.max(-d, 0)) / RSI_BARS;
      }
    }
    rsiLast = c;
  }
}

/** Wilder RSI(14) over the closes, or null before it is set. */
function rsi(): number | null {
  if (rsiGain === null || rsiLoss === null) return null;
  const moved = rsiGain + rsiLoss;
  return moved === 0 ? 50 : (100 * rsiGain) / moved;
}

/** The close's return over the last 240 bars, or 0 before there are 240. */
function ret240(): number {
  const n = closes.length;
  if (n < 241) return 0;
  return closes[n - 1] / closes[n - 241] - 1;
}

/** The deep capitulation setup: the fall's low, or null when unarmed. */
function deepSetup(): number | null {
  const n = closes.length;
  if (n < 242) return null;
  const close = closes[n - 1];
  if (close / closes[n - 61] - 1 > -P.deepDrop60) return null;
  if (close / closes[n - 241] - 1 > -P.deepDrop240) return null;
  let low = Infinity;
  let lowAt = -1;
  for (let i = n - 60; i < n; i++) {
    if (lows[i] < low) {
      low = lows[i];
      lowAt = i;
    }
  }
  const age = n - 1 - lowAt;
  if (age < P.deepLowMinAge || age > P.deepLowMaxAge) return null;
  if (close < low * (1 + P.deepBounce)) return null;
  if (close <= opens[n - 1]) return null; // the bar that just closed is green
  if (close <= closes[n - 2]) return null; // …a higher close than the bar before
  if (closes[n - 2] < closes[n - 3]) return null; // …and the step up began a bar earlier
  return low;
}

/** The shallow pullback setup: the pullback low, or null when unarmed. */
function pullSetup(): number | null {
  const n = closes.length;
  if (n < Math.max(P.pullRefBars, P.pullLowBars) + 2) return null;
  if (ret240() < P.downR240) return null; // downtrend regime: this leg stays out
  const close = closes[n - 1];
  let high = -Infinity;
  for (let i = n - P.pullRefBars; i < n; i++) {
    if (closes[i] > high) high = closes[i];
  }
  if (close / high - 1 > -P.pullFall) return null;
  let low = Infinity;
  for (let i = n - P.pullLowBars; i < n; i++) {
    if (lows[i] < low) low = lows[i];
  }
  if (close < low * (1 + P.pullBounce)) return null;
  for (let i = 1; i <= P.pullUpCloses; i++) {
    if (closes[n - i] <= closes[n - i - 1]) return null;
  }
  const r = rsi();
  if (r !== null && r > P.pullRsiMax) return null;
  return low;
}

/** The gentle-uptrend shape the single scout entry is allowed to take. */
function scoutSetup(): boolean {
  const n = closes.length;
  if (n < 61) return false;
  return closes[n - 1] > closes[n - 61] && closes[n - 1] > closes[n - 2];
}

await runBars({
  onBatch(state: BarsState, batch: Batch): OrderInput[] {
    pushBar(batch);
    if (state.warmingUp) return [];
    const held = state.positions.get(SYMBOL);
    const equity = Number(state.equity) / 1e6;
    if (equity > equityPeak) equityPeak = equity;
    const drawdown = equityPeak > 0 ? (equityPeak - equity) / equityPeak : 0;
    const n = closes.length;
    const close = n > 0 ? closes[n - 1] : 0;

    const sell = (): OrderInput[] => [
      { side: 'sell', symbol: SYMBOL, qty: held!.qty, max_slippage_bps: P.exitSlipBps },
    ];
    const buy = (frac: number): OrderInput[] => {
      let notional = (state.equity * BigInt(Math.round(frac * 10_000))) / 10_000n;
      if (notional > state.cash) notional = state.cash;
      if (notional < BigInt(state.hello.fees.min_notional_quote_units)) return [];
      return [{ side: 'buy', symbol: SYMBOL, notional_quote_units: notional, max_slippage_bps: P.entrySlipBps }];
    };

    // ---- holding: exits -------------------------------------------------
    if (held !== undefined) {
      if (entrySeq < 0) {
        entrySeq = state.seq;
        entryCost = Number(held.cost);
        peakRatio = 1;
      }
      const ratio = Number(state.value(SYMBOL, held.qty)) / entryCost;
      if (ratio > peakRatio) peakRatio = ratio;
      const deep = leg === 'deep';
      const tp = deep ? P.deepTakeProfit : P.pullTakeProfit;
      const sl = deep ? P.deepStopLoss : P.pullStopLoss;
      const arm = deep ? P.deepTrailArm : P.pullTrailArm;
      const dist = deep ? P.deepTrailDist : P.pullTrailDist;
      const maxHold = deep ? P.deepMaxHold : P.pullMaxHold;
      const noProgBars = deep ? P.deepNoProgressBars : P.pullNoProgressBars;
      const noProgRatio = deep ? P.deepNoProgressRatio : P.pullNoProgressRatio;
      // Book the round trip for the pullback leg's losing-streak brake: a
      // sell decided while the position is worth less than its cost counts
      // as a losing leg, and two in a row shut that leg down.
      const bookExit = (): OrderInput[] => {
        if (leg === 'pull') {
          if (ratio < 1) {
            pullLossStreak++;
            if (pullLossStreak >= P.pullLossStreakBrake) pullDisabled = true;
          } else {
            pullLossStreak = 0;
          }
        }
        return sell();
      };
      if (drawdown >= P.ddExit) return bookExit();
      if (ratio >= 1 + tp) return bookExit();
      if (peakRatio >= 1 + arm && ratio <= peakRatio - dist) return bookExit();
      if (ratio <= 1 - sl) return bookExit();
      if (deep && setupLow > 0 && close < setupLow * (1 - P.deepStructStop)) return bookExit();
      if (state.seq - entrySeq >= noProgBars && ratio < noProgRatio) return bookExit();
      if (state.seq - entrySeq >= maxHold) return bookExit();
      return [];
    }

    // ---- flat again after a position: book the exit ----------------------
    if (entrySeq >= 0) {
      lastExitSeq = state.seq;
      lowAtExit = Infinity;
      for (let i = Math.max(0, n - 60); i < n; i++) {
        if (lows[i] < lowAtExit) lowAtExit = lows[i];
      }
      entrySeq = -1;
      entryCost = 0;
      peakRatio = 1;
      setupLow = 0;
      leg = null;
    }

    // ---- flat: entries ----------------------------------------------------
    if (drawdown >= P.ddHalt) return [];
    if (entries >= P.maxEntries) return [];
    const scoredSeq = state.seq - state.warmupBars;
    if (scoredSeq > P.lastEntryBar) return [];

    const deepLow = deepSetup();
    if (deepLow !== null) {
      // The cooldown holds unless a genuinely new low has printed since the
      // exit and the full setup has fired again over it.
      const newLow = deepLow < lowAtExit;
      if (state.seq - lastExitSeq >= P.deepCooldown || newLow) {
        const frac = state.seq - lastDeepSeq >= P.deepProbeGap ? P.deepProbeFrac : P.deepSize;
        const orders = buy(frac);
        if (orders.length > 0) {
          entries++;
          leg = 'deep';
          lastDeepSeq = state.seq;
          setupLow = deepLow;
        }
        return orders;
      }
      return [];
    }

    const pullLow = pullDisabled ? null : pullSetup();
    if (pullLow !== null) {
      if (state.seq - lastExitSeq >= P.pullCooldown) {
        pullEntries++;
        const frac = pullEntries === 1 ? P.pullProbeFrac : P.pullSize;
        const orders = buy(frac);
        if (orders.length > 0) {
          entries++;
          leg = 'pull';
          setupLow = pullLow;
        }
        return orders;
      }
      return [];
    }

    // The scout: one small trade so the run always meets the race's bar.
    if (entries === 0 && scoredSeq >= P.scoutBar && scoutSetup()) {
      const orders = buy(P.scoutSize);
      if (orders.length > 0) {
        entries++;
        leg = 'scout';
        setupLow = 0;
      }
      return orders;
    }
    return [];
  },
});
