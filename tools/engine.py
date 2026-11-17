"""Faithful local replica of the kit's bars replay engine (sim/bars/engine.ts).

Integer math mirrors the TypeScript BigInt code path for path: BarsPool,
barsBuyFill, barsSellFill, the decision checks and the end-of-window flatten.
Used only for local development; the submitted bot never imports this file.
"""

import gzip
import json
from math import gcd

BPS = 10_000
USDC_DECIMALS = 6

QUOTE_SCALE = 6
PRICE_SCALE = 5
QTY_SCALE = 8
START_CASH = 1_000_000_000
FEE_BPS = 4
SLIPPAGE_BPS = 2
MIN_NOTIONAL = 1_000_000
MAX_ORDERS = 16
MAX_WEIGHT_BPS = 10_000
MAX_OPEN_POSITIONS = 1


def value(qty, price):
    """quote units a qty is worth at price (truncating)."""
    return qty * price * 10**QUOTE_SCALE // 10 ** (QTY_SCALE + PRICE_SCALE)


class Pool:
    """BarsPool: constant-product hops in series kept as a reduced fraction."""

    __slots__ = ("yn", "yd", "kn", "kd", "unit")

    def __init__(self, yn, yd, kn, kd, unit):
        g = gcd(yn, yd)
        self.yn = yn // g
        self.yd = yd // g
        self.kn = kn
        self.kd = kd
        self.unit = unit

    @classmethod
    def of(cls, depth):
        d = [x * 10**QUOTE_SCALE for x in depth]
        prod = 1
        for x in d:
            prod *= x
        total = 0
        for i in range(len(d)):
            other = 1
            for j, x in enumerate(d):
                if j != i:
                    other *= x
            total += other
        g = gcd(prod, total)
        p, s = prod // g, total // g
        return cls(p, s, p * p, s * s, 10**USDC_DECIMALS)

    def buy(self, spend):
        yn2 = self.yn + spend * self.unit * self.yd
        num = self.yn * yn2 * self.kd
        den = self.yd * self.yd * self.kn
        return num, den, Pool(yn2, self.yd, self.kn, self.kd, self.unit)

    def sell(self, val):
        t = self.kn * self.yd + val * self.unit * self.yn * self.kd
        num = t * self.yd
        den = self.kd * self.yn * self.yn
        return num, den, Pool(self.kn * self.yn, t, self.kn, self.kd, self.unit)


def buy_fill(notional, open_price, pool, fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS):
    spend = notional * (BPS - fee_bps) // BPS
    num, den, after = pool.buy(spend)
    price = open_price * (BPS + slippage_bps) * num // (BPS * den)
    qty = spend * 10 ** (QTY_SCALE + PRICE_SCALE) // (price * 10**QUOTE_SCALE)
    return {"price": price, "spend": spend, "fee": notional - spend, "qty": qty, "after": after}


def sell_fill(qty, open_price, pool, fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS):
    num, den, after = pool.sell(value(qty, open_price))
    slipped = open_price * (BPS - slippage_bps) * den // (BPS * num)
    price = slipped if slipped >= 1 else 1
    gross = value(qty, price)
    proceeds = gross * (BPS - fee_bps) // BPS
    return {"price": price, "gross": gross, "fee": gross - proceeds, "proceeds": proceeds, "after": after}


def load_bars(path):
    bars = []
    with gzip.open(path, "rt") as f:
        for line in f:
            b = json.loads(line)
            bars.append(
                {
                    "t": b["t"],
                    "o": int(b["o"]),
                    "h": int(b["h"]),
                    "l": int(b["l"]),
                    "c": int(b["c"]),
                    "v": b["v"],
                    "od": [int(x) for x in b["o_depth"]],
                    "cd": [int(x) for x in b["c_depth"]],
                    "wire": {
                        "symbol": "CBBTC",
                        "t": b["t"],
                        "o": b["o"],
                        "h": b["h"],
                        "l": b["l"],
                        "c": b["c"],
                        "v": b["v"],
                        "o_depth": b["o_depth"],
                        "c_depth": b["c_depth"],
                    },
                }
            )
    return bars


