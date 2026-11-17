"""Drive a bot process over the bars-replay wire protocol through tools/engine.py.

Usage:
  python3 tools/drive.py --bars <bars.ndjson.gz> -- <bot command...>
Prints the run summary as JSON. The bot's replies are echoed to
tools/last_replies.jsonl when --save-replies is given (determinism checks).
"""

import argparse
import json
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E


def run_bot(bars, cmd, save_replies=None):
    eng = E.Engine(bars)
    hello = E.make_hello(bars)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    replies_out = open(save_replies, "w") if save_replies else None
    try:
        proc.stdin.write(json.dumps(hello) + "\n")
        proc.stdin.flush()
        while True:
            batch = eng.next_batch()
            if batch is None:
                break
            proc.stdin.write(json.dumps(batch) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"bot died at seq {batch['seq']}")
            reply = json.loads(line)
            assert reply.get("type") == "reply" and reply.get("seq") == batch["seq"], f"bad reply at {batch['seq']}: {line[:200]}"
            if replies_out:
                replies_out.write(line if line.endswith("\n") else line + "\n")
            eng.submit(reply.get("orders", []))
        result = eng.finish()
        done = {"type": "done", "seq": eng.seq, "reason": "end_of_data", "account": eng.account()}
        proc.stdin.write(json.dumps(done) + "\n")
        proc.stdin.flush()
        proc.stdin.close()
        proc.wait(timeout=30)
        result["exit_code"] = proc.returncode
        return result
    finally:
        if replies_out:
            replies_out.close()
        if proc.poll() is None:
            proc.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--save-replies")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    bars = E.load_bars(args.bars)
    result = run_bot(bars, cmd, args.save_replies)
    result["net_pnl_usdc"] = result["net_pnl"] / 1e6
    result["fees_usdc"] = result["fees"] / 1e6
    for rt in result["round_trips"]:
        rt["pnl_usdc"] = rt["pnl"] / 1e6
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
