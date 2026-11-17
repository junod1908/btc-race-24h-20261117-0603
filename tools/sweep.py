"""Parameter sweeps and robustness runs for main.ts against tools/engine.py.

Variants are made by rewriting numbers in the P block of a copy of main.ts;
each runs through the wire driver on the dev window (or a slice / mirror of
it). Usage: python3 tools/sweep.py [grid|subs|mirror] [overrides-json]
"""
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E
import drive as D

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BARS_PATH = "/home/hatch/workspace/makefun/btc-race-24h-20261117-0603-kit/dataset/bars/CBBTC.ndjson.gz"
OLD_PATH = "/home/hatch/workspace/makefun/btc-race-24h-kit/btc-race-24h-20261115-0828-kit/dataset/bars/CBBTC.ndjson.gz"
BASE = open(os.path.join(REPO, "main.ts")).read()


def variant(overrides):
    src = BASE
    for key, val in overrides.items():
        pat = re.compile(r"(\b" + key + r": )([0-9.]+)(,)")
        src, n = pat.subn(lambda m: m.group(1) + repr(val) + m.group(3), src, count=1)
        assert n == 1, key
    return src


def run_src(src, bars):
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "main.ts"), "w") as f:
            f.write(src)
        os.symlink(os.path.join(REPO, "vendor"), os.path.join(td, "vendor"))
        return D.run_bot(bars, ["node", os.path.join(td, "main.ts")])


def brief(r):
    return "PnL %+8.3f | DD %.4f | trades %d | RTs %s" % (
        r["net_pnl"] / 1e6,
        r["max_drawdown"],
        r["trades"],
        [round(rt["pnl"] / 1e6, 2) for rt in r["round_trips"]],
    )


def mirror_bars(bars):
    k = int(bars[0]["wire"]["c"]) * int(bars[-1]["wire"]["c"])

    def mp(x):
        return str(max(1, round(k / int(x))))

    out = []
    for b in bars:
        w = b["wire"]
        out.append(
            {
                "t": b["t"],
                "o": int(mp(w["c"])),
                "h": int(mp(w["l"])),
                "l": int(mp(w["h"])),
                "c": int(mp(w["o"])),
                "v": None,
                "od": b["od"],
                "cd": b["cd"],
                "wire": {
                    "symbol": "CBBTC",
                    "t": w["t"],
                    "o": mp(w["c"]),
                    "h": mp(w["l"]),
                    "l": mp(w["h"]),
                    "c": mp(w["o"]),
                    "v": None,
                    "o_depth": w["o_depth"],
                    "c_depth": w["c_depth"],
                },
            }
        )
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "grid"
    bars = E.load_bars(BARS_PATH)
    old = E.load_bars(OLD_PATH)
    ov = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    src = variant(ov) if ov else BASE
    if mode == "grid":
        grid = {
            "pullTakeProfit": [0.016, 0.02, 0.025],
            "pullTrailDist": [0.007, 0.01, 0.013],
            "pullStopLoss": [0.006, 0.008, 0.011],
        }
        keys = list(grid)
        rows = []
        for combo in itertools.product(*(grid[k] for k in keys)):
            o = dict(zip(keys, combo))
            r = run_src(variant(o), bars)
            ro = run_src(variant(o), old)
            rows.append((r["net_pnl"], o, r, ro))
            print(o, "-> NEW", brief(r), "| OLD", brief(ro), flush=True)
        rows.sort(key=lambda x: -x[0])
        print("\nTOP 5 (by NEW window):")
        for _, o, r, ro in rows[:5]:
            print(o, "-> NEW", brief(r), "| OLD", brief(ro))
    elif mode == "subs":
        for name, bs in (("NEW", bars), ("OLD", old)):
            print(name, "full    :", brief(run_src(src, bs)))
            print(name, "half1   :", brief(run_src(src, bs[:720])))
            print(name, "half2   :", brief(run_src(src, bs[720:])))
            print(name, "last 480:", brief(run_src(src, bs[960:])))
            print(name, "mirror  :", brief(run_src(src, mirror_bars(bs))))
    elif mode == "one":
        print("NEW:", brief(run_src(src, bars)))
        print("OLD:", brief(run_src(src, old)))


if __name__ == "__main__":
    main()