def make_hello(bars, seed="0"):
    return {
        "type": "hello",
        "protocol": "agent-fun/bars-replay/v1",
        "seed": seed,
        "symbols": [
            {
                "symbol": "CBBTC",
                "mint": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
                "price_scale": PRICE_SCALE,
                "qty_scale": QTY_SCALE,
            }
        ],
        "capital": {"quote": "USDC", "quote_scale": QUOTE_SCALE, "quote_units": str(START_CASH)},
        "bar_interval": "1m",
        "first_close_ms": bars[0]["t"],
        "caps": {
            "max_open_positions": MAX_OPEN_POSITIONS,
            "max_orders_per_bar": MAX_ORDERS,
            "max_weight_bps": MAX_WEIGHT_BPS,
            "shorts": False,
            "leverage": False,
        },
        "fees": {"fee_bps": FEE_BPS, "slippage_bps": SLIPPAGE_BPS, "min_notional_quote_units": str(MIN_NOTIONAL)},
        "latency_bars": 1,
    }


class Engine:
    """Lockstep replay: feed replies (order lists) batch by batch, get batches."""

    def __init__(self, bars, fee_multiplier=1):
        self.bars = bars
        self.n = len(bars)
        self.fee_bps = min(BPS, FEE_BPS * fee_multiplier)
        self.cash = START_CASH
        self.qty = 0
        self.cost = 0
        self.close = None
        self.close_depth = []
        self.pending = []  # decided last batch, resolve at this batch's open
        self.used_ids = set()
        self.undelivered = []
        self.equity_points = []
        self.trades = 0
        self.fees = 0
        self.rejected = 0
        self.round_trips = []
        self._open_rt = None
        self.seq = 0
        self.fills_log = []

    def equity_now(self):
        e = self.cash
        if self.qty > 0:
            e += value(self.qty, self.close)
        return e

    def account(self):
        positions = []
        if self.qty > 0:
            positions.append({"symbol": "CBBTC", "qty": str(self.qty), "cost_quote_units": str(self.cost)})
        return {
            "cash_quote_units": str(self.cash),
            "positions": positions,
            "equity_quote_units": str(self.equity_now()),
        }

    def next_batch(self):
        """Resolve pending orders at this bar's open, close the bar, return the batch dict."""
        k = self.seq
        if k >= self.n:
            return None
        bar = self.bars[k]
        if k == 0:
            self.equity_points.append(self.cash)  # start point (warm == 0)
        pools = {}
        for p in self.pending:
            self._resolve(p, bar, pools)
        self.pending = []
        self.close = bar["c"]
        self.close_depth = bar["cd"]
        fills = self.undelivered
        self.undelivered = []
        account = self.account()
        self.equity_points.append(int(account["equity_quote_units"]))
        return {"type": "batch", "seq": k, "t": bar["t"], "bars": [bar["wire"]], "fills": fills, "account": account}

    def submit(self, orders):
        """The decision checks of one reply, in the engine's order."""
        k = self.seq
        is_last = k == self.n - 1
        equity = self.equity_now()
        buys = 0
        bought = 0
        sold = 0
        has_position = self.qty > 0
        for index, order in enumerate(orders):
            side = order["side"]
            written = int(order["notional_quote_units"]) if side == "buy" else int(order["qty"])
            amount = written
            reject = None
            if index >= MAX_ORDERS:
                reject = "max_orders_per_bar"
            elif order["client_id"] in self.used_ids:
                reject = "duplicate_client_id"
            elif order["symbol"] != "CBBTC":
                reject = "not_in_universe"
            elif written == 0:
                reject = "zero_amount"
            elif is_last:
                reject = "after_end"
            elif self.close is None:
                reject = "no_bar"
            elif side == "buy":
                cash_left = self.cash - buys
                held_value = value(self.qty, self.close)
                if amount == 0:
                    reject = "insufficient_cash" if cash_left <= 0 else "max_weight"
                elif amount < MIN_NOTIONAL:
                    reject = "below_min_notional"
                elif amount > cash_left:
                    reject = "insufficient_cash"
                elif not has_position and False:
                    reject = "max_open_positions"
                elif (held_value + bought + amount) * BPS > MAX_WEIGHT_BPS * equity:
                    reject = "max_weight"
                if reject is None:
                    buys += amount
                    bought += amount
            else:
                available = self.qty - sold
                if amount == 0 or amount > available:
                    reject = "insufficient_position"
                elif amount != available and value(amount, self.close) < MIN_NOTIONAL:
                    reject = "below_min_notional"
                if reject is None:
                    sold += amount
            self.used_ids.add(order["client_id"])
            self.pending.append(
                {
                    "order": order,
                    "amount": amount,
                    "reject": reject,
                    "close": self.close,
                    "decided_bar": self.bars[k]["t"],
                    "fill_bar": self.bars[k]["t"] + 60_000,
                }
            )
        self.seq += 1

    def _record(self, p, status, reason, price=0, qty=0, notional=0, fee=0, deliver=True):
        fill = {
            "client_id": p["order"]["client_id"],
            "symbol": p["order"]["symbol"],
            "side": p["order"]["side"],
            "status": status,
            "reason": reason,
            "decided_bar": p["decided_bar"],
            "fill_bar": p["fill_bar"],
            "price": str(price),
            "qty": str(qty),
            "notional_quote_units": str(notional),
            "fee_quote_units": str(fee),
        }
        self.fills_log.append(fill)
        if status == "rejected":
            self.rejected += 1
        if deliver:
            self.undelivered.append(fill)

    def _resolve(self, p, bar, pools):
        if p["reject"] is not None:
            return self._record(p, "rejected", p["reject"])
        order = p["order"]
        pool = pools.get("CBBTC")
        if pool is None:
            pool = Pool.of(bar["od"])
        max_slip = int(order["max_slippage_bps"])
        close = p["close"]
        if order["side"] == "buy":
            fill = buy_fill(p["amount"], bar["o"], pool, self.fee_bps)
            if fill["price"] * BPS > close * (BPS + max_slip):
                return self._record(p, "rejected", "max_slippage")
            if fill["qty"] == 0:
                return self._record(p, "rejected", "zero_amount")
            pools["CBBTC"] = fill["after"]
            self.cash -= p["amount"]
            if self.qty == 0:
                self._open_rt = {"opened_bar": bar["t"], "pnl": 0}
            self.qty += fill["qty"]
            self.cost += p["amount"]
            self.trades += 1
            self.fees += fill["fee"]
            if self._open_rt is not None:
                self._open_rt["pnl"] -= p["amount"]
            return self._record(p, "filled", None, fill["price"], fill["qty"], p["amount"], fill["fee"])
        fill = sell_fill(p["amount"], bar["o"], pool, self.fee_bps)
        if fill["price"] * BPS < close * (BPS - max_slip):
            return self._record(p, "rejected", "max_slippage")
        pools["CBBTC"] = fill["after"]
        self.cash += fill["proceeds"]
        self.cost = self.cost * (self.qty - p["amount"]) // self.qty
        self.qty -= p["amount"]
        self.trades += 1
        self.fees += fill["fee"]
        if self._open_rt is not None:
            self._open_rt["pnl"] += fill["proceeds"]
            if self.qty == 0:
                self.round_trips.append(
                    {"opened_bar": self._open_rt["opened_bar"], "closed_bar": bar["t"], "pnl": self._open_rt["pnl"]}
                )
                self._open_rt = None
        return self._record(p, "filled", None, fill["price"], p["amount"], fill["proceeds"], fill["fee"])

    def finish(self):
        """Reject the last batch's orders, flatten, and score the run."""
        for p in self.pending:
            assert p["reject"] is not None, "last-batch order passed decision checks"
            self._record(p, "rejected", p["reject"], deliver=False)
        self.pending = []
        if self.qty > 0:
            fill = sell_fill(self.qty, self.close, Pool.of(self.close_depth), self.fee_bps)
            self.cash += fill["proceeds"]
            self.fees += fill["fee"]
            if self._open_rt is not None:
                self._open_rt["pnl"] += fill["proceeds"]
                self.round_trips.append(
                    {"opened_bar": self._open_rt["opened_bar"], "closed_bar": self.bars[-1]["t"], "pnl": self._open_rt["pnl"]}
                )
                self._open_rt = None
            self.qty = 0
            self.cost = 0
        self.equity_points.append(self.cash)
        peak = 0
        max_dd = 0.0
        for e in self.equity_points:
            if e > peak:
                peak = e
            if peak > 0:
                dd = (peak - e) / peak
                if dd > max_dd:
                    max_dd = dd
        return {
            "net_pnl": self.cash - START_CASH,
            "final_cash": self.cash,
            "trades": self.trades,
            "fees": self.fees,
            "rejected": self.rejected,
            "max_drawdown": max_dd,
            "round_trips": self.round_trips,
        }
